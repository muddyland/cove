"""The thin client install script (``/thin-client/install.sh?code=…``).

Kept deliberately small: it enrols the device (swapping the one-time code for a
device token), downloads the agent and checks its sha256, then hands over to
``agent install``. Everything else — packages, the kiosk user, sway, Chromium
policy, systemd units — lives in the agent, so updating the agent from Cove also
updates how the device is set up.
"""

import shlex

_TEMPLATE = r"""#!/usr/bin/env bash
# Cove thin client installer for "__NAME__".
# Turns this Debian machine into a kiosk that shows Cove full screen and is
# managed from Cove (Admin -> Thin clients). The enrolment code inside is
# single-use and expires.
set -euo pipefail

CP_URL=__CP_URL__
CODE=__CODE__
ETC=/etc/cove-thin-client
LIB=/usr/local/lib/cove-thin-client

if [ "$(id -u)" -ne 0 ]; then echo "Please run as root (sudo)." >&2; exit 1; fi
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" debian "*) ;;
  *) echo "This installer supports Debian (and Debian derivatives) only." >&2; exit 1 ;;
esac

echo ">> Cove thin client: installing bootstrap packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3 ca-certificates >/dev/null

# Readable by the kiosk session (its sway config lives here); the device token
# and config inside are root-only.
install -d -m 0755 "$ETC"
install -d -m 0755 "$LIB"

# Cove's own certificate, when it isn't publicly trusted (self-signed or a
# private CA): trusted system-wide from here on, and by the kiosk browser too.
CA_PEM=__CA_PEM__
if [ -n "$CA_PEM" ]; then
  echo ">> Trusting Cove's certificate"
  install -d -m 0755 /usr/local/share/ca-certificates
  printf '%s\n' "$CA_PEM" > /usr/local/share/ca-certificates/cove-thin-client.crt
  update-ca-certificates >/dev/null
fi

echo ">> Enrolling with $CP_URL"
CP_URL="$CP_URL" CODE="$CODE" ETC="$ETC" LIB="$LIB" python3 - <<'PY'
import hashlib, json, os, socket, urllib.request

cp, code, etc, lib = (os.environ[k] for k in ("CP_URL", "CODE", "ETC", "LIB"))
ua = {"User-Agent": "cove-thin-client-installer/1"}

req = urllib.request.Request(
    f"{cp}/api/thin-client/enroll?code={code}",
    data=json.dumps({"hostname": socket.gethostname()}).encode(),
    headers={**ua, "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=30) as r:
    resp = json.load(r)

token_path = os.path.join(etc, "device.token")
fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(resp["device_token"])
fd = os.open(os.path.join(etc, "config.json"), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"cp_url": cp, "public_id": resp["public_id"], "name": resp["name"],
               "allow_agent_update": True}, f, indent=2)

req = urllib.request.Request(
    f"{cp}/api/thin-client/agent",
    headers={**ua, "Authorization": "Bearer " + resp["device_token"]},
)
with urllib.request.urlopen(req, timeout=60) as r:
    data, want = r.read(), r.headers.get("X-Agent-Sha256", "")
if hashlib.sha256(data).hexdigest() != want:
    raise SystemExit("Agent download failed its checksum")
tmp = os.path.join(lib, "agent.py.new")
with open(tmp, "wb") as f:
    f.write(data)
os.replace(tmp, os.path.join(lib, "agent.py"))
print(f"   enrolled as {resp['name']!r}")
PY

echo ">> Setting up the kiosk (packages, browser, display) — this takes a few minutes"
python3 "$LIB/agent.py" install
echo ">> Done. This machine now boots straight into Cove."
"""


def render_install_script(*, cp_url: str, code: str, name: str, ca_pem: str = "") -> str:
    # The name is shown in a comment only; strip newlines so it can't break out.
    safe_name = name.replace("\n", " ").replace("\r", " ")
    return (
        _TEMPLATE.replace("__CP_URL__", shlex.quote(cp_url))
        .replace("__CODE__", shlex.quote(code))
        .replace("__NAME__", safe_name)
        .replace("__CA_PEM__", shlex.quote(ca_pem.strip()))
    )
