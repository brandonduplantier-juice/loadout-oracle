"""Lightweight anomaly alerting for Loadout Oracle.

Emails the maintainer when a real user request hits a problem: an unhandled
exception, a malformed generated build (empty slot, off-element ability, an
ability-locked exotic forced to the wrong ability), or broken pool data on
deploy. Designed to be safe and quiet:

  * No-ops when SMTP env vars are unset (local dev never emails).
  * Throttled: one email per unique problem signature per cooldown window,
    with a hard daily cap, so a recurring fault cannot flood the inbox.
  * Fail-safe: every path is wrapped so alerting can never break a request.

Config via environment (set these on Render):
  LO_ALERT_GMAIL_USER     gmail address that sends (e.g. juice6121@gmail.com)
  LO_ALERT_GMAIL_APP_PW   gmail app password (NOT the account password)
  LO_ALERT_TO             where alerts go (defaults to LO_ALERT_GMAIL_USER)

State is in-memory; it resets on worker restart, which is fine for a single
worker and matches the app's existing rate-limiter approach.
"""
import os, smtplib, ssl, time, traceback, hashlib
from email.message import EmailMessage

_USER = os.environ.get("LO_ALERT_GMAIL_USER")
_PW = os.environ.get("LO_ALERT_GMAIL_APP_PW")
_TO = os.environ.get("LO_ALERT_TO") or _USER
_ENABLED = bool(_USER and _PW and _TO)

COOLDOWN_SEC = 6 * 3600   # per-signature: at most one email every 6 hours
DAILY_MAX = 30            # hard cap across all alerts per day

_last_sent = {}           # signature -> last send epoch
_day = {"date": None, "count": 0}


def _sig(category, key):
    h = hashlib.sha1(("%s|%s" % (category, key)).encode("utf-8")).hexdigest()[:12]
    return h


def _allowed(signature):
    now = time.time()
    today = time.strftime("%Y-%m-%d")
    if _day["date"] != today:
        _day["date"], _day["count"] = today, 0
    if _day["count"] >= DAILY_MAX:
        return False
    last = _last_sent.get(signature, 0)
    if now - last < COOLDOWN_SEC:
        return False
    _last_sent[signature] = now
    _day["count"] += 1
    return True


def _send(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _USER
    msg["To"] = _TO
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
        s.starttls(context=ctx)
        s.login(_USER, _PW)
        s.send_message(msg)


def alert(category, detail, dedupe_key=None):
    """Email one alert, throttled. Never raises."""
    try:
        if not _ENABLED:
            print("[alert:%s] %s" % (category, str(detail)[:300]))
            return
        key = dedupe_key if dedupe_key is not None else str(detail)[:200]
        sig = _sig(category, key)
        if not _allowed(sig):
            return
        subject = "[Loadout Oracle] %s" % category
        body = "%s\n\nTime: %s UTC\nSignature: %s\n" % (
            str(detail), time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), sig)
        _send(subject, body)
    except Exception as e:  # alerting must never break the caller
        print("[alert-failed]", repr(e))


def send_report(subject, body, png_bytes=None):
    """Send a user-submitted issue report with an optional PNG attachment.
    Reuses the alert Gmail credentials. Returns True on success, False otherwise.
    Counts against the same daily cap as a light abuse guard."""
    try:
        if not _ENABLED:
            print("[report] %s :: %s" % (subject, str(body)[:300]))
            return False
        today = time.strftime("%Y-%m-%d")
        if _day["date"] != today:
            _day["date"], _day["count"] = today, 0
        if _day["count"] >= DAILY_MAX:
            return False
        _day["count"] += 1
        msg = EmailMessage()
        msg["Subject"] = "[Loadout Oracle] %s" % subject
        msg["From"] = _USER
        msg["To"] = _TO
        msg.set_content(str(body))
        if png_bytes:
            msg.add_attachment(png_bytes, maintype="image", subtype="png", filename="build.png")
        ctx = ssl.create_default_context()
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(_USER, _PW)
            s.send_message(msg)
        return True
    except Exception as e:
        print("[report-failed]", repr(e))
        return False


# ---- build-level invariant checks (run per real request) -------------------

