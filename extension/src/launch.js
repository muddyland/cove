import { buildLaunchPayload, browserFeatures, deriveName, uniqueName, availableNetworks, pickProfile, profileLabel, browserImages, hasNonDefaultAdvanced, resolveDarkDefault, darkIsInherited, darkPreferenceToSave, loginPageUrl, NETWORK_MODES } from './lib/cove.js';
import * as cove from './lib/client.js';

const api = globalThis.browser ?? globalThis.chrome;
const $ = (id) => document.getElementById(id);

const NETWORK_LABELS = { direct: 'Direct', gluetun: 'VPN', tailscale: 'Tailscale' };
const NETWORK_HINTS = {
  direct: 'Straight out to the internet, firewalled off from your LAN.',
  gluetun: 'All traffic through the VPN profile below.',
  tailscale: 'Joined to the tailnet of the profile below; optionally via an exit node.',
};
const UNAVAILABLE = {
  gluetun: 'No usable VPN profile — add one in Cove → Preferences.',
  tailscale: 'No usable Tailscale profile — add one in Cove → Preferences.',
};
const PROFILE_LABELS = { gluetun: 'VPN profile', tailscale: 'Tailscale profile' };

let state = {
  images: [], networks: { direct: true }, network: 'direct', existingNames: [], prefersDark: false,
  profiles: { gluetun: [], tailscale: [] }, rememberedProfile: {},
};

init();

async function init() {
  const targetUrl = new URLSearchParams(location.search).get('url') || '';
  $('url').value = targetUrl;

  const { baseUrl, defaults } = await cove.getSettings();
  if (!baseUrl) return showBlocked('No Cove server configured yet.', 'Open options', openOptions);

  try {
    // Fetched together: the launcher is useless without all four, and doing them
    // in series would show an empty form for as long as the slowest one takes.
    const [images, tailscale, gluetun, workspaces] = await Promise.all([
      cove.listImages(),
      cove.tailscaleProfiles().catch(() => []),
      cove.vpnProfiles().catch(() => []),
      cove.listWorkspaces().catch(() => []),
    ]);
    state.images = browserImages(images);
    state.profiles = { gluetun: gluetun || [], tailscale: tailscale || [] };
    state.rememberedProfile = defaults.profiles || {};
    state.networks = availableNetworks(tailscale, gluetun);
    state.existingNames = (workspaces || []).map((w) => w.name);
  } catch (e) {
    // A 401 here means no usable session at all — request() already tries the
    // browser's own Cove cookie, which covers SSO accounts without a password.
    // So send them to Cove's login page rather than to a form that cannot help.
    if (e.status === 401) return showBlocked('Not signed in to Cove.', 'Sign in to Cove', openLogin);
    return showBlocked(e.message, 'Open options', openOptions);
  }

  if (!state.images.length) {
    return showBlocked('No browser images are enabled on this Cove server.', 'Open options', openOptions);
  }

  renderImages(defaults);
  renderNetworks(defaults);
  applyDefaults(defaults);
  syncBrowserOptions();
  wire();

  $('loading').hidden = true;
  $('form').hidden = false;
}

function renderImages(defaults) {
  const sel = $('image');
  sel.replaceChildren(
    ...state.images.map((img) => {
      const o = document.createElement('option');
      o.value = String(img.id);
      o.textContent = img.name;
      return o;
    }),
  );
  // Match the remembered browser by name rather than id: image ids are per
  // install and get renumbered when an admin re-syncs the catalog.
  const wanted = state.images.find((i) => i.name === defaults.imageName);
  sel.value = String((wanted || state.images[0]).id);
  sel.addEventListener('change', syncBrowserOptions);
  syncBrowserOptions();
}

/** The picked browser's own capabilities. */
function currentFeatures() {
  return browserFeatures(state.images.find((i) => String(i.id) === $('image').value));
}

/**
 * Show only the start-up options the picked browser honours. Hiding beats
 * disabling here: a greyed-out row invites "why?", and the answer — this browser
 * ignores the flag — is not something to make people hunt for.
 */
