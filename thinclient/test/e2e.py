#!/usr/bin/env python3
"""End-to-end test of the thin client flow, in containers.

Starts a real Cove container and a plain Debian 13 "device" container booting
systemd, then drives the whole life of a thin client:

  enrol with the real install one-liner -> kiosk browser binds -> a user signs
  in by typing into the kiosk -> idle screen-off and idle sign-out (only on the
  kiosk) -> commands (screenshot, identify, logs) -> policy (rotation) -> agent
  self-update without dropping the browser -> deletion cuts the device off.

The device has no GPU or console, so sway runs on a headless output and the
test types with wtype; everything else is what a real install does.

Usage (from the repo root, with Docker):
    docker build -t cove:tc-e2e .
    python3 thinclient/test/e2e.py [--image cove:tc-e2e] [--keep] [--shots DIR]
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
NET = "cove-tc-e2e"
COVE = "cove-tc-e2e-cove"
DEVICE = "cove-tc-e2e-device"
DEVICE_IMAGE = "cove-tc-e2e-device:latest"
PORT = int(os.environ.get("COVE_TC_E2E_PORT", "18431"))
HOST = os.environ.get("COVE_TC_E2E_HOST", "127.0.0.1")
BASE = f"http://{HOST}:{PORT}"

passed: list[str] = []


def sh(*cmd, check=True, capture=True, timeout=None, input_=None):
    p = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, input=input_)
    if check and p.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return (p.stdout or "") + (p.stderr or "") if capture else ""


def dexec(*cmd, check=True, timeout=None, user=None):
    pre = ["docker", "exec"] + (["-u", user] if user else []) + [DEVICE]
    return sh(*pre, *cmd, check=check, timeout=timeout)


def ok(msg):
    passed.append(msg)
    print(f"  \033[32m✓\033[0m {msg}", flush=True)


def step(msg):
    print(f"\n\033[36m// {msg}\033[0m", flush=True)


class Api:
    def __init__(self):
        self.token = None

    def call(self, method, path, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = r.read()
        if raw:
            return payload
        return json.loads(payload) if payload else None


api = Api()


def wait_for(desc, fn, timeout=120, every=2):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(every)
    raise SystemExit(f"FAILED: timed out waiting for {desc} (last: {last!r})")


def device_row(dev_id):
    return api.call("GET", f"/api/admin/thin-clients/{dev_id}")


def run_command(dev_id, kind, args=None, timeout=90):
    cmd = api.call("POST", f"/api/admin/thin-clients/{dev_id}/commands", {"kind": kind, "args": args or {}})

    def finished():
        for c in api.call("GET", f"/api/admin/thin-clients/{dev_id}/commands"):
            if c["id"] == cmd["id"] and c["status"] not in ("queued", "sent"):
                return c
        return None

    done = wait_for(f"command {kind}", finished, timeout=timeout)
    if done["status"] != "done":
        raise SystemExit(f"FAILED: {kind} -> {done['status']}: {done.get('result')}")
    return done


def screenshot(dev_id, shots, name):
    run_command(dev_id, "screenshot")
    png = api.call("GET", f"/api/admin/thin-clients/{dev_id}/screenshot.png", raw=True)
    assert png.startswith(b"\x89PNG"), "not a PNG"
    if shots:
        os.makedirs(shots, exist_ok=True)
        with open(os.path.join(shots, name), "wb") as f:
            f.write(png)
    return png


def type_on_kiosk(*keys):
    """Type into the kiosk's focused window, as its user, on its Wayland display."""
    rt = "/run/user/$(id -u kiosk)"
    disp = f"$(basename $(ls {rt}/wayland-[0-9]* | grep -v lock | head -1))"
    # A fresh virtual keyboard can drop its very first key event while the
    # compositor takes its keymap, so lead with a harmless one (Shift).
    dexec("sh", "-c", f"XDG_RUNTIME_DIR={rt} WAYLAND_DISPLAY={disp} runuser -u kiosk -- "
          f"env XDG_RUNTIME_DIR={rt} WAYLAND_DISPLAY={disp} wtype -d 40 -k Shift_L {' '.join(keys)}")


