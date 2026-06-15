"""
One-time Bungie OAuth setup for Loadout Oracle's single-account DIM sharing.

Run this ONCE on your own machine. It walks you through authorizing your Bungie
account, then prints the two values you paste into Render (BUNGIE_REFRESH_TOKEN
and BUNGIE_MEMBERSHIP_ID). It also tests the full chain end to end (Bungie token
-> DIM auth token -> create a real dim.gg share) so you know it works before you
deploy.

Requirements: Python 3.8+. No third-party packages (uses the standard library).

Before running, your app at https://www.bungie.net/en/Application must be:
  - OAuth Client Type: Confidential   (this gives you a client SECRET)
  - Has a Redirect URL set            (any https URL you control is fine, e.g.
                                        https://loadout-oracle.onrender.com/oauth
                                        the page does not need to exist; you only
                                        copy the code out of the address bar)
  - Has read scopes enabled           (the default "Read your Destiny ..." scopes)

You will be asked for: Client ID, Client Secret, Bungie API Key, DIM API Key,
and the Redirect URL exactly as registered.
"""

import base64
import json
import sys
import uuid
from urllib import request as urlreq, error as urlerr, parse as urlparse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
BUNGIE_TOKEN_URL = "https://www.bungie.net/Platform/App/OAuth/Token/"
BUNGIE_AUTH_URL = "https://www.bungie.net/en/OAuth/Authorize"
DIM_AUTH_URL = "https://api.destinyitemmanager.com/auth/token"
DIM_SHARE_URL = "https://api.destinyitemmanager.com/loadout_share"


def ask(label, secret=False):
    val = input(label).strip()
    while not val:
        val = input(label).strip()
    return val


def post_form(url, form, headers):
    data = urlparse.urlencode(form).encode("utf-8")
    req = urlreq.Request(url, data=data, method="POST", headers=headers)
    with urlreq.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(url, obj, headers):
    data = json.dumps(obj).encode("utf-8")
    h = dict(headers)
    h["Content-Type"] = "application/json"
    req = urlreq.Request(url, data=data, method="POST", headers=h)
    with urlreq.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(url, headers):
    req = urlreq.Request(url, method="GET", headers=headers)
    with urlreq.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def show_http_error(prefix, e):
    body = ""
    try:
        body = e.read().decode("utf-8")[:600]
    except Exception:
        pass
    print("\n[FAIL] " + prefix + " HTTP " + str(e.code))
    if body:
        print(body)


