# Staff access and private authentication data

Staff use the same configured provider or verified email-code sign-in as authors.
Local password authentication is disabled, including Django admin. Every admin
view requires an active staff account, an enrolled TOTP authenticator, and an
actual TOTP or recovery-code challenge completed in the same user's session
within the last 15 minutes. An enrolled device alone grants no access. An expired
administration form is rejected without saving or replaying its changes.

## Initial operator

1. Configure and test one sign-in provider. The operator signs in normally to
   create their account; a private email is not needed for ORCID or GitHub.
2. Independently confirm the intended operator's provider identity. In the
   trusted server console, grant the existing account its role, for example:

   ```sh
   python server/manage.py grant_staff_role --provider orcid --uid 0000-0002-1825-0097 --superuser
   ```

   Replace the example ORCID with the intended operator's authenticated ORCID.
   This command does not create an account or bypass MFA. It invalidates existing
   sessions and disables any local password. Reserve `--superuser` for the initial
   operator; give later reviewers only the permissions needed for their work.
3. The operator signs in again, visits `/admin/`, and sets up their authenticator
   within five minutes of that sign-in. The owner scans the QR code themselves.
   Confirm a code, then complete the separate MFA challenge before entering admin.
4. Save the one-time recovery codes offline. They are shown once. Test one in a
   separate sign-in and keep the remaining codes in a secure location.

Staff cannot remove their last authenticator. MFA settings and recovery-code
regeneration require a recent actual MFA challenge. A remembered browser cannot
skip it. Lost-device recovery needs a remaining recovery code, or a trusted
operator who independently verifies identity, revokes the affected account's
sessions, removes its authenticators, and arranges fresh sign-in and enrollment.
There is no public password-reset or administrator-screen MFA-reset shortcut.
Do not reset MFA based only on an email or chat claiming to be the account owner.

Authors may optionally enable an authenticator from their account page. The same
fresh-sign-in enrollment and recent-MFA management checks apply to every account.
Authors may remove their optional authenticator after completing an MFA challenge;
staff must retain a primary factor.

Changing connected login methods requires an existing account sign-in within
five minutes. This is checked before connection starts and again at its callback;
authenticating the new provider being linked does not refresh the old account's
proof. Every account with MFA enabled, and all staff, also needs recent MFA.
Stale users get a clear instruction to sign out
and sign back in. Viewing the account page and ordinary submission work remain
available. GitHub repository verification does not link a sign-in method and
continues through its separate nonce-checked flow.

For access, export, correction and closure requests, follow the separate
[privacy-request procedure](PRIVACY_REQUESTS.md). An MFA recovery request does not
authorize disclosure or deletion of account data.

## Encryption and retained data

The service encrypts TOTP secrets, recovery-code seeds, and entire server-side
session payloads with authenticated Fernet encryption. This includes temporary
enrollment secrets, email verification codes, and OAuth state. It uses HKDF-SHA256
to derive separate MFA and session keys from the existing random
`DJANGO_SECRET_KEY`, with different purpose contexts. No additional production
secret is required. The master key must remain in the hosting secret store,
separate from database backups. A development fallback is allowed only in debug
mode; production requires the existing strong-key validation.

This protects the contents against disclosure of the database alone. It does
not protect against compromise of both the running application and its host
secrets. Database session IDs, expiration times, authenticator type, and usage
timestamps remain available for normal operation; Fernet also exposes its token
creation timestamp. Recovery codes are represented by an encrypted seed and a
used-code mask, not merely password hashes. No phone number is collected.
Session IDs are bearer credentials, so a stolen active database session ID can
still be replayed until expiry or revocation. Database access and backups must
remain private; encrypting session contents does not remove that requirement.

The Django session table and expiration cleanup remain unchanged. A custom
session backend encrypts and authenticates its payload. Maintenance that needs
to inspect a session must use `accounts.sessions.SessionStore().decode(...)`;
Django's `Session.get_decoded()` is hardcoded to its original decoder. Legacy
signed-but-readable sessions are deliberately rejected. Deploy this before live
accounts exist; an existing installation would need a planned session logout and
MFA-data migration rather than silently accepting plaintext secrets.

## Key rotation and recovery

Back up the current master key securely and test restoration with a database
backup. Losing the key makes existing encrypted MFA data unreadable. The optional
`DJANGO_SECRET_KEY_FALLBACKS` hosting setting is a JSON array of previous strong
keys. It permits decryption during a planned rotation; new writes use the current
key. Never place keys in the repository or command output.

Rotation is not complete merely because the current key has changed. Existing
MFA records remain encrypted under their old key until explicitly reencrypted or
reenrolled. Sessions are reencrypted when written, not on every read. Retain an
old fallback only until its data has been migrated or invalidated, and ensure the
necessary key is retained securely for any older backups. There is deliberately
no automatic bulk rewrite of live authentication data. If a master key was
exposed, do not retain it as a fallback: revoke sessions and arrange trusted MFA
reenrollment. A separate independently managed master key or external key manager
would reduce the impact of a Django signing-key compromise; the current design
uses purpose separation under one protected host secret.

TOTP and recovery codes can be phished. WebAuthn security keys with user
verification are the next hardening option once the final domain is chosen.
Credentials enrolled on an `onrender.com` hostname cannot be used automatically
on `math-stoa.org`. Require two registered keys or a deliberate recovery process
before adopting a key-only policy.

Implementation references: [allauth admin integration](https://docs.allauth.org/en/latest/common/admin.html),
[allauth MFA encryption hook](https://docs.allauth.org/en/latest/mfa/adapter.html),
[Fernet](https://cryptography.io/en/50.0.2/fernet/), and
[HKDF](https://cryptography.io/en/50.0.2/hazmat/primitives/key-derivation-functions/#hkdf).