function syncBrowserOptions() {
  const f = currentFeatures();
  const kioskRow = $('kiosk').closest('label');
  const menuRow = $('kioskMenu').closest('label');
  const darkRow = $('dark').closest('label');
  const windowFlags = f.kiosk || f.fullscreen;

  kioskRow.hidden = !windowFlags;
  if (!windowFlags) $('kiosk').checked = false;
  // Without the kiosk lock, the toggle can still ask for plain full screen.
  kioskRow.querySelector('strong').textContent = f.kiosk ? 'Kiosk mode' : 'Start full screen';
  kioskRow.querySelector('em').textContent = f.kiosk
    ? 'Full screen, locked: no tab bar, context menu or shortcuts.'
    : 'Full screen, with the browser\'s own UI.';

  menuRow.hidden = !(f.kiosk && f.fullscreen);
  if (menuRow.hidden) $('kioskMenu').checked = false;
  darkRow.hidden = !f.dark;
  if (!f.dark) $('dark').checked = false;

  const note = $('browser-note');
  if (note) {
    note.hidden = windowFlags;
    note.textContent = windowFlags ? '' : 'This browser ignores start-up window flags, so it opens in its normal window.';
  }
  syncDependentFields();
}

function renderNetworks(defaults) {
  const wrap = $('network');
  wrap.replaceChildren(
    ...NETWORK_MODES.map((mode) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.dataset.mode = mode;
      b.textContent = NETWORK_LABELS[mode];
      b.disabled = !state.networks[mode];
      if (b.disabled) b.title = UNAVAILABLE[mode];
      b.addEventListener('click', () => selectNetwork(mode));
      return b;
    }),
  );
  const remembered = defaults.network;
  selectNetwork(state.networks[remembered] ? remembered : 'direct');
}

function selectNetwork(mode) {
  state.network = mode;
  for (const b of $('network').children) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
  $('network-hint').textContent = NETWORK_HINTS[mode];
  $('exit-field').hidden = mode !== 'tailscale';
  renderProfiles(mode);
}

/**
 * The profile picker for the chosen route. Incomplete profiles are listed but
 * disabled (with what they lack), and a VPN profile already connected in
 * another workspace says so — Cove allows one connection per profile, so
 * launching on it would fail. A Cove without profiles yields one stand-in
 * entry, which needs no picker.
 */
function renderProfiles(mode) {
  const list = state.profiles[mode] || [];
  const field = $('profile-field');
  const sel = $('profile');
  const show = mode !== 'direct' && list.length > 0 && !list[0].legacy;
  field.hidden = !show;
  if (!show) { sel.replaceChildren(); return; }
  $('profile-label').textContent = PROFILE_LABELS[mode];
  sel.replaceChildren(
    ...list.map((p) => {
      const o = document.createElement('option');
      o.value = String(p.id);
      o.textContent = profileLabel(p);
      o.disabled = !!p.problem;
      return o;
    }),
  );
  const chosen = pickProfile(list, state.rememberedProfile[mode]);
  if (chosen) sel.value = String(chosen.id);
  sel.onchange = syncProfileWarning;
  syncProfileWarning();
}

function currentProfile() {
  const list = state.profiles[state.network] || [];
  return list.find((p) => String(p.id) === $('profile').value) || null;
}

function syncProfileWarning() {
  const p = currentProfile();
  $('network-hint').textContent = p && p.active_in
    ? `“${p.name}” is connected in ${p.active_in}. A VPN profile carries one connection at a time, so stop that workspace first or pick another profile.`
    : NETWORK_HINTS[state.network];
}

function applyDefaults(d) {
  const adv = d.advanced || {};
  $('ephemeral').checked = d.ephemeral === undefined ? true : !!d.ephemeral;
  $('autoRemove').checked = d.autoRemove === undefined ? true : !!d.autoRemove;
  for (const k of ['kiosk', 'kioskMenu', 'lanAccess', 'customDns', 'gpuAccel', 'clearBrowserLock']) {
    $(k).checked = !!adv[k];
  }

  // Dark mode follows the browser this launcher is running in unless the user
  // saved a choice. Opening a link from a dark browser into a workspace that
  // flashes white is the thing this avoids.
  const prefersDark = !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  state.prefersDark = prefersDark;
  $('dark').checked = resolveDarkDefault(adv.dark, prefersDark);
  if (darkIsInherited(adv.dark, prefersDark)) {
    // Say so in both places: in the panel for anyone who opens it, and in the
    // collapsed summary for everyone else — a default applied inside a closed
    // section is otherwise invisible. Opening the panel on every launch just
    // because the browser is dark would be worse.
    $('dark-hint').textContent = 'On, matching this browser. Untick to override.';
    $('advanced-note').textContent = 'dark mode on';
  }
  $('wayland').checked = adv.wayland === undefined ? true : !!adv.wayland;
  $('dnsServers').value = adv.dnsServers || '';
  $('tsExitNode').value = adv.tsExitNode || '';
  syncDependentFields();
  // Open the section if anything in it is non-default, so a remembered setting
  // is never applied invisibly.
  if (hasNonDefaultAdvanced(adv)) $('advanced').open = true;
}

