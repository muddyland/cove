"""Parse the client-certificate CN that the agent's Traefik forwards.

The agent's Traefik runs a ``passTLSClientCert`` middleware that injects the
verified client cert's subject into ``X-Forwarded-Tls-Client-Cert-Info``. The
agent uses the CN to pin each zone to *its* control-plane client cert
(``cove-cp-<zone-public-id>``) — so a client cert issued for one zone cannot be
replayed against another, even though both are signed by the same CA.
"""

import re
from urllib.parse import unquote

# Header Traefik's passTLSClientCert middleware sets (with info.subject.commonName).
CLIENT_CERT_INFO_HEADER = "x-forwarded-tls-client-cert-info"

_SUBJECT_RE = re.compile(r'Subject="([^"]*)"')
_CN_RE = re.compile(r"CN=([^\",;/]+)")


def extract_client_cn(header_value: str | None) -> str | None:
    """Return the client cert CN from the forwarded header, or None.

    The header value is URL-encoded and contains a ``Subject="..."`` segment."""
    if not header_value:
        return None
    decoded = unquote(header_value)
    subject_match = _SUBJECT_RE.search(decoded)
    subject = subject_match.group(1) if subject_match else decoded
    cn_match = _CN_RE.search(subject)
    return cn_match.group(1) if cn_match else None
