#!/usr/bin/env python3
"""Install-USB test: boot the built ISO in a VM, end to end.

Starts a Cove container, builds an install image that points at it (with an
enrolment code baked in), then in QEMU: picks "Install Cove thin client" at the
boot menu, lets the unattended Debian install run, boots the installed disk,
accepts the prefilled Cove address on the first-boot screen, and waits until
the device shows up online in Cove. A last screenshot shows the kiosk on the
VM's own (virtual) monitor, through the real tty1 autologin and sway on DRM.

Slow without KVM (software emulation): expect one to two hours. With /dev/kvm
available to Docker it takes a fraction of that.

Usage (from the repo root):
    docker build -t cove:tc-e2e .
    python3 thinclient/test/iso_vm.py --work /path/with/space [--image cove:tc-e2e] [--keep]
The work directory must be visible to the Docker daemon (it holds the ISO and
the VM disk).
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NET = "cove-tc-iso"
COVE = "cove-tc-iso-cove"
VM = "cove-tc-iso-vm"
PORT = int(os.environ.get("COVE_TC_ISO_PORT", "18432"))
BASE = f"http://127.0.0.1:{PORT}"
QEMU_IMAGE = "cove-tc-qemu:latest"

QEMU_DOCKERFILE = """FROM debian:trixie
RUN apt-get update && apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils socat netpbm \\
 && rm -rf /var/lib/apt/lists/*
"""


def sh(*cmd, check=True, timeout=None):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise SystemExit(f"FAILED: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p.stdout + p.stderr


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def api(method, path, body=None, token=None, raw=False, timeout=30):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = r.read()
    return payload if raw else (json.loads(payload) if payload else None)


def monitor(cmd):
    return sh("docker", "exec", VM, "sh", "-c", f"echo '{cmd}' | socat - UNIX-CONNECT:/work/mon", check=False)


def vm_running():
    return sh("docker", "inspect", "-f", "{{.State.Running}}", VM, check=False).strip() == "true"


def shot(work, name):
    monitor("screendump /work/screen.ppm")
    time.sleep(1)
    sh("docker", "exec", VM, "sh", "-c", f"pnmtopng /work/screen.ppm > /work/{name} 2>/dev/null", check=False)
    return os.path.join(work, name)


def start_vm(work, *, cdrom):
    sh("docker", "rm", "-f", VM, check=False)
    kvm = ["--device", "/dev/kvm"] if os.path.exists("/dev/kvm") else []
    accel = ["-accel", "kvm", "-cpu", "host"] if kvm else ["-accel", "tcg", "-cpu", "max"]
    args = ["qemu-system-x86_64", *accel, "-m", "3072", "-smp", "2",
            "-drive", "file=/work/disk.qcow2,if=virtio",
            "-nic", "user,model=virtio-net-pci", "-vga", "virtio", "-display", "none",
            "-monitor", "unix:/work/mon,server,nowait"]
    if cdrom:
        # -no-reboot: the VM exits when the installer reboots, which is our cue.
        args += ["-cdrom", "/work/cove-tc-vm.iso", "-boot", "d", "-no-reboot"]
    else:
        args += ["-boot", "c"]
    sh("docker", "run", "-d", "--name", VM, "--network", NET, *kvm, "-v", f"{work}:/work", QEMU_IMAGE, *args)


def teardown():
    for c in (VM, COVE):
        sh("docker", "rm", "-f", c, check=False)
    sh("docker", "network", "rm", NET, check=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="cove:tc-e2e")
    ap.add_argument("--work", required=True)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--install-timeout", type=int, default=3 * 3600)
    args = ap.parse_args()
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)

    teardown()
    sh("docker", "network", "create", NET)
    # The VM reaches Cove by its IP on the test network, through QEMU's NAT.
    # No COVE_APP_ORIGIN: Cove then builds install URLs from the address the
    # VM used, which is that IP.
    sh("docker", "run", "-d", "--name", COVE, "--network", NET, "-p", f"{PORT}:8080",
       "-e", "COVE_COOKIE_SECURE=false", args.image)
    cove_ip = sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", COVE).strip()
    for _ in range(90):
        try:
            if api("GET", "/api/health", timeout=5)["status"] == "ok":
                break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    else:
        raise SystemExit(f"FAILED: Cove never answered on {BASE}")
    token = api("POST", "/api/auth/setup", {"username": "admin", "password": "password123"})["access_token"]
    dev = api("POST", "/api/admin/thin-clients", {"name": "Rack", "location": "server rack"}, token)
    code = api("POST", f"/api/admin/thin-clients/{dev['id']}/enroll-token", {"ttl_hours": 168}, token)["code"]
    log(f"Cove up at {cove_ip}; device 'Rack' with code {code}")

    log("building the install image")
    out = sh(os.path.join(REPO, "thinclient", "iso", "build-iso.sh"), "--cove-url", f"http://{cove_ip}:8080",
             "--code", code, "--out", os.path.join(work, "cove-tc-vm.iso"), "--password", "testpass123",
             timeout=3600)
    log(out.strip().splitlines()[-5] if out.strip() else "built")

    with open(os.path.join(work, "Dockerfile.qemu"), "w") as f:
        f.write(QEMU_DOCKERFILE)
    sh("docker", "build", "-q", "-t", QEMU_IMAGE, "-f", os.path.join(work, "Dockerfile.qemu"), work)
    sh("docker", "run", "--rm", "-v", f"{work}:/work", QEMU_IMAGE, "sh", "-c",
       "rm -f /work/disk.qcow2 && qemu-img create -f qcow2 /work/disk.qcow2 16G")

    log("stage 1: unattended Debian install from the ISO")
    start_vm(work, cdrom=True)
    time.sleep(25)
    shot(work, "vm-00-bootmenu.png")
    monitor("sendkey ret")  # the Cove entry is the default; Debian's menu has no timeout
    t0 = time.time()
    n = 0
    while vm_running():
        if time.time() - t0 > args.install_timeout:
            shot(work, "vm-timeout.png")
            raise SystemExit("FAILED: the install did not finish in time")
        time.sleep(60)
        n += 1
        if n % 5 == 0:
            shot(work, "vm-01-installing.png")
            log(f"  installing… {int((time.time() - t0) / 60)} min")
    log(f"install finished in {int((time.time() - t0) / 60)} min")

    log("stage 2: first boot and enrolment")
    start_vm(work, cdrom=False)
    t0 = time.time()
    online = None
    pressed = 0
    while time.time() - t0 < args.install_timeout:
        time.sleep(30)
        elapsed = time.time() - t0
        # Accept the prefilled Cove address (the code is baked in). Blind, a few
        # times: before the dialog is up the key is harmless.
        if pressed < 6 and elapsed > 120 + pressed * 60:
            shot(work, f"vm-02-firstboot-{pressed}.png")
            monitor("sendkey ret")
            pressed += 1
        # The admin's access token (30 min) outlives neither stage: sign in again.
        token = api("POST", "/api/auth/login", {"username": "admin", "password": "password123"})["access_token"]
        row = api("GET", f"/api/admin/thin-clients/{dev['id']}", token=token)
        if row["online"] and row["state"].get("browser_running") and row["bound_at"]:
            online = row
            break
    if not online:
        shot(work, "vm-timeout.png")
        raise SystemExit("FAILED: the VM never came online in Cove")
    log(f"device online after {int((time.time() - t0) / 60)} min: {online['state'].get('os')}, "
        f"{online['state'].get('browser_version')}")
    time.sleep(45)
    path = shot(work, "vm-03-kiosk.png")
    log(f"kiosk screenshot: {path}")
    api("POST", f"/api/admin/thin-clients/{dev['id']}/commands", {"kind": "screenshot"}, token)
    time.sleep(20)
    try:
        png = api("GET", f"/api/admin/thin-clients/{dev['id']}/screenshot.png", token=token, raw=True)
        with open(os.path.join(work, "vm-04-agent-screenshot.png"), "wb") as f:
            f.write(png)
    except Exception as exc:  # noqa: BLE001
        log(f"agent screenshot not available: {exc}")
    print("\nISO VM TEST PASSED")
    if not args.keep:
        teardown()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
