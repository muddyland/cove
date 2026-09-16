import { normalizeBaseUrl, loginPageUrl, authModes, DEFAULT_SESSION_COOKIE } from './lib/cove.js';
import * as cove from './lib/client.js';

const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

const say = (el, msg, kind = '') => {
  el.className = `status ${kind}`;
  el.textContent = msg;
};

let modes = { browser: true, password: true, oidc: false, providerName: 'SSO' };

init();

async function init() {
  const { baseUrl, sessionCookieName } = await cove.getSettings();
  $('baseUrl').value = baseUrl;
  $('cookieName').value = sessionCookieName || DEFAULT_SESSION_COOKIE;

  $('save').addEventListener('click', saveServer);
  $('save-cookie').addEventListener('click', saveCookieName);
  $('open-login').addEventListener('click', openLogin);
  $('connect').addEventListener('click', connect);
  $('login').addEventListener('click', signInWithPassword);
  $('recheck').addEventListener('click', refresh);
  $('signout').addEventListener('click', forgetToken);

  // Coming back from the login tab is the common case, so re-check on focus
  // rather than making the user press a button they can easily miss.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && $('signin').hidden === false) connect(true);
  });

  if (baseUrl) refresh();
}

/**
 * Saving the address also requests host permission for exactly that origin.
 * The manifest asks for none up front, so the extension never holds blanket
 * access to every site the user visits.
 */
async function saveServer() {
  const status = $('server-status');
  let origin;
  try {
    origin = normalizeBaseUrl($('baseUrl').value);
  } catch (e) {
    return say(status, e.message, 'error');
  }

  const granted = await api.permissions.request({ origins: [`${origin}/*`] });
  if (!granted) return say(status, 'Permission denied — the extension cannot reach that host.', 'error');

  await cove.setSettings({ baseUrl: origin });
  $('baseUrl').value = origin;
  say(status, `Saved ${origin}`, 'ok');
  refresh();
}

async function saveCookieName() {
  const name = $('cookieName').value.trim() || DEFAULT_SESSION_COOKIE;
  await cove.setSettings({ sessionCookieName: name });
  $('cookieName').value = name;
  say($('server-status'), `Session cookie: ${name}`, 'ok');
  refresh();
}

/** Ask the server how sign-in works here, then show what is actually usable. */
async function loadModes(baseUrl) {
  try {
    modes = authModes(await cove.authConfig(baseUrl));
  } catch {
    // Unreachable or an older server: assume both routes rather than hiding one.
    modes = authModes(null);
  }
  $('open-login').textContent = modes.oidc ? `Sign in with ${modes.providerName}` : 'Open Cove sign-in';
  $('signin-note').textContent = modes.oidc
    ? `Signs in through ${modes.providerName} in a normal tab, then the extension adopts that session.`
    : 'Signs in through Cove in a normal tab, then the extension adopts that session.';
  $('password-block').hidden = !modes.password;
}

async function refresh() {
  const { baseUrl } = await cove.getSettings();
  const status = $('auth-status');
  if (!baseUrl) return say(status, 'Set your Cove address above first.');

  say(status, 'Checking…');
  await loadModes(baseUrl);

  try {
    const user = await cove.me();
    say(status, `Signed in as ${user.username}.`, 'ok');
    $('signin').hidden = true;
    $('session-actions').hidden = false;
  } catch (e) {
    $('signin').hidden = false;
    $('session-actions').hidden = false;
    say(status, e.status === 401 ? 'Reachable, but not signed in.' : e.message, 'error');
  }
}

async function openLogin() {
  const { baseUrl } = await cove.getSettings();
  if (baseUrl) await api.tabs.create({ url: loginPageUrl(baseUrl) });
}

/** Adopt the browser's current Cove session. `quiet` skips the failure noise
 *  when this runs automatically on tab focus. */
async function connect(quiet = false) {
  const { baseUrl, sessionCookieName } = await cove.getSettings();
  if (!baseUrl) return;
  const token = await cove.adoptBrowserSession(baseUrl, sessionCookieName);
  if (token) return refresh();
  if (quiet !== true) say($('auth-status'), 'No Cove session found in this browser yet.', 'error');
}

async function signInWithPassword() {
  const status = $('auth-status');
  let origin;
  try {
    origin = normalizeBaseUrl($('baseUrl').value);
  } catch (e) {
    return say(status, e.message, 'error');
  }
  try {
    await cove.login(origin, $('username').value, $('password').value);
    $('password').value = '';
    refresh();
  } catch (e) {
    // 401 here usually means an SSO account rather than a wrong password.
    const hint = e.status === 401 && modes.oidc
      ? ' If this is an SSO account, use the sign-in button above instead.'
      : '';
    say(status, e.message + hint, 'error');
  }
}

async function forgetToken() {
  await cove.signOut();
  say($('auth-status'), 'Stored token cleared.', '');
  refresh();
}