function syncDependentFields() {
  // Cove only accepts auto_remove alongside ephemeral, so the control follows it.
  $('autoremove-row').hidden = !$('ephemeral').checked;
  $('dns-field').hidden = !$('customDns').checked;
  $('kioskMenu').disabled = !$('kiosk').checked;
}

function wire() {
  $('ephemeral').addEventListener('change', syncDependentFields);
  $('customDns').addEventListener('change', syncDependentFields);
  $('kiosk').addEventListener('change', syncDependentFields);
  $('launch').addEventListener('click', launch);
  $('settings-link').addEventListener('click', (e) => { e.preventDefault(); openOptions(); });
}

function collectAdvanced() {
  return {
    kiosk: $('kiosk').checked,
    kioskMenu: $('kioskMenu').checked,
    dark: $('dark').checked,
    lanAccess: $('lanAccess').checked,
    customDns: $('customDns').checked,
    dnsServers: $('dnsServers').value,
    tsExitNode: $('tsExitNode').value,
    gpuAccel: $('gpuAccel').checked,
    clearBrowserLock: $('clearBrowserLock').checked,
    wayland: $('wayland').checked,
  };
}

async function launch() {
  const btn = $('launch');
  const status = $('status');
  status.className = 'status';
  status.textContent = '';

  const targetUrl = $('url').value.trim();
  const image = state.images.find((i) => String(i.id) === $('image').value);
  const advanced = collectAdvanced();
  const typed = $('name').value.trim();
  const name = uniqueName(typed || deriveName(targetUrl, image.name), state.existingNames);

  let payload;
  try {
    payload = buildLaunchPayload({
      name,
      imageId: image.id,
      targetUrl,
      network: state.network,
      ephemeral: $('ephemeral').checked,
      autoRemove: $('autoRemove').checked,
      advanced,
      features: currentFeatures(),
      profileId: currentProfile()?.id ?? null,
    });
  } catch (e) {
    status.className = 'status error';
    status.textContent = e.message;
    return;
  }

  btn.disabled = true;
  status.textContent = 'Launching…';

  try {
    if ($('remember').checked) {
      // Store only a deliberate dark override, so the browser-following default
      // survives being remembered. storage drops nothing for us, so the key is
      // removed rather than set to undefined.
      const savedAdvanced = { ...advanced, dark: darkPreferenceToSave(advanced.dark, state.prefersDark) };
      if (savedAdvanced.dark === undefined) delete savedAdvanced.dark;
      await cove.setSettings({
        defaults: {
          imageName: image.name,
          network: state.network,
          // By name, like the browser: profile ids are per install.
          profiles: {
            ...state.rememberedProfile,
            ...(currentProfile() && !currentProfile().legacy ? { [state.network]: currentProfile().name } : {}),
          },
          ephemeral: $('ephemeral').checked,
          autoRemove: $('autoRemove').checked,
          advanced: savedAdvanced,
        },
      });
    }
    const ws = await cove.createWorkspace(payload);
    const { baseUrl } = await cove.getSettings();
    status.className = 'status ok';
    status.textContent = `Booting “${ws.name}” — opening it now.`;
    await api.tabs.create({ url: cove.workspaceUrl(baseUrl, ws.id) });
    window.close();
  } catch (e) {
    btn.disabled = false;
    status.className = 'status error';
    status.textContent = e.message;
  }
}

function showBlocked(message, actionLabel, action) {
  $('loading').hidden = true;
  $('blocked').hidden = false;
  $('blocked-msg').textContent = message;
  const btn = $('blocked-action');
  btn.textContent = actionLabel;
  btn.addEventListener('click', action);
}

function openOptions() {
  api.runtime.openOptionsPage();
}

/** Cove's own login screen handles local and SSO alike; the extension picks the
 *  resulting session up from the cookie jar next time it is opened. */
async function openLogin() {
  const { baseUrl } = await cove.getSettings();
  if (baseUrl) await api.tabs.create({ url: loginPageUrl(baseUrl) });
  window.close();
}
