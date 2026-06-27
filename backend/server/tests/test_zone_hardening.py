"""Phase 7: zone liveness tracking and control-plane client-cert rotation."""

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from server.db import SessionLocal
from server.main import _mark_zone_reachable
from server.models import Zone
from server.tests.helpers import setup_admin


def _make_csr() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "z")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def _enroll_zone(client, host="10.0.0.5"):
    zid = client.post(
        "/api/admin/zones", json={"name": "LAN", "endpoint_host": host}
    ).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": host},
    )
    return zid


def test_zone_marked_offline_then_recovered(client):
    setup_admin(client)
    zid = _enroll_zone(client)

    _mark_zone_reachable(zid, False)
    assert client.get(f"/api/admin/zones/{zid}").json()["status"] == "offline"

    _mark_zone_reachable(zid, True)
    z = client.get(f"/api/admin/zones/{zid}").json()
    assert z["status"] == "enrolled"
    assert z["last_seen_at"] is not None


def test_mark_zone_reachable_ignores_local(client):
    setup_admin(client)
    _mark_zone_reachable(0, False)
    # The local zone is never flipped offline.
    assert client.get("/api/admin/zones/0").json()["status"] == "enrolled"


def test_rotate_client_cert_changes_cert(client):
    setup_admin(client)
    zid = _enroll_zone(client)

    db = SessionLocal()
    try:
        before = db.get(Zone, zid).client_cert_pem
    finally:
        db.close()

    resp = client.post(f"/api/admin/zones/{zid}/rotate-client-cert")
    assert resp.status_code == 200, resp.text

    db = SessionLocal()
    try:
        after = db.get(Zone, zid).client_cert_pem
    finally:
        db.close()
    assert after and after != before


def test_rotate_requires_mtls_material(client):
    setup_admin(client)
    # A manually-registered (plain TCP) zone has no certs to rotate.
    zid = client.post(
        "/api/admin/zones", json={"name": "plain", "endpoint_host": "1.2.3.4"}
    ).json()["id"]
    resp = client.post(f"/api/admin/zones/{zid}/rotate-client-cert")
    assert resp.status_code == 409, resp.text
