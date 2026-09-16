# Open in Cove

A browser extension that opens any link in a fresh [Cove](https://gitlab.com/muddycove/cove)
browser workspace — you pick the browser and the network route, and the page
loads in a container instead of in your own browser.

**For Chrome-based browsers** — Chrome, Edge, Brave, Vivaldi, Opera, Helium and
other Chromium forks. It is not packaged for Firefox: Firefox's extension APIs
differ enough (and its MV3 support lags) that a build claiming to support it
would be one nobody had run.

Right-click a link → **Open link in Cove workspace**, or click the toolbar
button to send the current page. A small launcher window asks:

| | |
|---|---|
| **Browser** | Chromium, Brave, Firefox, Edge, Helium, Vivaldi, Opera — whichever browser images are enabled on your server |
| **Network** | **Direct**, **VPN** (Gluetun), or **Tailscale**. Routes you have not configured are shown disabled, with the reason |
| **Ephemeral** | **On by default** — nothing is saved and the profile is discarded when the workspace stops |
| **Discard when stopped** | **On by default** — `docker run --rm` for workspaces: the record is deleted when it halts, so opening links does not fill your grid with cards that can only start blank. Requires ephemeral, and follows it automatically |
| **Advanced** | Kiosk mode, dark mode, LAN access, custom DNS, Tailscale exit node, GPU, Wayland, and the workspace name. The kiosk and dark options appear only for browsers that honour them — Cove reports that per image, so e.g. Firefox offers kiosk but not dark, and Vivaldi offers neither |
| **Dark mode** | **Follows this browser.** If you browse dark, the workspace opens dark, so a link does not flash white at you. Untick it to override — and an override is what gets remembered, so following the browser stays live |

Tick **Remember these as my defaults** and the next link opens with the same
choices pre-filled.

## Install

Not in any store — load it unpacked. Your own Cove server also offers it as a zip
under **Preferences → Browser extension**, which saves cloning this repo.

1. Download the zip from Cove (or clone this repo) and **unzip it somewhere it can
   stay** — Chrome loads an unpacked extension from that folder every time it
   starts, so deleting the folder uninstalls it.
2. Open `chrome://extensions` (Edge: `edge://extensions`, Brave:
   `brave://extensions`, and so on for other Chromium browsers).
3. Turn on **Developer mode**.
4. Click **Load unpacked** and pick the unzipped folder (the one holding
   `manifest.json`).
5. Open the extension's **options** and enter your Cove address.

Updating is the same flow: unzip the new version over the old folder and press
**Reload** on the extension's card.

## How it authenticates

**Works the same for local and SSO/OIDC accounts, and never needs your password.**

Cove's session cookie *is* its access token — `routers/auth.py` sets
`cove_session` to `create_access_token(...)`, and the OIDC callback goes through
the very same `_set_auth_cookies()`. The extension reads that cookie and presents
it as `Authorization: Bearer`. Extensions may read httponly cookies, so this
works whether or not the browser would attach the cookie to a cross-site `fetch`
— which `SameSite=Lax` does not oblige it to do, and browsers differ on.

Credentials are tried in this order:

1. the live session cookie — always current, every account type
2. a token stored from a previous session
3. `POST /api/auth/refresh`, which authenticates from the refresh cookie alone

Only if all three fail are you asked to sign in, and then **in a normal browser
tab**, where your identity provider works normally. Come back to the options
page and it adopts the new session automatically.

There is also a username/password form, but it is hidden when the server reports
`oidc_only`, because it cannot succeed there. Worth knowing even when it is
shown: an **OIDC account has no password on the Cove side at all** — it has no
`password_hash`, so `/api/auth/login` answers 401 for it regardless. If you have
an SSO account, the sign-in button is the route; the form is for local accounts.

Only an access token is ever stored, never a password. Changing your Cove
password revokes it immediately.

If your deployment sets `COVE_COOKIE_SESSION_NAME`, put the custom name under
**Advanced** in the options.

## Permissions

The manifest requests **no host permissions up front**. When you save your Cove
address, the extension asks for access to that one origin and nothing else — so
it never holds blanket access to every site you visit.

`cookies` is what lets it adopt your existing Cove session instead of asking for
a password; it is scoped to the one host you granted. `contextMenus` and
`storage` cover the menu entry and your saved settings, and `activeTab` reads
the current tab's URL when you click the toolbar button.

## Notes and limits

- Only **browser** images can open a URL. Desktop and single-app images have no
  startup-URL environment variable, so they are filtered out of the picker.
- **Kiosk mode locks the window** — no tab bar, context menu or shortcuts.
  Tick *…but keep the tab bar* for full screen without the lockdown. Cove also
  ignores the lock for multi-URL launches, since tabs need a tab bar.
- Cove requires workspace names to be unique **after sanitising**, so `Brave`
  and `brave!` collide. The extension checks your existing names and suffixes
  automatically.
- **LAN access** and **GPU** are opt-ins that only take effect if an admin has
  enabled them server-side. Ticking them on a server that has not will launch
  without them rather than fail.

## Development

```bash
npm test     # 28 tests: payload building, auth modes, name collisions,
             #           DOM/manifest wiring, and the lint substitutes below
npm run check  # parse every module
```

There is no build step and no dependencies — the extension is plain ES modules
loaded directly by the browser. That is also why there is no ESLint: adding it
would end the zero-dependency property that lets CI skip an install step and a
CVE gate. `test/lint.test.mjs` covers the defect class a linter would catch
here — an import, an export, or an element id that is declared and then never
used — and each of those checks was verified to fail on a deliberately broken
copy before being trusted. `src/lib/cove.js` holds every decision about
*what* to send and is pure, so it is testable without a browser;
`src/lib/client.js` is the only module that touches the network.

Icons are generated, not committed as opaque binaries:

```bash
python3 icons/generate.py
```