_REQUIRED_SLOTS = ["Super", "Grenade", "Melee", "Class Ability", "Exotic Armor"]


def check_build(answers, gen):
    """Inspect a generated build for inconsistencies; email a digest if any.
    Returns the list of issue strings (also useful for logging/tests)."""
    issues = []
    try:
        import app  # lazy to avoid circular import at module load
        build = (gen or {}).get("build") or {}
        cls = answers.get("cls"); elem = answers.get("element")

        for slot in _REQUIRED_SLOTS:
            if not build.get(slot):
                issues.append("empty slot: %s" % slot)

        # off-element ability in an ability slot
        for slot in ("Grenade", "Melee", "Super"):
            legal = {x["name"] for x in app.gated(slot, cls, elem)} if cls and elem else None
            for e in build.get(slot, []):
                nm = e["item"]["name"]
                if legal is not None and nm not in legal:
                    issues.append("off-element %s: %s on %s %s" % (slot, nm, elem, cls))

        # ability-locked exotic forced to the wrong ability
        ea = getattr(app, "EXOTIC_ABILITY", {})
        for e in build.get("Exotic Armor", []) + build.get("Exotic Weapon", []):
            nm = e["item"]["name"]
            if nm in ea:
                for slot, want in ea[nm].items():
                    have = [x["item"]["name"] for x in build.get(slot, [])]
                    if have and want not in have:
                        issues.append("exotic lock mismatch: %s wants %s:%s, build has %s"
                                      % (nm, slot, want, have))

        # tagw data drift on any used item
        for slot, entries in build.items():
            for e in entries:
                tw = e["item"].get("tagw") or {}
                if tw and abs(sum(tw.values()) - 1.0) > 0.05:
                    issues.append("tagw sum off: %s = %.3f" % (e["item"]["name"], sum(tw.values())))

        if issues:
            ctx = "%s %s | goal=%s/%s engine=%s" % (
                cls, elem, answers.get("goal"), answers.get("goal2"), answers.get("engine"))
            alert("build inconsistency",
                  "Context: %s\n\n- %s" % (ctx, "\n- ".join(issues)),
                  dedupe_key="build|" + "|".join(sorted(set(issues))))
    except Exception as e:
        alert("check_build crashed", traceback.format_exc(), dedupe_key="check_build_crash")
    return issues


# ---- Flask + startup integration -------------------------------------------

def install(flask_app):
    """Register a 500 handler that emails the traceback (throttled)."""
    @flask_app.errorhandler(500)
    def _on_500(e):
        try:
            tb = traceback.format_exc()
            # dedupe on the exception type + last frame, not the full trace
            lines = [l for l in tb.strip().splitlines() if l]
            key = (lines[-1] if lines else "500")[:160]
            alert("unhandled 500", tb, dedupe_key=key)
        except Exception:
            pass
        # let Flask render its normal error response
        return ("Internal Server Error", 500)
    return flask_app


def startup_data_check(pool, exotic_ability):
    """Run once at import: email if pool data is structurally broken on deploy.
    Throttled per distinct failure so a bad deploy emails at most every 6h."""
    try:
        VOCAB = {"Damage", "Add Clear", "Ability Regen", "Survivability", "Utility",
                 "Crowd Control", "Team Buff", "Mobility", "Healing"}
        bad = []
        names = {it["name"] for it in pool if isinstance(it, dict)}
        for it in pool:
            if not isinstance(it, dict):
                continue
            tw = it.get("tagw") or {}
            if tw and abs(sum(tw.values()) - 1.0) > 0.05:
                bad.append("tagw sum %.3f: %s" % (sum(tw.values()), it.get("name")))
            if set(tw) - VOCAB:
                bad.append("bad tag %s: %s" % (list(set(tw) - VOCAB), it.get("name")))
        for exo in exotic_ability:
            if exo not in names:
                bad.append("ability-lock for missing exotic: %s" % exo)
        if bad:
            alert("data integrity (deploy)",
                  "pool.json / exotic_abilities.json problems:\n\n- " + "\n- ".join(bad[:40]),
                  dedupe_key="startup|" + "|".join(sorted(bad))[:200])
        return bad
    except Exception:
        alert("startup_data_check crashed", traceback.format_exc(), dedupe_key="startup_crash")
        return []