def main():
    print(__doc__)
    print("=" * 70)
    client_id = ask("Bungie Client ID: ")
    client_secret = ask("Bungie Client Secret: ")
    bungie_api_key = ask("Bungie API Key: ")
    dim_api_key = ask("DIM API Key: ")
    redirect_url = ask("Redirect URL exactly as registered: ")

    state = uuid.uuid4().hex
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "state": state,
        "redirect_uri": redirect_url,
    }
    auth_link = BUNGIE_AUTH_URL + "?" + urlparse.urlencode(auth_params)

    print("\n" + "=" * 70)
    print("STEP 1. Open this URL in your browser and click Authorize:\n")
    print(auth_link)
    print("\nAfter you approve, your browser will try to load the Redirect URL")
    print("with ?code=... in the address bar. The page may show an error; that")
    print("is fine. Copy the value of 'code' from the address bar.")
    print("=" * 70)
    code = ask("\nPaste the code value here: ")

    basic = base64.b64encode(
        (client_id + ":" + client_secret).encode("utf-8")).decode("ascii")
    token_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + basic,
        "X-API-Key": bungie_api_key,
        "User-Agent": UA,
    }

    print("\nSTEP 2. Exchanging the code for tokens ...")
    try:
        tok = post_form(BUNGIE_TOKEN_URL, {
            "grant_type": "authorization_code",
            "code": code,
        }, token_headers)
    except urlerr.HTTPError as e:
        show_http_error("Bungie token exchange", e)
        print("\nCommon causes: code already used (get a fresh one), redirect URL")
        print("mismatch, or client is not Confidential.")
        sys.exit(1)

    access_token = tok.get("access_token", "")
    refresh_token = tok.get("refresh_token", "")
    membership_id = tok.get("membership_id", "")
    if not (access_token and refresh_token and membership_id):
        print("\n[FAIL] Token response missing fields:")
        print(json.dumps(tok, indent=2)[:800])
        sys.exit(1)
    print("[ok] Got access token, refresh token, membership id.")
    print("     refresh token valid for ~" +
          str(int(tok.get("refresh_expires_in", 7776000)) // 86400) + " days.")

    print("\nSTEP 3. Testing the DIM auth exchange ...")
    dim_headers = {"X-API-Key": dim_api_key, "Accept": "application/json",
                   "User-Agent": UA}
    try:
        dim_tok = post_json(DIM_AUTH_URL, {
            "bungieAccessToken": access_token,
            "membershipId": membership_id,
        }, dim_headers)
    except urlerr.HTTPError as e:
        show_http_error("DIM auth token", e)
        sys.exit(1)
    dim_bearer = dim_tok.get("accessToken", "")
    if not dim_bearer:
        print("[FAIL] DIM auth response missing accessToken:")
        print(json.dumps(dim_tok, indent=2)[:800])
        sys.exit(1)
    print("[ok] DIM bearer token obtained.")

    print("\nSTEP 4. Looking up your Destiny platform membership id ...")
    try:
        memb = get_json(
            "https://www.bungie.net/Platform/User/GetMembershipsForCurrentUser/",
            {"X-API-Key": bungie_api_key, "Authorization": "Bearer " + access_token,
             "User-Agent": UA, "Accept": "application/json"})
    except urlerr.HTTPError as e:
        show_http_error("GetMembershipsForCurrentUser", e)
        sys.exit(1)
    mresp = memb.get("Response", {})
    platform_mid = mresp.get("primaryMembershipId")
    if not platform_mid:
        dm = mresp.get("destinyMemberships") or []
        if dm:
            platform_mid = dm[0].get("membershipId")
    if not platform_mid:
        print("[FAIL] No Destiny membership found on this account.")
        print(json.dumps(mresp, indent=2)[:800])
        sys.exit(1)
    print("[ok] Destiny membership id resolved.")

    print("\nSTEP 5. Creating a real test share to confirm the full chain ...")
    test_loadout = {
        "id": str(uuid.uuid4()),
        "name": "Loadout Oracle auth test",
        "classType": 2,
        "equipped": [],
        "unequipped": [],
        "parameters": {"mods": [], "statConstraints": []},
    }
    share_headers = {
        "X-API-Key": dim_api_key,
        "Authorization": "Bearer " + dim_bearer,
        "Accept": "application/json",
        "User-Agent": UA,
    }
    share_url = None
    try:
        res = post_json(DIM_SHARE_URL,
                        {"loadout": test_loadout, "platformMembershipId": platform_mid},
                        share_headers)
        share_url = res.get("shareUrl") or res.get("url")
        if not share_url:
            sid = res.get("shareId") or res.get("id")
            share_url = "https://dim.gg/" + str(sid) if sid else None
    except urlerr.HTTPError as e:
        show_http_error("loadout_share", e)
    if not share_url:
        print("[FAIL] Could not create a test share. See errors above.")
        sys.exit(1)
    print("[ok] Test share created: " + share_url)

    print("\n" + "=" * 70)
    print("SUCCESS. The full chain works. Set these on Render (then redeploy):")
    print("=" * 70)
    print("BUNGIE_OAUTH_CLIENT_ID     = " + client_id)
    print("BUNGIE_OAUTH_CLIENT_SECRET = " + client_secret)
    print("BUNGIE_API_KEY             = " + bungie_api_key)
    print("BUNGIE_REFRESH_TOKEN       = " + refresh_token)
    print("BUNGIE_MEMBERSHIP_ID       = " + membership_id)
    print("DIM_API_KEY                = (already set)")
    print("=" * 70)
    print("Note: the refresh token expires ~90 days from now. When sharing stops")
    print("working, run this script again and update BUNGIE_REFRESH_TOKEN.")


if __name__ == "__main__":
    main()
