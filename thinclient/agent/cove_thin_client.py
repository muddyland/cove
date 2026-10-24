#!/usr/bin/env python3
"""Cove thin client agent.

Runs as root on a Debian machine and turns it into a Cove kiosk:

* a ``kiosk`` user auto-logs in on tty1 and runs **sway** with no key bindings
  (nothing to escape to);
* the agent launches **Chromium** full screen as that user, in its own transient
  systemd unit, on a profile it wipes at every launch, locked down by a managed
  policy that only allows Cove's own hosts;
* it watches the keyboard/mouse (evdev) for idleness: the screen goes dark and,
  later, the browser is reset, which signs whoever was using it out;
* it **dials out** to Cove (HTTPS long-poll) to report state, receive policy and
  run commands — no inbound port, nothing new exposed.

Stdlib only, Python 3.11+ (Debian 12 and 13). Cove serves this file and the agent
updates itself when Cove's copy changes (checked against a sha256).

Usage: agent.py install | converge | run | poke | status | version
"""

import datetime as dt
import glob
import grp
import hashlib
import json
import logging
import os
import pwd
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

AGENT_VERSION = "1.0.0"

ETC = "/etc/cove-thin-client"
CONFIG = f"{ETC}/config.json"
TOKEN_FILE = f"{ETC}/device.token"
LIB = "/usr/local/lib/cove-thin-client"
AGENT_PATH = f"{LIB}/agent.py"
STATE_DIR = "/var/lib/cove-thin-client"
PROFILE_DIR = f"{STATE_DIR}/browser"
CACHE_FILE = f"{STATE_DIR}/last-checkin.json"
MAINT_FILE = f"{STATE_DIR}/maintenance.json"
RUN_DIR = "/run/cove-thin-client"
ACTIVITY_FILE = f"{RUN_DIR}/activity"
SHARE = "/usr/share/cove-thin-client"
OFFLINE_PAGE = f"{SHARE}/offline.html"
SWAY_CONF = f"{ETC}/sway.conf"
SWAY_OUTPUTS = f"{ETC}/sway-outputs.conf"
CHROMIUM_POLICY = "/etc/chromium/policies/managed/cove-thin-client.json"
BROWSER_UNIT = "cove-browser"
BROWSER_STATE = f"{RUN_DIR}/browser.json"
KIOSK_USER = "kiosk"

PACKAGES = [
    "sway", "swaybg", "chromium", "grim", "pipewire-audio", "wireplumber",
    "dbus-user-session", "libpam-systemd", "ddcutil", "fonts-dejavu-core",
    "libnss3-tools", "unattended-upgrades", "plymouth", "va-driver-all",
    "python3", "ca-certificates",
]

UA = f"cove-thin-client/{AGENT_VERSION}"
LONG_POLL = 25

log = logging.getLogger("cove-thin-client")


# ── Small helpers ────────────────────────────────────────────────────────────

def run(cmd, *, timeout=60, check=False, env=None, input_=None):
    """Run a command, return (returncode, combined output). Never raises on a
    missing binary — that is just a failure."""
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, env=env, input=input_
        )
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: timed out"
    out = (p.stdout or "") + (p.stderr or "")
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed ({p.returncode}): {out[-2000:]}")
    return p.returncode, out


def write_if_changed(path, content, mode=0o644, owner=None):
    """Write ``content`` atomically if it differs. Returns True when changed."""
    data = content.encode() if isinstance(content, str) else content
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.cove-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.chmod(tmp, mode)
    if owner:
        os.chown(tmp, owner[0], owner[1])
    os.replace(tmp, path)
    return True


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def kiosk_ids():
    pw = pwd.getpwnam(KIOSK_USER)
    return pw.pw_uid, pw.pw_gid, pw.pw_dir


def runtime_dir():
    return f"/run/user/{kiosk_ids()[0]}"


def utcnow():
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# ── Managed files ────────────────────────────────────────────────────────────

AGENT_UNIT = f"""[Unit]
Description=Cove thin client agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 {AGENT_PATH} run
Restart=always
RestartSec=5
# The browser runs in its own transient unit (cove-browser), so restarting or
# updating the agent never kills the session on screen.

[Install]
WantedBy=multi-user.target
"""

GETTY_OVERRIDE = f"""[Service]
ExecStart=
ExecStart=-/sbin/agetty -o '-p -f -- \\\\u' --noclear --autologin {KIOSK_USER} %I $TERM
"""

LOGIND_CONF = """[Login]
# No spare consoles to switch to, and a closed laptop lid doesn't suspend.
NAutoVTs=0
ReserveVT=0
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
"""

JOURNALD_CONF = """[Journal]
SystemMaxUse=200M
"""

BASH_PROFILE = f"""# Managed by cove-thin-client. On tty1 this account runs the kiosk session and
# nothing else; sway has no key bindings, so there is no way out of it.
if [ -z "${{WAYLAND_DISPLAY:-}}" ] && [ "$(tty)" = "/dev/tty1" ]; then
  export XDG_CURRENT_DESKTOP=sway XDG_SESSION_TYPE=wayland
  exec systemd-cat --identifier=cove-session sway --config {SWAY_CONF}
fi
"""

APT_CONF = """// Managed by cove-thin-client.
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
// Reboots happen in the maintenance hour, decided by the agent.
Unattended-Upgrade::Automatic-Reboot "false";
"""

PLYMOUTH_THEME = """[Plymouth Theme]
Name=Cove
Description=Cove thin client
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/cove
ScriptFile=/usr/share/plymouth/themes/cove/cove.script
"""

PLYMOUTH_SCRIPT = """Window.SetBackgroundTopColor(0.024, 0.024, 0.06);
Window.SetBackgroundBottomColor(0.024, 0.024, 0.06);
title = Image.Text("C O V E", 0.0, 0.96, 1.0, 1.0, "Sans Bold 36");
sub = Image.Text("// THIN CLIENT  BOOTING", 1.0, 0.0, 0.67, 1.0, "Mono 13");
t = Sprite(title);
s = Sprite(sub);
cx = Window.GetWidth() / 2;
cy = Window.GetHeight() / 2;
t.SetPosition(cx - title.GetWidth() / 2, cy - title.GetHeight(), 1);
s.SetPosition(cx - sub.GetWidth() / 2, cy + 18, 1);
tick = 0;
fun refresh() {
  tick++;
  s.SetOpacity(0.55 + 0.45 * Math.Sin(tick / 12));
}
Plymouth.SetRefreshFunction(refresh);
"""

