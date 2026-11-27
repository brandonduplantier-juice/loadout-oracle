# Release and versioning policy

Use existing semantic version and platform build-number conventions. For new versioned releases prefer `vMAJOR.MINOR.PATCH` with prerelease identifiers when appropriate. Validate migrations, tests and platform builds as applicable. Keep secrets and user data out of artifacts.

## Release checklist

- Identify the exact commit and review the intended changes.
- Preserve existing version files, changelogs and release automation; update them only when applicable.
- Run the project's relevant checks and document failures or unavailable checks explicitly.
- Record scope, verification evidence, known limitations and rollback or recovery instructions.
- Do not publish a release, deploy, or claim successful validation solely because documentation changed.

Existing tags and historical versions remain authoritative. This policy does not itself create a release or change a version.
