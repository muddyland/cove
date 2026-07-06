#!/usr/bin/env bash
# Generate a self-signed TLS certificate + Traefik default-cert config for a LAN
# (self-signed) HTTPS deployment — see docker-compose.lan-tls.yml.
#
# Why HTTPS on a LAN at all: LinuxServer/Selkies workspace streams require a
# browser "secure context", which is only granted over HTTPS (or *.localhost).
# Served over plain HTTP the stream refuses to start with:
#   "This application requires a secure connection (HTTPS)."
#
# Usage:  scripts/gen-lan-cert.sh <host-or-ip> [more hosts/ips ...]
#   scripts/gen-lan-cert.sh 192.168.8.131
#   scripts/gen-lan-cert.sh 192.168.8.131 campserve campserve.local
#
# The first argument is the CN; every argument is added to the Subject
# Alternative Name list (dotted-quad -> IP:, otherwise DNS:). localhost and
# 127.0.0.1 are always included. Writes, relative to the repo root:
#   certs/cove.crt, certs/cove.key, certs/dynamic.yml
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <host-or-ip> [host-or-ip ...]" >&2
  echo "  e.g. $0 192.168.8.131 campserve.local" >&2
  exit 1
fi

cd "$(dirname "$0")/.."
mkdir -p certs

# Build the SAN list. Numeric IPv4 -> IP:, everything else -> DNS:.
sans="DNS:localhost,IP:127.0.0.1"
for h in "$@"; do
  if [[ "$h" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    sans="${sans},IP:${h}"
  else
    sans="${sans},DNS:${h}"
  fi
done

openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
  -keyout certs/cove.key -out certs/cove.crt \
  -subj "/CN=${1}" \
  -addext "subjectAltName=${sans}"
chmod 600 certs/cove.key

cat > certs/dynamic.yml <<'YAML'
# Default TLS certificate for the LAN self-signed HTTPS deployment
# (docker-compose.lan-tls.yml). Regenerate with scripts/gen-lan-cert.sh.
tls:
  stores:
    default:
      defaultCertificate:
        certFile: /tls/cove.crt
        keyFile: /tls/cove.key
YAML

echo "Wrote certs/cove.crt, certs/cove.key, certs/dynamic.yml"
echo "  CN:   ${1}"
echo "  SANs: ${sans}"
echo
echo "Next: set COVE_APP_ORIGIN=https://<host> and COVE_COOKIE_SECURE=true in .env, then:"
echo "  docker compose -f docker-compose.yml -f docker-compose.lan-tls.yml up -d"
