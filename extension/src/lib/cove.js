// Cove API client and the pure helpers around it.
//
// Everything that decides *what* to send lives here as a plain function so it
// can be tested under node without a browser (see test/cove.test.mjs). Only
// `request` and the thin wrappers below touch the network.

/** Cove's own field names, so callers never invent their own vocabulary. */
export const NETWORK_MODES = ['direct', 'gluetun', 'tailscale'];

/**
 * Normalise a user-typed server address into an origin with no trailing slash.
 * Accepts "cove.example.com", "https://cove.example.com/", "…/app" — all of
 * which people paste — and throws on anything that isn't http(s).
 */
export function normalizeBaseUrl(input) {
  const raw = (input || '').trim();
  if (!raw) throw new Error('Enter your Cove server address');
  // A non-http scheme must be rejected, not repaired: prefixing "https://" onto
  // "ftp://host" yields "https://ftp://host", which URL parses happily with the
  // host "ftp" — a silently wrong server rather than an error.
  const scheme = raw.match(/^([a-z][a-z0-9+.-]*):\/\//i);
  if (scheme && !/^https?$/i.test(scheme[1])) {
    throw new Error('Only http:// and https:// are supported');
  }
  const withScheme = scheme ? raw : `https://${raw}`;
  let url;
  try {
    url = new URL(withScheme);
  } catch {
    throw new Error('That does not look like a URL');
  }
  if (!/^https?:$/.test(url.protocol)) throw new Error('Only http:// and https:// are supported');
  return url.origin;
}

/**
 * A workspace name for a link. Cove requires the name to be unique per user
 * *after sanitising*, so this is only a starting point — see `uniqueName`.
 */
export function deriveName(targetUrl, imageName) {
  let host = 'link';
  try {
    host = new URL(targetUrl).hostname.replace(/^www\./, '') || 'link';
  } catch {
    /* fall through to the default */
  }
  return `${imageName} · ${host}`;
}

/**
 * Cove sanitises names down to a storage key and rejects collisions with 409,
 * so "Chromium · github.com" twice is a conflict rather than two workspaces.
 * Suffix until the sanitised form is unused. Mirrors cove's own _sanitize:
 * lowercase, non-alphanumerics collapsed to '-'.
 */
export function sanitizeName(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

export function uniqueName(base, existingNames) {
  const taken = new Set((existingNames || []).map(sanitizeName));
  if (!taken.has(sanitizeName(base))) return base;
  for (let n = 2; n < 1000; n++) {
    const candidate = `${base} ${n}`;
    if (!taken.has(sanitizeName(candidate))) return candidate;
  }
  return `${base} ${Date.now()}`;
}

/**
 * Build the POST /api/workspaces body.
 *
 * Ephemeral and auto-remove both default on: a workspace opened from a link is
 * a disposable browsing session. Persisting /config per link would pile up
 * storage nobody asked for, and keeping the record would fill the grid with
 * cards that can only ever start blank. Everything else mirrors cove's own
 * defaults so the API never has to reject a field we invented.
 */
export function buildLaunchPayload({ name, imageId, targetUrl, network = 'direct', ephemeral = true, autoRemove = true, advanced = {}, features = null }) {
  if (!name) throw new Error('A workspace name is required');
  if (!Number.isInteger(imageId)) throw new Error('Pick a browser');
  const f = features || { kiosk: true, fullscreen: true, dark: true };
  if (!NETWORK_MODES.includes(network)) throw new Error(`Unknown network mode: ${network}`);

  const a = advanced || {};
  const payload = {
    name,
    image_id: imageId,
    target_url: targetUrl || null,
    ephemeral: !!ephemeral,
    // `--rm`: drop the record when the container stops, so a workspace opened
    // for one link does not leave a card behind that can only start blank.
    // Cove refuses this without ephemeral — a persistent home would be lost on
    // an ordinary halt — so it is forced off rather than sent as an invalid
    // pair the API would only reject.
    auto_remove: !!ephemeral && !!autoRemove,

    // Routing. Cove rejects both at once, so the tri-state is resolved here.
    use_gluetun: network === 'gluetun',
    use_tailscale: network === 'tailscale',

    // Kiosk trio. kiosk_menu only means anything when kiosk is on: it downgrades
    // the locked --kiosk to --start-fullscreen so the tab bar survives.
    // Only what this browser honours: a flag it ignores is noise at best, and
    // "keep the tab bar" without full-screen support would turn into a lock.
    kiosk: !!a.kiosk && !!(f.kiosk || f.fullscreen),
    kiosk_menu: !!a.kiosk && !!a.kioskMenu && !!f.fullscreen && !!f.kiosk,
    kiosk_dark: !!a.dark && !!f.dark,

    lan_access: !!a.lanAccess,
    allow_sudo: !!a.allowSudo,
    gpu_accel: !!a.gpuAccel,
    clear_browser_lock: !!a.clearBrowserLock,
    pixelflux_wayland: a.wayland === undefined ? true : !!a.wayland,
  };

  if (a.customDns && (a.dnsServers || '').trim()) {
    payload.custom_dns = true;
    payload.dns_servers = a.dnsServers.trim();
  }
  if (network === 'tailscale' && (a.tsExitNode || '').trim()) {
    payload.ts_exit_node = a.tsExitNode.trim();
  }
  return payload;
}

/** Which network modes this account can actually use, given cove's own gates. */
export function availableNetworks(tailscale, gluetun) {
  return {
    direct: true,
    // _validate_routing requires an auth key on the user's Tailscale config.
    tailscale: !!(tailscale && tailscale.has_auth_key),
    // …and an enabled Gluetun config with a config file uploaded.
    gluetun: !!(gluetun && gluetun.enabled && gluetun.has_config),
  };
}

/** Only browser images take a startup URL; the rest cannot open a link at all. */
export function browserImages(images) {
  return (images || []).filter((i) => i.enabled && i.image_type === 'browser' && i.url_env);
}

/**
 * Which start-up options a browser image honours.
 *
 * Cove works this out per image and reports it as `image.browser` (see
 * catalog.browser_features): Firefox takes its own --kiosk but has no
 * full-screen or dark switch, Vivaldi honours none of them, the Chromium family
 * takes all three. An older Cove doesn't send the field at all — assume Chromium
 * flags there, which is what this extension did before and what these images
 * overwhelmingly are.
 */
export function browserFeatures(image) {
  const f = image && image.browser;
  if (!f) return { kiosk: true, fullscreen: true, dark: true };
  return { kiosk: !!f.kiosk, fullscreen: !!f.fullscreen, dark: !!f.dark };
}

export class CoveError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

/** Pull FastAPI's `detail` out of an error body, which may be a string or a list. */
export async function errorDetail(response) {
  let body;
  try {
    body = await response.json();
  } catch {
    return `${response.status} ${response.statusText}`;
  }
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length && d[0].msg) return d.map((e) => e.msg).join('; ');
  return `${response.status} ${response.statusText}`;
}

/**
 * Does this saved advanced-options set differ from Cove's defaults?
 *
 * Used to auto-open the Advanced section, so a remembered setting is never
 * applied invisibly. `wayland` is the awkward one: it defaults to ON, so its
 * mere presence means nothing — only being switched off is a deviation.
 */
export function hasNonDefaultAdvanced(advanced) {
  const a = advanced || {};
  const flags = ['kiosk', 'kioskMenu', 'lanAccess', 'customDns', 'gpuAccel', 'allowSudo', 'clearBrowserLock'];
  if (flags.some((k) => a[k])) return true;
  // dark is tri-state and only stored when it overrides the browser, so it
  // counts as a deviation whichever way it was set — including to false.
  if (a.dark !== undefined && a.dark !== null) return true;
  if (a.wayland === false) return true;
  return !!(a.dnsServers || '').trim() || !!(a.tsExitNode || '').trim();
}

/** Cove's default session-cookie name (COVE_COOKIE_SESSION_NAME overrides it). */
export const DEFAULT_SESSION_COOKIE = 'cove_session';

/** Where the SPA's login screen lives — it offers local and OIDC alike. */
export function loginPageUrl(baseUrl) {
  return `${baseUrl}/app/login`;
}

/**
 * What sign-in routes to offer, from GET /api/auth/config.
 *
 * OIDC accounts have no password_hash, so /api/auth/login answers 401 for them
 * even when local login is enabled — and 403 outright in oidc_only mode. So the
 * password form is only ever worth showing when local login can actually
 * succeed, and the browser hand-off is the route that always works.
 */
export function authModes(config) {
  const c = config || {};
  return {
    browser: true,
    password: !c.oidc_only,
    oidc: !!c.oidc_enabled,
    providerName: c.oidc_provider_name || 'SSO',
  };
}

/**
 * The dark-mode default for a new launch.
 *
 * With no saved preference, follow the browser the extension is running in: if
 * you browse dark, the workspace you are about to open should not flash white.
 * A value the user explicitly saved still wins — otherwise "remember these as
 * my defaults" would quietly not remember this one.
 *
 * `saved` is deliberately tri-state. Collapsing undefined into false is what
 * makes "not set yet" indistinguishable from "the user turned it off", and the
 * two want opposite behaviour here.
 */
export function resolveDarkDefault(saved, prefersDark) {
  return saved === undefined || saved === null ? !!prefersDark : !!saved;
}

/** Does this launch's dark setting come from the browser rather than a choice? */
export function darkIsInherited(saved, prefersDark) {
  return (saved === undefined || saved === null) && !!prefersDark;
}

/**
 * What to persist for dark mode when the user saves their defaults.
 *
 * Returns undefined when the box matches the browser, so "follow the browser"
 * stays live rather than being frozen into whatever the browser happened to be
 * the day they pressed remember. Only a deliberate override is stored.
 */
export function darkPreferenceToSave(checked, prefersDark) {
  return !!checked === !!prefersDark ? undefined : !!checked;
}
