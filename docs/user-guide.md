# User guide

This is the day-to-day guide to using Cove as a regular user. For the full set of
launch options and the workspace lifecycle, see [Workspaces](workspaces.md).

## Navigation

After signing in you're in the SPA under `/app`. The top navigation has:

- **Dashboard** — your workspaces, plus the launch buttons.
- **Files** — browse your workspace storage.
- **Preferences** — password, SSH key, Tailscale, Gluetun.
- **Admin** (admins only) — Users, Sessions, Images, Audit, Settings. See [Administration](administration.md).
- **Background tasks** (the checklist icon) — app installs, updates and removals
  running inside your workspaces. It shows a spinner and a count while work is in
  flight; open it for progress and each task's log. See [Managing apps](#managing-apps-in-a-workspace).

## The dashboard

The dashboard ("Workspace Grid") lists **your own** workspaces, split into
**Active** and **Offline**. Each running card shows:

- A live **CPU** and **memory** readout for the container.
- The **status** (booting / provisioning / running / error).
- For tailnet-routed workspaces, the **Tailscale IP** (copyable).
- Actions: **Connect**, **Apps**, **Logs**, **Halt**, and **Purge** (delete),
  plus **Edit**, **Clone** and **Migrate** on a stopped one.

**Launch** at the top opens the one launcher for everything: it asks what you
want — a **Desktop**, a **Browser**, or an **App** — then shows only the images of
that kind.

## Screen previews

Every **running** card shows a still of what's actually on that node's screen, so
you can tell your workspaces apart at a glance without opening them.

- The frame is captured from the workspace's **own stream**, so it works the same
  for every image and needs nothing installed inside it.
- It doubles as the **readiness check**: you can't connect to a workspace until
  Cove has decoded a real frame from its stream (the card shows **STARTING** until
  then), so the stream never opens half-ready.
- Previews are **dropped the moment a workspace halts** — a stopped card never
  shows what was last on its screen. Booting it again takes a fresh one.
- A stream Cove can't read simply leaves the card with a placeholder. The
  workspace still launches normally.