# systemd as PID 1 needs a writable cgroup tree. --privileged gives it one on a
# normal Docker host; nested daemons (Docker-in-Docker) often refuse privileged
# mounts, and there the host cgroup namespace with a writable bind works instead.
# Both are for a disposable test daemon only.
DEVICE_MODES = [
    ["--privileged", "--cgroupns=private"],
    ["--cap-add", "SYS_ADMIN", "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
     "--cgroupns=host", "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw"],
]


def start_device():
    for mode in DEVICE_MODES:
        sh("docker", "rm", "-f", DEVICE, check=False)
        p = subprocess.run(["docker", "run", "-d", "--name", DEVICE, "--hostname", "bench-tc", "--network", NET,
                            *mode, "--tmpfs", "/run", "--tmpfs", "/run/lock", DEVICE_IMAGE],
                           capture_output=True, text=True)
        if p.returncode != 0:
            continue
        time.sleep(5)
        state = dexec("systemctl", "is-system-running", check=False).strip()
        if state in ("running", "degraded", "starting"):
            print(f"  device container mode: {' '.join(mode)}")
            return
    raise SystemExit("FAILED: could not boot systemd in a container on this Docker host")


def ppm_brightness(ppm: bytes) -> float:
    """Mean channel value of a binary PPM (P6) image: 0 black .. 255 white."""
    parts = ppm.split(maxsplit=4)  # P6, width, height, maxval, pixels
    pixels = parts[4]
    sample = pixels[:: 97]
    return sum(sample) / len(sample)