OFFLINE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Cove</title>
<style>
html,body{margin:0;height:100%;background:#06060f;color:#c8d8ff;
  font:16px "DejaVu Sans Mono",monospace;overflow:hidden}
body:before{content:"";position:fixed;inset:0;pointer-events:none;
  background:repeating-linear-gradient(0deg,rgba(0,0,0,.25) 0 1px,transparent 1px 3px)}
main{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:18px}
h1{margin:0;font-size:44px;letter-spacing:14px;color:#00f5ff;
  text-shadow:0 0 8px #00f5ff,0 0 28px rgba(0,245,255,.35)}
.state{color:__COLOR__;letter-spacing:4px;text-shadow:0 0 8px __COLOR__}
.box{border:1px solid #1c1c42;background:#0b0b1e;padding:14px 22px;min-width:420px}
.row{display:flex;justify-content:space-between;gap:24px;color:#7d8bb5;font-size:13px;line-height:1.9}
.row b{color:#c8d8ff;font-weight:normal}
.blink{animation:b 1.2s steps(2) infinite}@keyframes b{50%{opacity:0}}
</style></head><body><main>
<h1>COVE</h1>
<div class="state">// __STATE__ <span class="blink">_</span></div>
<div class="box">
<div class="row"><span>DEVICE</span><b>__NAME__</b></div>
<div class="row"><span>HOST</span><b>__HOST__</b></div>
<div class="row"><span>ADDRESS</span><b>__IPS__</b></div>
<div class="row"><span>COVE</span><b>__CP__</b></div>
<div class="row"><span>STATUS</span><b>__DETAIL__</b></div>
</div></main></body></html>
"""


def keyboard_layout():
    layout, variant = "us", ""
    try:
        with open("/etc/default/keyboard") as f:
            for line in f:
                m = re.match(r'^\s*XKB(LAYOUT|VARIANT)="?([^"\n]*)"?', line)
                if m and m.group(1) == "LAYOUT" and m.group(2):
                    layout = m.group(2)
                elif m and m.group(1) == "VARIANT":
                    variant = m.group(2)
    except OSError:
        pass
    return layout, variant


def sway_config():
    layout, variant = keyboard_layout()
    kb = [f"    xkb_layout {layout}"]
    if variant:
        kb.append(f"    xkb_variant {variant}")
    # srvrkeys:none turns Ctrl+Alt+F<n> into plain F-keys: no console switching.
    kb.append("    xkb_options srvrkeys:none")
    kb_block = "\n".join(kb)
    return f"""# Managed by cove-thin-client — local edits are overwritten.
# No key bindings at all: the only thing this session can do is show Cove.
xwayland disable
default_border none
default_floating_border none
hide_edge_borders both
focus_follows_mouse no
seat * hide_cursor 8000
input type:keyboard {{
{kb_block}
}}
input type:touchpad {{
    tap enabled
}}
output * bg #06060f solid_color
client.focused   #00f5ff #06060f #c8d8ff #ff00aa #00f5ff
client.unfocused #1c1c42 #06060f #7d8bb5 #1c1c42 #1c1c42
for_window [app_id=".*"] fullscreen enable
include {SWAY_OUTPUTS}
"""


def chromium_policy(allowed_hosts, cp_url):
    # A path filter is a prefix match; a trailing "*" would be taken literally and
    # leave the agent's own offline page blocked.
    allow = list(allowed_hosts) + [f"file://{SHARE}/"]
    clip = []
    for h in allowed_hosts:
        clip.append(f"https://{h[1:]}" if h.startswith(".") else f"https://[*.]{h}")
    return {
        # Everything is blocked except Cove, its sign-in provider and the
        # workspace domain (plus any hosts the admin added).
        "URLBlocklist": ["*", "chrome://*", "chrome-untrusted://*", "file://*",
                         "view-source:*", "javascript://*", "chrome-extension://*"],
        "URLAllowlist": allow,
        "DeveloperToolsAvailability": 2,
        "IncognitoModeAvailability": 1,
        "BrowserGuestModeEnabled": False,
        "BrowserAddPersonEnabled": False,
        "BrowserSignin": 0,
        "SyncDisabled": True,
        "PasswordManagerEnabled": False,
        "PasswordLeakDetectionEnabled": False,
        "AutofillAddressEnabled": False,
        "AutofillCreditCardEnabled": False,
        "SavingBrowserHistoryDisabled": True,
        "PrintingEnabled": False,
        "DownloadRestrictions": 3,
        "AllowFileSelectionDialogs": False,
        "ExtensionInstallBlocklist": ["*"],
        "BlockExternalExtensions": True,
        "DefaultBrowserSettingEnabled": False,
        "TranslateEnabled": False,
        "SpellcheckEnabled": False,
        "SearchSuggestEnabled": False,
        "MetricsReportingEnabled": False,
        "PromotionalTabsEnabled": False,
        "ShowHomeButton": False,
        "AutoplayAllowed": True,
        "FullscreenAllowed": True,
        "HardwareAccelerationModeEnabled": True,
        "ScreenCaptureAllowed": False,
        "DefaultNotificationsSetting": 2,
        "DefaultGeolocationSetting": 2,
        # Selkies needs clipboard access for copy/paste into the workspace.
        "ClipboardAllowedForUrls": clip,
        "HomepageLocation": f"{cp_url}/app",
        "HomepageIsNewTabPage": False,
    }


def converge(cached=None):
    """Bring every managed file to its desired content. Idempotent and cheap:
    files are only rewritten (and services only reloaded) when they differ."""
    cfg = read_json(CONFIG, {}) or {}
    cached = cached if cached is not None else read_json(CACHE_FILE, {}) or {}
    ensure_kiosk_user()
    uid, gid, home = kiosk_ids()
    # The kiosk's sway reads its config from ETC; the secrets in it stay root-only.
    os.makedirs(ETC, exist_ok=True)
    os.chmod(ETC, 0o755)
    for secret in (TOKEN_FILE, CONFIG):
        if os.path.exists(secret):
            os.chmod(secret, 0o600)
    changed_units = False
    changed_units |= write_if_changed("/etc/systemd/system/cove-thin-client.service", AGENT_UNIT)
    changed_units |= write_if_changed("/etc/systemd/system/getty@tty1.service.d/cove-autologin.conf", GETTY_OVERRIDE)
    write_if_changed("/etc/systemd/logind.conf.d/cove-thin-client.conf", LOGIND_CONF)
    write_if_changed("/etc/systemd/journald.conf.d/cove-thin-client.conf", JOURNALD_CONF)
    write_if_changed(f"{home}/.bash_profile", BASH_PROFILE, 0o644)
    write_if_changed(SWAY_CONF, sway_config())
    if not os.path.exists(SWAY_OUTPUTS):
        write_if_changed(SWAY_OUTPUTS, "# Written by cove-thin-client from the device policy.\n")
    write_if_changed("/etc/apt/apt.conf.d/52cove-thin-client", APT_CONF)
    write_if_changed("/usr/share/plymouth/themes/cove/cove.plymouth", PLYMOUTH_THEME)
    write_if_changed("/usr/share/plymouth/themes/cove/cove.script", PLYMOUTH_SCRIPT)
    hosts = cached.get("allowed_hosts") or []
    cp = cfg.get("cp_url", "")
    if not hosts and cp:
        hosts = ["." + re.sub(r"^https?://([^/:]+).*$", r"\1", cp)]
    write_if_changed(CHROMIUM_POLICY, json.dumps(chromium_policy(hosts, cp), indent=2) + "\n")
    os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
    os.makedirs(STATE_DIR, mode=0o755, exist_ok=True)
    if changed_units:
        run(["systemctl", "daemon-reload"])
    import_local_cas(uid, gid, home)


def ensure_kiosk_user():
    try:
        pwd.getpwnam(KIOSK_USER)
        return
    except KeyError:
        pass
    groups = [g for g in ("video", "audio", "input", "render") if _group_exists(g)]
    cmd = ["useradd", "--create-home", "--shell", "/bin/bash", "--comment", "Cove kiosk"]
    if groups:
        cmd += ["--groups", ",".join(groups)]
    run(cmd + [KIOSK_USER], check=True)
    run(["passwd", "--lock", KIOSK_USER])


def _group_exists(name):
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def import_local_cas(uid, gid, home):
    """Chromium on Linux trusts its NSS database, not /etc/ssl. Mirror any CA the
    admin added under /usr/local/share/ca-certificates (a private Cove CA) into
    the kiosk's NSS database."""
    cas = sorted(glob.glob("/usr/local/share/ca-certificates/**/*.crt", recursive=True))
    if not cas or not shutil.which("certutil"):
        return
    db = f"{home}/.pki/nssdb"
    marker = f"{STATE_DIR}/nss-cas.sha256"
    h = hashlib.sha256()
    for c in cas:
        with open(c, "rb") as f:
            h.update(c.encode() + b"\0" + f.read())
    digest = h.hexdigest()
    try:
        if open(marker).read().strip() == digest and os.path.isdir(db):
            return
    except OSError:
        pass
    os.makedirs(db, exist_ok=True)
    if not os.path.exists(f"{db}/cert9.db"):
        run(["certutil", "-N", "-d", f"sql:{db}", "--empty-password"])
    for ca in cas:
        name = "cove-local-" + os.path.basename(ca)
        run(["certutil", "-A", "-d", f"sql:{db}", "-t", "C,,", "-n", name, "-i", ca])
    for root, dirs, files in os.walk(f"{home}/.pki"):
        os.chown(root, uid, gid)
        for f in files:
            os.chown(os.path.join(root, f), uid, gid)
    write_if_changed(marker, digest + "\n")


def install():
    """First-time setup (the install script runs this). Safe to re-run."""
    if os.geteuid() != 0:
        sys.exit("install must run as root")
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    print(">> apt: installing kiosk packages", flush=True)
    run(["apt-get", "update", "-qq"], timeout=600, env=env)
    rc, out = run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", *PACKAGES], timeout=3600, env=env)
    if rc != 0:
        sys.exit(f"Package install failed:\n{out[-4000:]}")
    print(">> writing kiosk configuration", flush=True)
    converge()
    setup_boot_splash()
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "cove-thin-client.service"])
    run(["systemctl", "restart", "cove-thin-client.service"])
    on_tty1 = os.ttyname(0) == "/dev/tty1" if os.isatty(0) else False
    if on_tty1:
        print(">> reboot to start the kiosk session", flush=True)
    else:
        run(["systemctl", "restart", "getty@tty1.service"])
        print(">> kiosk session started on the console", flush=True)


def setup_boot_splash():
    """Cove's boot splash and a quiet, fast boot. Done once."""
    marker = f"{STATE_DIR}/boot-splash.done"
    if os.path.exists(marker) or not shutil.which("plymouth-set-default-theme"):
        return
    run(["plymouth-set-default-theme", "cove"])
    grub = "/etc/default/grub"
    if os.path.exists(grub):
        text = open(grub).read()
        text = re.sub(r"^GRUB_TIMEOUT=.*$", "GRUB_TIMEOUT=1", text, flags=re.M)
        m = re.search(r'^GRUB_CMDLINE_LINUX_DEFAULT="([^"]*)"', text, flags=re.M)
        if m and "splash" not in m.group(1).split():
            opts = " ".join([*m.group(1).split(), "splash"]).strip()
            text = text.replace(m.group(0), f'GRUB_CMDLINE_LINUX_DEFAULT="{opts}"')
        write_if_changed(grub, text)
        run(["update-grub"], timeout=300)
    run(["update-initramfs", "-u"], timeout=600)
    os.makedirs(STATE_DIR, exist_ok=True)
    open(marker, "w").close()


# ── Sway / display ───────────────────────────────────────────────────────────

class Sway:
    """Minimal i3/sway IPC client over the kiosk session's socket."""

    RUN_COMMAND, GET_OUTPUTS, GET_VERSION = 0, 3, 7

    def socket_path(self):
        try:
            uid = kiosk_ids()[0]
        except KeyError:
            return None
        socks = []
        for path in glob.glob(f"/run/user/{uid}/sway-ipc.{uid}.*.sock"):
            m = re.search(r"\.(\d+)\.sock$", path)
            if m and os.path.exists(f"/proc/{m.group(1)}"):
                socks.append(path)
        return max(socks, key=os.path.getmtime) if socks else None

    def wayland_display(self):
        try:
            uid = kiosk_ids()[0]
        except KeyError:
            return None
        for path in sorted(glob.glob(f"/run/user/{uid}/wayland-[0-9]*")):
            if not path.endswith(".lock"):
                return os.path.basename(path)
        return None

    def available(self):
        return self.socket_path() is not None and self.wayland_display() is not None

    def _call(self, msg_type, payload=""):
        path = self.socket_path()
        if not path:
            raise RuntimeError("sway is not running")
        data = payload.encode()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(path)
            s.sendall(b"i3-ipc" + struct.pack("=II", len(data), msg_type) + data)
            head = self._recv(s, 14)
            length, _ = struct.unpack("=II", head[6:])
            return json.loads(self._recv(s, length))

    @staticmethod
    def _recv(s, n):
        buf = b""
        while len(buf) < n:
            chunk = s.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("sway closed the IPC socket")
            buf += chunk
        return buf

    def command(self, cmd):
        res = self._call(self.RUN_COMMAND, cmd)
        errors = [r.get("error") for r in res if not r.get("success")]
        if errors:
            raise RuntimeError("; ".join(e or "failed" for e in errors))
        return res

    def outputs(self):
        return self._call(self.GET_OUTPUTS)

    def version(self):
        try:
            return self._call(self.GET_VERSION).get("human_readable")
        except Exception:  # noqa: BLE001
            return None

    def set_power(self, on):
        word = "on" if on else "off"
        try:
            self.command(f"output * power {word}")
        except RuntimeError:
            self.command(f"output * dpms {word}")  # sway < 1.8

    def is_on(self):
        try:
            outs = self.outputs()
        except Exception:  # noqa: BLE001
            return None
        active = [o for o in outs if o.get("active")]
        if not active:
            return None
        return any(o.get("power", o.get("dpms", True)) for o in active)


def brightness(level):
    """Set brightness 0-100 via a laptop backlight or the monitor's DDC/CI."""
    done = []
    for dev in glob.glob("/sys/class/backlight/*"):
        try:
            mx = int(open(f"{dev}/max_brightness").read())
            with open(f"{dev}/brightness", "w") as f:
                f.write(str(max(1, round(mx * level / 100))))
            done.append(os.path.basename(dev))
        except (OSError, ValueError):
            pass
    if not done and shutil.which("ddcutil"):
        rc, out = run(["ddcutil", "--noverify", "setvcp", "10", str(level)], timeout=20)
        if rc == 0:
            done.append("ddc")
    return done


# ── Activity (idle) ──────────────────────────────────────────────────────────

class Activity(threading.Thread):
    """Tracks the last human input (keyboard, mouse, touch) from evdev, plus
    ``agent.py poke`` (the activity file) and, optionally, audio playback."""

    def __init__(self, on_input):
        super().__init__(daemon=True, name="activity")
        self.last = time.monotonic()
        self.on_input = on_input
        self._fds = {}

    @staticmethod
    def input_devices():
        devices = []
        try:
            text = open("/proc/bus/input/devices").read()
        except OSError:
            return devices
        for block in text.split("\n\n"):
            m = re.search(r"^H: Handlers=(.*)$", block, re.M)
            if not m:
                continue
            handlers = m.group(1).split()
            if not any(h == "kbd" or h.startswith("mouse") for h in handlers):
                continue
            for h in handlers:
                if h.startswith("event"):
                    devices.append(f"/dev/input/{h}")
        return devices

    def _rescan(self):
        wanted = set(self.input_devices())
        for path in list(self._fds):
            if path not in wanted:
                os.close(self._fds.pop(path))
        for path in wanted - set(self._fds):
            try:
                self._fds[path] = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                pass

    def touch(self):
        self.last = time.monotonic()
        self.on_input()

    def run(self):
        last_scan = 0.0
        file_mtime = 0.0
        while True:
            now = time.monotonic()
            if now - last_scan > 5:
                self._rescan()
                last_scan = now
            fds = list(self._fds.values())
            ready = []
            if fds:
                try:
                    ready, _, _ = select.select(fds, [], [], 1.0)
                except (OSError, ValueError):
                    self._rescan()
            else:
                time.sleep(1.0)
            hit = False
            for fd in ready:
                try:
                    while os.read(fd, 4096):
                        hit = True
                except BlockingIOError:
                    pass
                except OSError:
                    last_scan = 0.0
            try:
                m = os.stat(ACTIVITY_FILE).st_mtime
                if m != file_mtime:
                    if file_mtime:
                        hit = True
                    file_mtime = m
            except OSError:
                pass
            if hit:
                self.touch()

    def idle_seconds(self):
        return time.monotonic() - self.last


def audio_playing():
    for status in glob.glob("/proc/asound/card*/pcm*p/sub*/status"):
        try:
            if "RUNNING" in open(status).read():
                return True
        except OSError:
            pass
    return False


# ── Browser ──────────────────────────────────────────────────────────────────

class Browser:
    """Chromium as the kiosk user, in a transient systemd unit so it survives
    agent restarts and logs to the journal (journalctl -u cove-browser)."""

    def __init__(self, sway):
        self.sway = sway
        self.page = None  # "cove" | "offline"
        # Server time the launch ticket was issued at; the launch counts as bound
        # once Cove reports a binding at or after it (server clock on both sides,
        # so device clock skew can't matter).
        self.ticket_issued_at = None
        self.launched_mono = None
        self.version = None

    def adopt(self):
        """Pick up a browser a previous agent process started."""
        saved = read_json(BROWSER_STATE, {}) or {}
        self.page = saved.get("page") or "cove"
        self.ticket_issued_at = None  # already bound (or it would have been restarted)
        self.launched_mono = time.monotonic()

    def running(self):
        rc, out = run(["systemctl", "is-active", BROWSER_UNIT], timeout=10)
        return out.strip() in ("active", "activating", "reloading")

    def stop(self):
        run(["systemctl", "stop", BROWSER_UNIT], timeout=30)
        run(["systemctl", "reset-failed", BROWSER_UNIT], timeout=10)

    def wipe_profile(self):
        uid, gid, _ = kiosk_ids()
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        os.makedirs(PROFILE_DIR, mode=0o700)
        os.chown(PROFILE_DIR, uid, gid)

    def launch(self, url, page, ticket_issued_at=None):
        uid, gid, home = kiosk_ids()
        display = self.sway.wayland_display()
        if not display:
            raise RuntimeError("no Wayland session yet")
        rt = f"/run/user/{uid}"
        groups = " ".join(g for g in ("audio", "video", "render", "input") if _group_exists(g))
        cmd = [
            "systemd-run", f"--unit={BROWSER_UNIT}", "--collect", "--quiet",
            f"--uid={uid}", f"--gid={gid}",
            "-p", f"SupplementaryGroups={groups}",
            "-p", f"WorkingDirectory={home}",
            "-E", f"HOME={home}", "-E", f"USER={KIOSK_USER}", "-E", f"LOGNAME={KIOSK_USER}",
            "-E", f"XDG_RUNTIME_DIR={rt}", "-E", f"WAYLAND_DISPLAY={display}",
            "-E", f"DBUS_SESSION_BUS_ADDRESS=unix:path={rt}/bus",
            "-E", "XDG_SESSION_TYPE=wayland",
            "--", "/usr/bin/chromium", *self.flags(), url,
        ]
        run(["systemctl", "reset-failed", BROWSER_UNIT], timeout=10)
        rc, out = run(cmd, timeout=30)
        if rc != 0:
            raise RuntimeError(f"browser launch failed: {out.strip()[-500:]}")
        self.page = page
        self.ticket_issued_at = ticket_issued_at
        self.launched_mono = time.monotonic()
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        write_if_changed(BROWSER_STATE, json.dumps({"page": page}), 0o600)

    @staticmethod
    def flags():
        extra = os.environ.get("COVE_TC_CHROMIUM_FLAGS", "").split()
        return [
            "--kiosk", "--no-first-run", "--noerrdialogs", "--disable-infobars",
            "--disable-session-crashed-bubble", "--disable-pinch",
            "--overscroll-history-navigation=0", "--password-store=basic",
            f"--user-data-dir={PROFILE_DIR}", "--disk-cache-size=268435456",
            "--ozone-platform=wayland", "--class=cove-kiosk",
            "--autoplay-policy=no-user-gesture-required",
            "--enable-features=AcceleratedVideoDecodeLinuxGL",
            "--disable-features=Translate,MediaRouter",
            "--check-for-update-interval=31536000",
            *extra,
        ]

    def detect_version(self):
        if self.version is None:
            rc, out = run(["/usr/bin/chromium", "--version"], timeout=20)
            self.version = out.strip() if rc == 0 else None
        return self.version


# ── Cove API ─────────────────────────────────────────────────────────────────

class Revoked(Exception):
    pass


class Api:
    def __init__(self, cp_url, token):
        self.cp = cp_url.rstrip("/")
        self.token = token

    def request(self, method, path, body=None, *, timeout=30, raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": UA}
        if data is not None:
            headers["Content-Type"] = ctype
        req = urllib.request.Request(self.cp + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                return payload, r.headers
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise Revoked() from e
            raise

    def json(self, method, path, body=None, **kw):
        payload, _ = self.request(method, path, body, **kw)
        return json.loads(payload) if payload else None


# ── The agent ────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self):
        self.cfg = read_json(CONFIG, {}) or {}
        with open(TOKEN_FILE) as f:
            token = f.read().strip()
        self.api = Api(self.cfg["cp_url"], token)
        self.sway = Sway()
        self.browser = Browser(self.sway)
        self.lock = threading.RLock()
        self.kick = threading.Event()
        self.activity = Activity(self._on_input)
        last = read_json(CACHE_FILE, {}) or {}
        self.policy = last.get("policy") or {}
        self.policy_version = None  # forces a re-apply on the first check-in
        self.allowed_hosts = last.get("allowed_hosts") or []
        self.keep_awake_until = None
        self.session_user = None
        self.bound_at = None
        self.connected = False
        self.revoked = False
        self.display_on = True
        self.display_synced = False
        self.forced_off = False
        self.idle_signed_out = False
        self.last_error = None
        self.apt_lock = threading.Lock()
        self.upgradable = None
        self.upgradable_at = 0.0
        self.last_update_at = (read_json(MAINT_FILE, {}) or {}).get("last_update_at")
        self.agent_sha = sha256_file(os.path.abspath(__file__))
        self.update_failed_at = 0.0

    # Activity / display

    def _on_input(self):
        self.forced_off = False
        self.idle_signed_out = False
        if not self.display_on:
            self.set_display(True)

    def set_display(self, on):
        try:
            self.sway.set_power(on)
            self.display_on = on
            self.kick.set()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"display: {exc}"

    def effective_idle(self):
        if self.keep_awake_until and utcnow() < self.keep_awake_until:
            return 0.0
        if self.policy.get("media_keeps_awake", True) and audio_playing():
            self.activity.last = time.monotonic()
            return 0.0
        return self.activity.idle_seconds()

    def in_on_hours(self):
        start, end = self.policy.get("on_hours_start"), self.policy.get("on_hours_end")
        if not start or not end:
            return True
        now = dt.datetime.now().strftime("%H:%M")
        return start <= now < end if start <= end else (now >= start or now < end)

    def display_loop(self):
        while True:
            time.sleep(1)
            try:
                self._display_tick()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"display loop: {exc}"

    def _display_tick(self):
        if not self.sway.available():
            self.display_synced = False
            return
        if not self.display_synced:
            on = self.sway.is_on()
            if on is not None:
                self.display_on = on
                self.display_synced = True
        idle = self.effective_idle()
        off_after = int(self.policy.get("screen_off_minutes", 15)) * 60
        if not self.in_on_hours():
            off_after = 60
        if self.display_on and (self.forced_off or (off_after and idle >= off_after)):
            self.set_display(False)
        elif not self.display_on and not self.forced_off and (not off_after or idle < off_after):
            self.set_display(True)
        signout_after = int(self.policy.get("signout_minutes", 30)) * 60
        if signout_after and self.session_user and not self.idle_signed_out and idle >= signout_after:
            log.info("idle for %ds: signing %s out", idle, self.session_user)
            self.idle_signed_out = True
            self.reset_browser("idle")

    # Browser

    def reset_browser(self, reason):
        """Stop the browser, wipe its profile and start a fresh, bound one. Tells
        Cove first, so the old session's tokens are revoked even if the profile
        wipe somehow missed something."""
        with self.lock:
            try:
                self.api.request("POST", "/api/thin-client/signed-out", {}, timeout=10)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not report sign-out (%s): %s", reason, exc)
            self.session_user = None
            self.browser.stop()
            self.browser.wipe_profile()
            self._launch()

    def _launch(self):
        if not self.sway.available():
            return
        if self.revoked:
            self._launch_offline("DEVICE REVOKED", "Re-enrol this device from Admin / Thin clients")
            return
        try:
            ticket = self.api.json("POST", "/api/thin-client/ticket", {}, timeout=15)
            self.browser.launch(ticket["url"], "cove", parse_ts(ticket.get("issued_at")))
            log.info("browser launched on Cove")
        except Revoked:
            self.revoked = True
            self._launch_offline("DEVICE REVOKED", "Re-enrol this device from Admin / Thin clients")
        except Exception as exc:  # noqa: BLE001
            log.warning("Cove unreachable, showing the offline page: %s", exc)
            self._launch_offline("LINK DOWN", "Cove is unreachable; retrying")

    def _launch_offline(self, state, detail):
        color = "#ff2055" if "REVOKED" in state else "#ffaa00"
        page = OFFLINE_HTML
        for k, v in {
            "__STATE__": state, "__DETAIL__": detail, "__COLOR__": color,
            "__NAME__": self.cfg.get("name", "?"), "__HOST__": socket.gethostname(),
            "__IPS__": ", ".join(local_ips()) or "none", "__CP__": self.api.cp,
        }.items():
            page = page.replace(k, html_escape(v))
        write_if_changed(OFFLINE_PAGE, page)
        try:
            self.browser.launch(f"file://{OFFLINE_PAGE}", "offline")
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"browser: {exc}"

    def unbound(self):
        """True while a ticket launch hasn't been confirmed bound by Cove."""
        t = self.browser.ticket_issued_at
        return self.browser.page == "cove" and t is not None and (self.bound_at is None or self.bound_at < t)

    def browser_loop(self):
        backoff = 2
        adopted = False
        while True:
            time.sleep(backoff)
            try:
                if not self.sway.available():
                    continue
                if not adopted:
                    adopted = True
                    if self.browser.running():
                        log.info("adopting the running browser")
                        self.browser.adopt()
                        continue
                # Every decision is re-checked under the lock: a reset (idle
                # sign-out, a command) may be stopping/starting the browser now.
                with self.lock:
                    if not self.browser.running():
                        log.info("browser not running: starting it")
                        self.browser.wipe_profile()
                        self._launch()
                        backoff = min(backoff * 2, 60)
                        continue
                    backoff = 2
                    if self.browser.page == "offline" and self.connected and not self.revoked:
                        log.info("Cove is reachable again: leaving the offline page")
                        self._relaunch()
                    elif (
                        self.unbound() and self.connected
                        and time.monotonic() - (self.browser.launched_mono or 0) > 45
                    ):
                        # Fail closed: a kiosk browser that never bound to its
                        # device would create unscoped sessions.
                        log.warning("browser never bound to Cove; restarting it")
                        self._relaunch()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"browser loop: {exc}"

    def _relaunch(self):
        self.browser.stop()
        self.browser.wipe_profile()
        self._launch()

    # Policy

    def apply_policy(self, resp):
        new_policy = resp.get("policy") or {}
        old = self.policy
        self.policy = new_policy
        self.keep_awake_until = parse_ts(resp.get("keep_awake_until"))
        hosts = resp.get("allowed_hosts") or []
        if hosts != self.allowed_hosts or not os.path.exists(CHROMIUM_POLICY):
            self.allowed_hosts = hosts
            write_if_changed(CHROMIUM_POLICY, json.dumps(chromium_policy(hosts, self.api.cp), indent=2) + "\n")
        self.apply_outputs(new_policy)
        if new_policy.get("brightness") is not None and new_policy.get("brightness") != old.get("brightness"):
            threading.Thread(target=brightness, args=(int(new_policy["brightness"]),), daemon=True).start()
        if new_policy.get("volume") is not None and new_policy.get("volume") != old.get("volume"):
            self.set_volume(int(new_policy["volume"]))
        write_if_changed(CACHE_FILE, json.dumps({"policy": new_policy, "allowed_hosts": hosts}))

    def apply_outputs(self, policy):
        rotation = policy.get("rotation") or "normal"
        scale = policy.get("scale")
        lines = ["# Written by cove-thin-client from the device policy."]
        lines.append(f"output * transform {rotation}")
        if scale:
            lines.append(f"output * scale {float(scale):g}")
        write_if_changed(SWAY_OUTPUTS, "\n".join(lines) + "\n")
        if self.sway.available():
            try:
                self.sway.command(f"output * transform {rotation}")
                if scale:
                    self.sway.command(f"output * scale {float(scale):g}")
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"outputs: {exc}"

    def set_volume(self, level):
        uid = kiosk_ids()[0]
        env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{uid}")
        return run(["runuser", "-u", KIOSK_USER, "--", "wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@",
                    f"{level / 100:.2f}"], env=env, timeout=10)

    # State

    def state(self):
        outs = []
        if self.sway.available():
            try:
                for o in self.sway.outputs():
                    mode = o.get("current_mode") or {}
                    outs.append({
                        "name": o.get("name"), "make": o.get("make"), "model": o.get("model"),
                        "active": o.get("active"), "power": o.get("power", o.get("dpms")),
                        "mode": output_mode(mode),
                        "transform": o.get("transform"), "scale": o.get("scale"),
                    })
            except Exception:  # noqa: BLE001
                pass
        osr = {}
        try:
            for line in open("/etc/os-release"):
                k, _, v = line.strip().partition("=")
                osr[k] = v.strip('"')
        except OSError:
            pass
        try:
            uptime = int(float(open("/proc/uptime").read().split()[0]))
        except (OSError, ValueError):
            uptime = None
        self.refresh_upgradable()
        return {
            "hostname": socket.gethostname(),
            "ips": local_ips(),
            "os": osr.get("PRETTY_NAME"),
            "kernel": os.uname().release,
            "agent_version": AGENT_VERSION,
            "browser_version": self.browser.detect_version(),
            "sway_version": self.sway.version() if self.sway.available() else None,
            "session_running": self.sway.available(),
            "uptime_s": uptime,
            "display_on": self.display_on,
            "idle_s": int(self.activity.idle_seconds()),
            "media_playing": audio_playing(),
            "browser_running": self.browser.running(),
            "browser_page": self.browser.page,
            "outputs": outs,
            "upgradable": self.upgradable,
            "reboot_required": os.path.exists("/run/reboot-required"),
            "last_update_at": self.last_update_at,
            "last_error": self.last_error,
        }

    def refresh_upgradable(self, force=False):
        if not force and time.monotonic() - self.upgradable_at < 6 * 3600 and self.upgradable is not None:
            return
        if not self.apt_lock.acquire(blocking=False):
            return
        try:
            rc, out = run(["apt-get", "-s", "-o", "Debug::NoLocking=1", "dist-upgrade"], timeout=120)
            if rc == 0:
                self.upgradable = sum(1 for line in out.splitlines() if line.startswith("Inst "))
                self.upgradable_at = time.monotonic()
        finally:
            self.apt_lock.release()

    # Check-in

    def checkin(self, wait):
        body = {"state": self.state(), "applied_policy_version": self.policy_version, "wait": wait}
        resp = self.api.json("POST", "/api/thin-client/checkin", body, timeout=wait + 20)
        self.connected = True
        if self.revoked:
            self.revoked = False
            self.browser.page = "offline"  # leave the revoked page on the next tick
        self.handle(resp)
        return resp

    def handle(self, resp):
        with self.lock:
            self.session_user = resp.get("session_username")
            self.bound_at = parse_ts(resp.get("bound_at"))
            if resp.get("name") and resp["name"] != self.cfg.get("name"):
                self.cfg["name"] = resp["name"]
                write_if_changed(CONFIG, json.dumps(self.cfg, indent=2), 0o600)
            if resp.get("policy_version") != self.policy_version:
                self.apply_policy(resp)
                self.policy_version = resp.get("policy_version")
            for cmd in resp.get("commands") or []:
                threading.Thread(target=self.execute, args=(cmd,), daemon=True, name=f"cmd-{cmd['id']}").start()
        self.maybe_self_update(resp)

    def checkin_loop(self):
        backoff = 2
        while True:
            try:
                # Poll briskly while a fresh browser waits to bind; otherwise long-poll.
                binding = self.unbound()
                self.checkin(0 if binding else LONG_POLL)
                backoff = 2
                if binding:
                    time.sleep(3)
            except Revoked:
                self.connected = False
                if not self.revoked:
                    log.error("Cove rejected this device's token (deleted or re-enrolled)")
                    self.revoked = True
                    with self.lock:
                        self._relaunch()
                time.sleep(60)
            except Exception as exc:  # noqa: BLE001
                if self.connected:
                    log.warning("lost Cove: %s", exc)
                self.connected = False
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def report_loop(self):
        """Push state promptly when something changes (screen on/off etc.)."""
        while True:
            self.kick.wait()
            time.sleep(1)
            self.kick.clear()
            if not self.connected:
                continue
            try:
                self.checkin(0)
            except Exception:  # noqa: BLE001
                pass

    # Commands

    def execute(self, cmd):
        kind, args = cmd.get("kind"), cmd.get("args") or {}
        try:
            ok, output = self._execute(kind, args)
        except Exception as exc:  # noqa: BLE001
            ok, output = False, f"{type(exc).__name__}: {exc}"
        try:
            self.api.request("POST", f"/api/thin-client/commands/{cmd['id']}/result",
                             {"ok": bool(ok), "output": str(output)[-190000:]}, timeout=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not report result of %s: %s", kind, exc)
        self.kick.set()

    def _execute(self, kind, args):
        if kind == "screen_on":
            self.forced_off = False
            self.activity.touch()
            self.set_display(True)
            return True, "screen on"
        if kind == "screen_off":
            self.forced_off = True
            self.set_display(False)
            return True, "screen off until the next input"
        if kind == "restart_browser":
            self.reset_browser(args.get("reason") or "admin")
            return True, "browser restarted on a fresh profile"
        if kind == "identify":
            return self.identify(int(args.get("seconds", 10)))
        if kind == "screenshot":
            return self.screenshot()
        if kind == "apt_upgrade":
            return self.apt_upgrade()
        if kind == "reboot":
            threading.Timer(3, lambda: run(["systemctl", "reboot"])).start()
            return True, "rebooting"
        if kind == "poweroff":
            threading.Timer(3, lambda: run(["systemctl", "poweroff"])).start()
            return True, "powering off"
        if kind == "logs":
            lines = str(int(args.get("lines", 200)))
            unit = args.get("unit", "agent")
            sel = {
                "agent": ["-u", "cove-thin-client", "-u", BROWSER_UNIT],
                "session": ["-t", "cove-session"],
                "system": ["-b", "-p", "warning"],
            }.get(unit, ["-u", "cove-thin-client"])
            rc, out = run(["journalctl", "--no-pager", "-o", "short-iso", "-n", lines, *sel], timeout=30)
            return rc == 0, out
        return False, f"unknown command {kind!r}"

    def identify(self, seconds):
        self.forced_off = False
        self.activity.touch()
        self.set_display(True)
        # The text goes through sway's `exec` (a shell): keep it to inert characters.
        raw = f"COVE // {self.cfg.get('name', 'thin client').upper()} // {socket.gethostname()} // "
        raw += ", ".join(local_ips())
        msg = re.sub(r"[^A-Za-z0-9 ._/:,()-]", "", raw)[:200]
        self.sway.command(
            f'exec swaynag --layer overlay -t warning -m "{msg}" '
            "--background 06060f --text 00f5ff --border 00f5ff --border-bottom ff00aa "
            "--button-background 0b0b1e --button-text c8d8ff --font 'DejaVu Sans Mono 16'"
        )
        threading.Timer(seconds, lambda: run(["pkill", "-u", KIOSK_USER, "-x", "swaynag"])).start()
        return True, f"showing the device banner for {seconds}s"

    def screenshot(self):
        uid = kiosk_ids()[0]
        display = self.sway.wayland_display()
        if not display:
            return False, "no session on screen"
        cmd = ["grim", "-t", "png"]
        try:
            widest = max((o.get("current_mode") or {}).get("width") or 0 for o in self.sway.outputs())
        except Exception:  # noqa: BLE001
            widest = 0
        if widest > 2000:
            cmd += ["-s", "0.5"]
        env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{uid}", WAYLAND_DISPLAY=display)
        try:
            png = subprocess.run([*cmd, "-"], capture_output=True, timeout=30, env=env)
        except FileNotFoundError:
            return False, "grim is not installed"
        if png.returncode != 0 or not png.stdout:
            return False, (png.stderr or b"").decode(errors="replace")[-500:] or "grim failed"
        self.api.request("POST", "/api/thin-client/screenshot", raw=png.stdout, ctype="image/png", timeout=60)
        return True, f"screenshot uploaded ({len(png.stdout) // 1024} KiB)"

    def apt_upgrade(self):
        if not self.apt_lock.acquire(timeout=5):
            return False, "another package operation is running"
        try:
            env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
            out = []
            for cmd in (
                ["apt-get", "update"],
                ["apt-get", "-y", "-o", "Dpkg::Options::=--force-confdef",
                 "-o", "Dpkg::Options::=--force-confold", "full-upgrade"],
                ["apt-get", "-y", "autoremove", "--purge"],
            ):
                rc, text = run(cmd, timeout=3600, env=env)
                out.append(f"$ {' '.join(cmd)}\n{text[-20000:]}")
                if rc != 0:
                    return False, "\n".join(out)
            self.last_update_at = utcnow().isoformat()
            write_if_changed(MAINT_FILE, json.dumps({"last_update_at": self.last_update_at}))
        finally:
            self.apt_lock.release()
        self.refresh_upgradable(force=True)
        self.browser.version = None
        reboot = os.path.exists("/run/reboot-required")
        return True, "\n".join(out) + ("\n\nA reboot is required." if reboot else "")

    def maintenance_loop(self):
        """Nightly updates (and, if allowed, the reboot they need) in the
        maintenance hour, but never while someone is signed in."""
        while True:
            time.sleep(300)
            try:
                p = self.policy
                now = dt.datetime.now()
                if not p.get("auto_update", True) or now.hour != int(p.get("maintenance_hour", 4)):
                    continue
                last = parse_ts(self.last_update_at)
                if last and (utcnow() - last) < dt.timedelta(hours=20):
                    continue
                if self.session_user:
                    continue
                ok, out = self.apt_upgrade()
                log.info("nightly update: %s", "ok" if ok else out[-500:])
                if ok and p.get("auto_reboot") and os.path.exists("/run/reboot-required") and not self.session_user:
                    log.info("rebooting for the update")
                    run(["systemctl", "reboot"])
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"maintenance: {exc}"

    # Self-update

    def maybe_self_update(self, resp):
        want = resp.get("agent_sha256")
        if not want or want == self.agent_sha or not self.cfg.get("allow_agent_update", True):
            return
        if time.monotonic() - self.update_failed_at < 600:
            return
        try:
            data, headers = self.api.request("GET", "/api/thin-client/agent", timeout=60)
            if hashlib.sha256(data).hexdigest() != headers.get("X-Agent-Sha256", ""):
                raise RuntimeError("checksum mismatch")
            compile(data, AGENT_PATH, "exec")  # refuse a file that doesn't even parse
            tmp = AGENT_PATH + ".new"
            with open(tmp, "wb") as f:
                f.write(data)
            os.chmod(tmp, 0o755)
            os.replace(tmp, AGENT_PATH)
        except Exception as exc:  # noqa: BLE001
            self.update_failed_at = time.monotonic()
            self.last_error = f"agent update: {exc}"
            log.warning("agent update failed: %s", exc)
            return
        log.info("agent updated to %s; restarting", headers.get("X-Agent-Version"))
        os.execv(sys.executable, [sys.executable, AGENT_PATH, "run"])

    # Main

    def run(self):
        log.info("cove-thin-client %s starting (Cove at %s)", AGENT_VERSION, self.api.cp)
        try:
            converge()
        except Exception as exc:  # noqa: BLE001
            log.warning("converge failed: %s", exc)
        threading.Thread(target=self.ensure_packages, daemon=True, name="packages").start()
        self.activity.start()
        for target in (self.checkin_loop, self.report_loop, self.display_loop,
                       self.browser_loop, self.maintenance_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()
        while True:
            time.sleep(3600)

    def ensure_packages(self):
        """Install packages a newer agent needs that this device lacks."""
        rc, out = run(["dpkg-query", "-W", "-f", "${Package} ${db:Status-Status}\n", *PACKAGES], timeout=60)
        have = {line.split()[0] for line in out.splitlines() if line.endswith(" installed")}
        missing = [p for p in PACKAGES if p not in have]
        if not missing:
            return
        with self.apt_lock:
            log.info("installing missing packages: %s", " ".join(missing))
            env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
            run(["apt-get", "update", "-qq"], timeout=600, env=env)
            rc, out = run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", *missing],
                          timeout=3600, env=env)
            if rc != 0:
                self.last_error = f"packages: {out[-300:]}"


def output_mode(mode):
    """'1920x1080@60' (refresh omitted when the output doesn't report one)."""
    if not mode or not mode.get("width"):
        return None
    hz = round((mode.get("refresh") or 0) / 1000)
    return f"{mode['width']}x{mode['height']}" + (f"@{hz}" if hz else "")


def local_ips():
    rc, out = run(["ip", "-o", "-4", "addr", "show", "scope", "global"], timeout=5)
    ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out) if rc == 0 else []
    return ips[:4]


def html_escape(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main(argv):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)
    cmd = argv[1] if len(argv) > 1 else "run"
    if cmd == "version":
        print(AGENT_VERSION)
    elif cmd == "install":
        install()
    elif cmd == "converge":
        converge()
    elif cmd == "poke":
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        with open(ACTIVITY_FILE, "a"):
            os.utime(ACTIVITY_FILE)
    elif cmd == "status":
        print(json.dumps({"config": {k: v for k, v in (read_json(CONFIG, {}) or {}).items()},
                          "cached": read_json(CACHE_FILE, {})}, indent=2))
    elif cmd == "run":
        Agent().run()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
