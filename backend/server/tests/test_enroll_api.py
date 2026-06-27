"""Tests for zone enrollment: token minting, install.sh, and CSR signing."""

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID, NameOID

from server.tests.helpers import add_image, setup_admin


def _make_csr() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cove-zone")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def _pending_zone_with_endpoint(client, host="10.0.0.5"):
    """Create a zone, then set its endpoint via PATCH so it stays non-enrolled."""
    zid = client.post("/api/admin/zones", json={"name": "LAN"}).json()["id"]
    client.patch(f"/api/admin/zones/{zid}", json={"endpoint_host": host})
    return zid


def test_mint_token_requires_endpoint(client):
    setup_admin(client)
    zid = client.post("/api/admin/zones", json={"name": "LAN"}).json()["id"]
    resp = client.post(f"/api/admin/zones/{zid}/enroll-token")
    assert resp.status_code == 400, resp.text


def test_mint_token_returns_install_command(client):
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client)
    resp = client.post(f"/api/admin/zones/{zid}/enroll-token")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token"]
    assert "install.sh?token=" in body["install_command"]
    # Minting flips a pending zone to "enrolling".
    assert client.get(f"/api/admin/zones/{zid}").json()["status"] == "enrolling"


def test_install_script_served_with_valid_token(client):
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client, host="agent.lan")
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    resp = client.get(f"/install.sh?token={token}")
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert token in body
    assert "agent.lan" in body
    assert "/api/zones/enroll" in body


def test_install_script_rejects_bad_token(client):
    setup_admin(client)
    resp = client.get("/install.sh?token=nope")
    assert resp.status_code == 404, resp.text


def test_enroll_signs_csr_and_marks_enrolled(client):
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]

    resp = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": "10.0.0.5"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "BEGIN CERTIFICATE" in body["server_cert_pem"]
    assert "BEGIN CERTIFICATE" in body["ca_cert_pem"]

    # The signed cert is a serverAuth leaf with the endpoint IP in its SAN.
    leaf = x509.load_pem_x509_certificate(body["server_cert_pem"].encode())
    eku = leaf.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert "10.0.0.5" in [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]

    # The zone is now enrolled.
    zone = client.get(f"/api/admin/zones/{zid}").json()
    assert zone["status"] == "enrolled"


def test_enroll_token_is_single_use(client):
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]

    first = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": "10.0.0.5"},
    )
    assert first.status_code == 200, first.text
    # A replay with the same (now-consumed) token is rejected.
    second = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": "10.0.0.5"},
    )
    assert second.status_code in (403, 409), second.text


def test_enroll_rejects_bad_token(client):
    setup_admin(client)
    resp = client.post(
        "/api/zones/enroll?token=nope",
        json={"csr_pem": _make_csr(), "endpoint_host": "10.0.0.5"},
    )
    assert resp.status_code == 403, resp.text


def test_enrolled_zone_accepts_workspaces(client):
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": _make_csr(), "endpoint_host": "10.0.0.5"},
    )
    image_id = add_image(name="Desktop", image_type="desktop")
    resp = client.post(
        "/api/workspaces", json={"name": "ws", "image_id": image_id, "zone_id": zid}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["zone_id"] == zid


def test_agent_image_streams_with_valid_token(client, fake_docker_manager):
    fake_docker_manager.save_image_stream.return_value = [b"TAR", b"DATA"]
    setup_admin(client)
    zid = _pending_zone_with_endpoint(client)
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]

    resp = client.get(f"/api/zones/agent-image?token={token}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/x-tar"
    assert resp.content == b"TARDATA"
    # The configured agent image (default cove:local) is what gets exported.
    fake_docker_manager.save_image_stream.assert_called_once_with("cove:local")


def test_agent_image_rejects_bad_token(client):
    setup_admin(client)
    resp = client.get("/api/zones/agent-image?token=nope")
    assert resp.status_code == 403, resp.text