def teardown():
    for name in (DEVICE, COVE):
        sh("docker", "rm", "-f", name, check=False)
    sh("docker", "network", "rm", NET, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="cove:tc-e2e")
    ap.add_argument("--keep", action="store_true", help="leave the containers running afterwards")
    ap.add_argument("--shots", help="save the kiosk screenshots here")
    args = ap.parse_args()

    teardown()
    step("starting Cove")
    sh("docker", "network", "create", NET)
    sh("docker", "run", "-d", "--name", COVE, "--network", NET, "--network-alias", "cove",
       "-p", f"{PORT}:8080", "-e", "COVE_COOKIE_SECURE=false", "-e", "COVE_APP_ORIGIN=http://cove:8080",
       args.image)
    wait_for("Cove health", lambda: api.call("GET", "/api/health")["status"] == "ok", timeout=90)
    api.token = api.call("POST", "/api/auth/setup", {"username": "admin", "password": "password123"})["access_token"]
    api.call("POST", "/api/admin/users", {"username": "alice", "password": "password123", "is_admin": False})
    dev = api.call("POST", "/api/admin/thin-clients", {"name": "Workbench", "location": "downstairs"})
    dev_id = dev["id"]
    code = api.call("POST", f"/api/admin/thin-clients/{dev_id}/enroll-token", {"ttl_hours": 1})["code"]
    ok(f"Cove up; thin client registered, enrolment code {code}")

    step("booting the device and running the install one-liner")
    sh("docker", "build", "-q", "-t", DEVICE_IMAGE, "-f", os.path.join(HERE, "Dockerfile.device"), HERE)
    start_device()
    wait_for("systemd in the device",
             lambda: dexec("systemctl", "is-system-running", check=False).strip() in ("running", "degraded"),
             timeout=60)
    t0 = time.time()
    out = dexec("bash", "-c", f"curl -fsSL 'http://cove:8080/thin-client/install.sh?code={code}' | bash",
                timeout=1800)
    if "Done. This machine now boots straight into Cove." not in out:
        raise SystemExit(f"FAILED: installer output:\n{out[-3000:]}")
    ok(f"installer finished in {int(time.time() - t0)}s")
    assert "kiosk" in dexec("id", "kiosk")
    policy = json.loads(dexec("cat", "/etc/chromium/policies/managed/cove-thin-client.json"))
    assert ".cove" in policy["URLAllowlist"] and policy["URLBlocklist"][0] == "*", policy
    assert dexec("stat", "-c", "%a", "/etc/cove-thin-client/device.token").strip() == "600"
    # The kiosk session must read its sway config, and must not read the token.
    dexec("runuser", "-u", "kiosk", "--", "test", "-r", "/etc/cove-thin-client/sway.conf")
    assert "Permission denied" in dexec("runuser", "-u", "kiosk", "--", "cat", "/etc/cove-thin-client/device.token",
                                        check=False)
    assert "--autologin kiosk" in dexec("cat", "/etc/systemd/system/getty@tty1.service.d/cove-autologin.conf")
    ok("kiosk user, getty autologin, locked-down Chromium policy and a root-only device token are in place")

    step("starting the kiosk session")
    sh("docker", "cp", os.path.join(HERE, "cove-test-session.service"),
       f"{DEVICE}:/etc/systemd/system/cove-test-session.service")
    dexec("systemctl", "daemon-reload")
    dexec("systemctl", "enable", "--now", "cove-test-session.service")
    row = wait_for("the browser to bind",
                   lambda: (r := device_row(dev_id))["online"] and r["bound_at"] and r["state"].get("browser_running")
                   and r, timeout=180)
    ok(f"device online; browser running and bound ({row['state'].get('browser_version')})")
    assert row["state"]["agent_version"] == row["latest_agent_version"]
    screenshot(dev_id, args.shots, "01-login.png")
    ok("screenshot of the login page")
    assert "--force-dark-mode" in dexec("systemctl", "show", "-p", "ExecStart", "cove-browser")
    ok("browser tells pages the user prefers dark (the default colour scheme)")

    step("colour scheme: switching to light restarts the idle browser")
    pol = row["policy"]
    pol["color_scheme"] = "light"
    api.call("PATCH", f"/api/admin/thin-clients/{dev_id}", {"policy": pol})
    wait_for("a light browser", lambda: "preferredColorScheme=1" in dexec(
        "systemctl", "show", "-p", "ExecStart", "cove-browser", check=False), timeout=90)
    row = wait_for("the new browser to bind", lambda: (r := device_row(dev_id))["bound_at"] != row["bound_at"]
                   and r["state"].get("browser_running") and r, timeout=120)
    ok("light scheme applied by restarting the browser, which bound again")
    pol["color_scheme"] = "dark"
    api.call("PATCH", f"/api/admin/thin-clients/{dev_id}", {"policy": pol})
    row = wait_for("dark again", lambda: "--force-dark-mode" in dexec(
        "systemctl", "show", "-p", "ExecStart", "cove-browser", check=False)
        and (r := device_row(dev_id))["bound_at"] != row["bound_at"] and r["state"].get("browser_running") and r,
        timeout=180)
    time.sleep(3)

    step("signing in on the kiosk")
    first_bind = row["bound_at"]
    time.sleep(3)
    type_on_kiosk("alice", "-k", "Tab", "password123", "-k", "Return")
    row = wait_for("alice's session", lambda: (r := device_row(dev_id))["session_username"] == "alice" and r,
                   timeout=60)
    ok("alice signed in by typing on the kiosk; Cove sees a device-scoped session")
    time.sleep(3)
    screenshot(dev_id, args.shots, "02-dashboard.png")

    step("agent self-update keeps the session on screen")
    before = dexec("systemctl", "show", "-p", "ActiveEnterTimestamp", "cove-browser").strip()
    sh("docker", "exec", COVE, "sed", "-i", 's/^AGENT_VERSION = "\\(.*\\)"/AGENT_VERSION = "\\1-e2e"/',
       "/app/thinclient/agent/cove_thin_client.py")
    row = wait_for("the agent to update itself",
                   lambda: (r := device_row(dev_id))["state"].get("agent_version", "").endswith("-e2e") and r,
                   timeout=120)
    after = dexec("systemctl", "show", "-p", "ActiveEnterTimestamp", "cove-browser").strip()
    assert before == after, (before, after)
    assert row["session_username"] == "alice" and row["bound_at"] == first_bind
    ok(f"agent updated to {row['state']['agent_version']} without restarting the browser")

    step("policy: rotate the screen")
    pol = row["policy"]
    pol.update({"rotation": "90", "screen_off_minutes": 1, "signout_minutes": 2})
    api.call("PATCH", f"/api/admin/thin-clients/{dev_id}", {"policy": pol})
    row = wait_for("rotation", lambda: (r := device_row(dev_id))["state"].get("outputs")
                   and r["state"]["outputs"][0].get("transform") == "90" and r, timeout=60)
    assert "transform 90" in dexec("cat", "/etc/cove-thin-client/sway-outputs.conf")
    ok("rotation applied live and persisted for the next boot")
    pol["rotation"] = "normal"
    api.call("PATCH", f"/api/admin/thin-clients/{dev_id}", {"policy": pol})

    step("commands")
    done = run_command(dev_id, "logs", {"unit": "agent", "lines": 50})
    assert "cove-thin-client" in done["result"] or "browser" in done["result"], done["result"][:500]
    ok("logs returned the agent journal")
    run_command(dev_id, "identify", {"seconds": 5})
    ok("identify banner shown")

    step("idle: screen off after 1 minute, sign-out after 2 (no input)")
    dexec("/usr/local/lib/cove-thin-client/agent.py", "poke")
    wait_for("screen off", lambda: device_row(dev_id)["state"].get("display_on") is False, timeout=110, every=3)
    ok("screen went dark after a minute idle")
    row = wait_for("idle sign-out", lambda: (r := device_row(dev_id))["session_username"] is None
                   and r["bound_at"] != first_bind and r, timeout=150, every=3)
    ok("signed out after two minutes idle; a fresh browser bound again")
    audit = api.call("GET", "/api/admin/audit?action=thin_client.idle_signout")
    entries = audit if isinstance(audit, list) else audit.get("items", [])
    assert any("alice" in (e.get("detail") or "") for e in entries), entries
    ok("idle sign-out is in the audit log")
    dexec("/usr/local/lib/cove-thin-client/agent.py", "poke")
    wait_for("screen back on", lambda: device_row(dev_id)["state"].get("display_on") is True, timeout=30)
    ok("input wakes the screen")
    time.sleep(3)
    screenshot(dev_id, args.shots, "03-after-signout.png")

    step("deleting the device cuts it off")
    api.call("DELETE", f"/api/admin/thin-clients/{dev_id}")
    wait_for("the revoked page",
             lambda: json.loads(dexec("cat", "/run/cove-thin-client/browser.json", check=False) or "{}")
             .get("page") == "offline", timeout=90)
    assert "DEVICE REVOKED" in dexec("cat", "/usr/share/cove-thin-client/offline.html")
    time.sleep(5)
    rt = "/run/user/$(id -u kiosk)"
    b64 = dexec("sh", "-c", f"XDG_RUNTIME_DIR={rt} WAYLAND_DISPLAY=$(basename $(ls {rt}/wayland-[0-9]* "
                f"| grep -v lock | head -1)) grim -t png - | base64 -w0")
    png = base64.b64decode(b64)
    if args.shots:
        with open(os.path.join(args.shots, "04-revoked.png"), "wb") as f:
            f.write(png)
    ppm = base64.b64decode(dexec("sh", "-c", f"XDG_RUNTIME_DIR={rt} WAYLAND_DISPLAY=$(basename $(ls "
                                 f"{rt}/wayland-[0-9]* | grep -v lock | head -1)) grim -t ppm - | base64 -w0"))
    # The offline page is dark; Chromium's "This page is blocked" page is white.
    level = ppm_brightness(ppm)
    assert level < 60, f"the screen isn't the offline page (brightness {level:.0f})"
    ok("device shows DEVICE REVOKED (and the browser policy lets the offline page through)")

    print(f"\n\033[32mALL {len(passed)} CHECKS PASSED\033[0m")
    if not args.keep:
        teardown()


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as e:
        print(f"FAILED: HTTP {e.code} {e.url}: {e.read()[:500]!r}", file=sys.stderr)
        sys.exit(1)
