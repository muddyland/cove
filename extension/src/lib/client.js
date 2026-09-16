// Authenticated transport to a Cove server.
//
// Getting a token without a password matters here, because OIDC accounts have
// no password to give: they have no password_hash, so POST /api/auth/login
// answers 401 for them even where local login is enabled, and 403 in oidc_only
// mode. The extension therefore reads the credential the browser already holds.
//
// Cove's session cookie IS the access JWT — backend/server/routers/auth.py sets
// `cove_session` to create_access_token(...), and the OIDC callback goes through
// exactly the same _set_auth_cookies(). So reading that cookie and presenting it
// as `Authorization: Bearer` authenticates any signed-in user, local or SSO,
// with no password and no dependence on whether a browser attaches a
// SameSite=Lax cookie to an extension-initiated cross-site request.
//
// Order of preference:
//   1. the live session cookie (always current, works for every account type)
//   2. a token we stored earlier
//   3. POST /api/auth/refresh, which authenticates from the refresh cookie
// and only then do we ask the user to sign in — in a real browser tab, where
// their identity provider works.

import { CoveError, errorDetail, DEFAULT_SESSION_COOKIE } from './cove.js';

const api = globalThis.browser ?? globalThis.chrome;

export async function getSettings() {
  const d = await api.storage.local.get(['baseUrl', 'accessToken', 'defaults', 'sessionCookieName']);
  return {
    baseUrl: d.baseUrl || '',
    accessToken: d.accessToken || '',
    defaults: d.defaults || {},
    sessionCookieName: d.sessionCookieName || DEFAULT_SESSION_COOKIE,
  };
}

export async function setSettings(patch) {
  await api.storage.local.set(patch);
}

/**
 * Read Cove's session cookie. Extensions can read httponly cookies through the
 * cookies API — that is the whole point of it — so this works where a `fetch`
 * with credentials may not.
 */
export async function readSessionCookie(baseUrl, name) {
  if (!api.cookies) return '';
  try {
    const c = await api.cookies.get({ url: baseUrl, name });
    return (c && c.value) || '';
  } catch {
    return '';
  }
}

async function rawRequest(baseUrl, path, { method = 'GET', body, token } = {}) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${baseUrl}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

/** Best available credential, freshest first. */
async function resolveToken({ baseUrl, accessToken, sessionCookieName }) {
  const live = await readSessionCookie(baseUrl, sessionCookieName);
  return live || accessToken || '';
}

/**
 * One authenticated call. On 401 it re-resolves once — the SPA may have rotated
 * the cookie in another tab — then tries a refresh, then gives up. Bounded, so
 * an expired credential can never loop.
 */
export async function request(path, opts = {}) {
  const settings = await getSettings();
  const { baseUrl } = settings;
  if (!baseUrl) throw new CoveError('No Cove server configured — open the extension options.', 0);

  const send = async (token) => {
    try {
      return await rawRequest(baseUrl, path, { ...opts, token });
    } catch {
      throw new CoveError(`Cannot reach ${baseUrl} — is it up, and is this browser allowed to see it?`, 0);
    }
  };

  let res = await send(await resolveToken(settings));

  if (res.status === 401) {
    const refreshed = await tryRefresh(baseUrl, settings.sessionCookieName);
    if (refreshed) res = await send(refreshed);
  }

  if (res.status === 401) throw new CoveError('Not signed in to Cove.', 401);
  if (!res.ok) throw new CoveError(await errorDetail(res), res.status);
  if (res.status === 204) return null;
  return res.json();
}

/**
 * Mint a fresh access token. Tries the refresh endpoint (which authenticates
 * from the refresh cookie alone, so it needs no password and works for OIDC
 * accounts too), then re-reads the session cookie in case the browser declined
 * to attach cookies to that request but the SPA has since renewed them.
 */
async function tryRefresh(baseUrl, cookieName) {
  try {
    const res = await rawRequest(baseUrl, '/api/auth/refresh', { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      if (data && data.access_token) {
        await setSettings({ accessToken: data.access_token });
        return data.access_token;
      }
    }
  } catch {
    /* fall through to the cookie re-read */
  }
  return readSessionCookie(baseUrl, cookieName);
}

/**
 * Adopt whatever session this browser currently holds. This is the sign-in
 * path for every account type: the user logs into Cove in a tab however they
 * normally do — password, or a redirect to their identity provider — and we
 * pick the resulting credential up from the cookie jar.
 */
export async function adoptBrowserSession(baseUrl, cookieName) {
  const token = await readSessionCookie(baseUrl, cookieName);
  if (token) {
    await setSettings({ accessToken: token });
    return token;
  }
  return tryRefresh(baseUrl, cookieName);
}

/** Username/password sign-in. Local accounts only — see the note at the top. */
export async function login(baseUrl, username, password) {
  const res = await rawRequest(baseUrl, '/api/auth/login', {
    method: 'POST',
    body: { username, password },
  });
  if (!res.ok) throw new CoveError(await errorDetail(res), res.status);
  const data = await res.json();
  await setSettings({ baseUrl, accessToken: data.access_token });
  return data.access_token;
}

export async function signOut() {
  await setSettings({ accessToken: '' });
}

// ── Endpoint wrappers ─────────────────────────────────────────────────────

/** Public — deliberately unauthenticated, so it works before we have a token. */
export async function authConfig(baseUrl) {
  const res = await rawRequest(baseUrl, '/api/auth/config');
  if (!res.ok) throw new CoveError(await errorDetail(res), res.status);
  return res.json();
}

export const me = () => request('/api/auth/me');
export const listImages = () => request('/api/images');
export const listWorkspaces = () => request('/api/workspaces');
export const tailscaleConfig = () => request('/api/users/me/tailscale');
export const gluetunConfig = () => request('/api/users/me/gluetun');
export const createWorkspace = (payload) => request('/api/workspaces', { method: 'POST', body: payload });

/** Where the SPA serves a single workspace's stream. */
export function workspaceUrl(baseUrl, id) {
  return `${baseUrl}/app/workspace/${id}`;
}