Previews are served only to you (or an admin). See
[Workspaces → Screen previews](workspaces.md#screen-previews) for the details.

## Launching a desktop

1. Click **Launch** and choose **Desktop**.
2. Pick an **image** (e.g. a Webtop XFCE/KDE/MATE variant, or Kali) and give it a **name**.
3. Optionally expand the extra options (apps, networking, sudo, SSH) — see [Workspaces → Launch options](workspaces.md#launch-options).
4. Click **Launch**. Cove creates the container and takes you to the workspace view, which shows a **Booting / Provisioning** screen until the desktop is ready, then streams it into the page.

The first launch of an image pulls it from `lscr.io` and can take a few minutes.

## Opening a website (browser workspaces)

Browser images (Chromium, Brave, Firefox, Edge) can boot straight to one or more
URLs — a lightweight way to deliver a web app:

1. Click **Launch** and choose **Browser**.
2. Pick the **browser** (Chromium, Brave, Firefox, Edge, Helium, Vivaldi or Opera).
3. Enter one URL per line — **up to 6**; each opens in its own tab. Multiple tabs open full-screen with a tab bar.
4. Options for a single URL, shown only where that browser supports them (see
   [Workspaces → browser start-up options](workspaces.md#browser-start-up-options)):
   - **Kiosk mode** — full-screen with no browser chrome.
   - **Allow right-click / refresh menu** — keeps a minimal menu instead of a hard kiosk lock.

   **Dark mode** sits on its own (it applies with or without kiosk) and starts
   **on when your own browser is in dark mode**, so an opened link doesn't flash
   white at you. Untick it to override.
5. Optionally tick **Ephemeral** (no saved data — cookies/history/downloads wiped on halt) or **Route through Tailscale**.
6. Click **Launch**.

## In-stream controls

While viewing a running workspace, a top bar provides:

- **Quick-switch menu** — a dropdown next to the workspace name listing all your workspaces (running first). Jump between them, and **boot a stopped one in place** without returning to the dashboard.
- **Fullscreen** (`FULL` / `WINDOW`) — expand the stream to fill the window.
- **CRT** — a retro scanline/flicker overlay (cosmetic; per-user toggle).
- **Apps** — on a desktop workspace, manage its proot-apps and AppImages (see [above](#managing-apps-in-a-workspace)).
- **Logs** — opens diagnostics: container logs and (for tailnet workspaces) `tailscale status`.
- **HALT** — stop and remove the container (persistent data is kept).
- **APP / install** — install *this* workspace as its own PWA (its own icon and window). Handy for a single-purpose browser workspace.
- **Connection indicators** — a lock icon when routed through a VPN (Gluetun), a network icon when routed through Tailscale (with the exit node in the tooltip).

## Managing apps in a workspace

**Actions → Apps** on a running desktop (or **Apps** in the stream page's menu)
manages what's installed inside that workspace, without a terminal:

- **proot-apps** — the list shows each app with its icon and whether it's **up to
  date** or has an **update available**, compared against the LinuxServer
  registry. Update one, update all, install more from the catalog, or remove one.
- **AppImages** — the list shows each app's size and the URL it came from.
  Install more by pasting URLs, update one by pasting the URL to install over it,
  or remove it. There's no update check here: an AppImage URL carries no version
  to compare, so you choose the link.

Everything runs as a **background task** inside the workspace, one at a time. You
can close the dialog; the **tasks** icon in the top bar tracks progress across all
your workspaces and keeps each task's log. Installing or removing also updates the
workspace's saved app lists, so a reboot or a migration reinstalls what you
actually have.

Booting a workspace installs anything missing from those saved lists but never
updates what's already there — updates are always your call. For the details see
[Workspaces → Managing apps](workspaces.md#managing-apps-in-a-running-workspace).

## The file browser

**Files** lets you browse, upload, download, organize, and delete files within
**your own** storage area (`<storage>/<your-username>/…`, which holds your
workspaces' `/config` homes). Access is confined to your directory — path
traversal is rejected — and a single upload may be up to `COVE_MAX_UPLOAD_MB`
(default **1024 MiB**). It has two tabs: **Files** and **Trash**.

### Getting files in

- **Upload** — pick one or more files; they land in the folder you're viewing.
- **Folder** — pick a whole folder; its sub-folders are recreated as the files upload.
- **Drag and drop** files or folders from your desktop onto the file list.

There is no separate "new folder" action — folders appear when you upload one.

### Moving and copying

Click a row's **Copy** or **Cut**, navigate to the destination, then **Paste**
(the button appears in the header once something is on the clipboard). Or **drag a
row onto a folder** — or onto **root** in the breadcrumb — to move it there. A name
collision doesn't overwrite: the arriving item is given a suffix.

### Downloading

**Download** on a file sends the file. **Download** on a folder sends it as a
streamed **zip**, so there's no need to fetch a directory tree file by file.

### Trash

Deleting is a **soft delete** by default:

- **Move to trash** (the bin icon) moves the item into your trash and out of the
  listing. Nothing is destroyed.
- The **Trash** tab lists what's in there with each item's original location,
  size, when it was deleted, and when it **expires**.
- **Restore** puts an item back where it came from, recreating the folder if it's
  gone. If something with that name is already there, the restored copy is
  suffixed rather than overwriting it.
- **Delete** on a trash row, or **Empty trash**, destroys the bytes immediately.
- **Delete permanently** (the ✕ on a normal row) **skips the trash** entirely.
  Both ask for confirmation and can't be undone.

Trash expires on its own: the admin's **trash retention** setting (default **30
days**) decides how long an item is kept before Cove purges it automatically, and
the Trash tab shows the countdown per item. Set to `0`, nothing auto-expires and
the trash is kept until you empty it — see
[Administration → Settings](administration.md#settings).

## The browser extension

**Open in Cove** adds a right-click action to the browser on your own machine:
send any link to a fresh Cove browser workspace instead of opening it locally.
You pick the browser, the network route (direct, VPN or Tailscale) and the
start-up options, and the page loads in a container.

**It is for Chrome-based browsers** — Chrome, Edge, Brave, Vivaldi, Opera, Helium
and other Chromium forks. There is no Firefox build.

Download it from **Preferences → Browser extension** (your Cove server ships the
zip, so this works with no internet access), then:

1. **Unzip it somewhere it can stay** — Chrome loads an unpacked extension from
   that folder every time it starts, so deleting the folder uninstalls it.
2. Open `chrome://extensions` (Edge `edge://extensions`, Brave
   `brave://extensions`, and so on).
3. Turn on **Developer mode**.
4. Click **Load unpacked** and pick the unzipped folder — the one holding
   `manifest.json`.
5. Open the extension's **options** and enter your Cove address.

To update later, unzip the new version over the same folder and press **Reload**
on the extension's card.

Each link opens in a brand-new workspace, so browsers that have a first-run
screen (Vivaldi's setup window, Opera's welcome tabs) show it on *every* launch —
they start from an empty profile each time. Chromium and Helium don't have one.
See [Troubleshooting](troubleshooting.md#a-browser-opens-its-own-welcome-screen-instead-of-the-link).

It signs in as you without a password, including for SSO accounts: it reads your
existing Cove session and presents it as a bearer token, so the identity provider
flow happens in a normal browser tab. Only an access token is ever stored.

## Preferences

Manage your own account and routing under **Preferences**:

- **Password** — change it (local accounts only; hidden for SSO accounts). Minimum 8 characters; changing it signs out your other sessions.
- **SSH key** — generate a fresh **Ed25519** keypair, or upload your own private key (ed25519/rsa/ecdsa/dsa; **unencrypted** keys only — passphrase-protected keys are rejected). The public key and fingerprint are shown; the private key is encrypted at rest and never returned. When set, it's injected into each workspace's `~/.ssh` at launch (toggle per workspace with **Inject SSH key**), so in-container `git`/`ssh` works out of the box.
- **Tailscale** — enable it and store your **auth key** (a preauth key, encrypted at rest) and an optional **login/control server** (e.g. a Headscale `https://` URL). Per-launch options (exit node, accept routes/DNS) are chosen when launching a workspace.
- **Gluetun (VPN)** — enable it, choose **OpenVPN** or **WireGuard**, and upload your VPN **config file** (encrypted at rest, up to 128 KiB). You can optionally override the WireGuard private key or OpenVPN username/password as separate secrets.

See [Networking & routing](networking.md) for how Tailscale and Gluetun apply to
a workspace's traffic.
