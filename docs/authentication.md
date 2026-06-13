# Authentication

Cove supports **local accounts** (username + password) and **OIDC/SSO**
(e.g. Authentik), which can run together or SSO-only.

## First-run setup

On a brand-new install with no users, visiting Cove shows the **first-run setup**
screen. It creates a single account with **admin** rights:

- Username — 1–64 characters of `[a-zA-Z0-9._-]` (not `.` or `..`).
- Password — at least **8 characters**.

Setup is only available while zero users exist; afterward the endpoint is closed.
It is also disabled entirely in [OIDC-only mode](#oidc-only-mode).

## Local accounts

- **Login** is username + password. Passwords are hashed with **bcrypt** (per-hash random salt).
- **Rate limiting:** the application allows `COVE_LOGIN_RATE_LIMIT` attempts (default **10**) per `COVE_LOGIN_RATE_WINDOW_SECONDS` (default **60 s**) per client IP; Traefik adds an independent edge limit (~5/min, burst 10) on the login route. Excess attempts return `429`.
- Login timing is constant whether or not the username exists, to avoid leaking which accounts are registered.

Admins create and manage additional accounts under **Admin → Users** — see
[Administration → User management](administration.md#user-management).

## Sessions & tokens

Cove issues signed (HS256) JSON Web Tokens delivered as **httpOnly cookies**:

| Cookie | Contains | Default lifetime | Scope |
|---|---|---|---|
| `cove_session` | access token | 30 min (`COVE_ACCESS_TOKEN_MINUTES`) | path `/` |
| `cove_refresh` | refresh token | 7 days (`COVE_REFRESH_TOKEN_DAYS`) | path `/api/auth` |
| `cove_stream` | per-workspace stream token (subdomain mode) | 480 min (`COVE_STREAM_TOKEN_MINUTES`) | the workspace origin only |

- Cookies are **host-only** (no `Domain`) so the powerful session cookie is never sent to workspace origins. They carry `Secure` when `COVE_COOKIE_SECURE=true` — required over HTTPS, and the reason the cookie silently fails if you serve HTTPS with it set `false`.
- The API also accepts an `Authorization: Bearer <token>` header as an alternative to the cookie.
- **Refresh:** the SPA silently rotates the session via the refresh cookie. **Logout** revokes all of a user's outstanding tokens (it stamps a `tokens_valid_from` marker) and clears the cookies. Changing your password does the same, ending other sessions.

## OIDC / SSO

OIDC is **enabled only when all three** of `COVE_OIDC_ISSUER`,
`COVE_OIDC_CLIENT_ID`, and `COVE_OIDC_CLIENT_SECRET` are set. With OIDC on, a
"Sign in with `<provider>`" button appears (label = `COVE_OIDC_PROVIDER_NAME`);
local login still works as a fallback unless [OIDC-only](#oidc-only-mode) is on.

```ini
# .env
COVE_OIDC_ISSUER=https://auth.example.com/application/o/cove/
COVE_OIDC_CLIENT_ID=...
COVE_OIDC_CLIENT_SECRET=...
COVE_OIDC_ADMIN_GROUP=cove-admins        # group claim that grants admin
COVE_OIDC_PROVIDER_NAME=Authentik        # button label
COVE_OIDC_SCOPES=openid email profile groups
```

In your IdP, set the redirect URI to:

```
https://<your-domain>/api/auth/oidc/callback
```

**Provisioning & admin mapping**

- Users are matched by their OIDC subject. A new user's username is derived from `preferred_username`, then the email local-part, then the subject — sanitized to the allowed charset, with a numeric suffix on collision.
- If `COVE_OIDC_ADMIN_GROUP` is set, admin status is taken from the token's `groups` claim on **every** login (so removing someone from the group revokes their admin on next login). If it's unset, existing admin status is left untouched.

**Security hardening (for reference):** the ID token's signature is verified
against the issuer's JWKS with the algorithm pinned to an asymmetric allowlist
(RS/ES/PS), so `HS256`/`none` are never honored; audience, issuer, and a one-time
`nonce` are all checked; the `state` parameter is HMAC-signed.

## OIDC-only mode

Set `COVE_OIDC_ONLY=true` to remove local login and local account creation
entirely — the SPA goes straight to the IdP, and `setup`/`login`/admin
user-creation are blocked.

This mode is **gated on OIDC being fully configured** (`oidc_only_active = oidc_only AND oidc_enabled`). That's a safety net: if the OIDC config is broken or
incomplete, OIDC is considered disabled, so OIDC-only does **not** activate and
the local login form remains available. You cannot lock everyone out with a
half-finished config.

**To recover or disable SSO-only:** set `COVE_OIDC_ONLY=false` (or fix/remove the
OIDC settings) on the server and restart. The local login form returns.

## Per-user credentials

Each user manages their own secrets under **Preferences** — password (local
accounts only), SSH key, Tailscale, and Gluetun. See
[User guide → Preferences](user-guide.md#preferences).
</content>
