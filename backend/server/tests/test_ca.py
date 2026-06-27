"""Tests for the private CA (zone mTLS) and per-zone cert staging."""

import os
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID

from server import ca
from server.config import Settings


@pytest.fixture
def ca_settings(tmp_path, monkeypatch):
    """Point the CA at a fresh per-test data dir so each test gets its own root."""
    s = Settings(data_dir=tmp_path)
    monkeypatch.setattr(ca, "get_settings", lambda: s)
    return s


def test_get_ca_generates_and_persists(ca_settings):
    cert, key = ca.get_ca()
    assert ca_settings.ca_cert_file.exists()
    assert ca_settings.ca_key_file.exists()
    # Key file is owner-only.
    assert oct(os.stat(ca_settings.ca_key_file).st_mode)[-3:] == "600"
    # Idempotent: a second call loads the same cert (same serial).
    cert2, _ = ca.get_ca()
    assert cert.serial_number == cert2.serial_number
    # It's a CA.
    bc = cert.extensions.get_extension_for_oid(ExtensionOID.BASIC_CONSTRAINTS).value
    assert bc.ca is True


def test_issue_server_cert_has_san_and_eku(ca_settings):
    cert_pem, key_pem = ca.issue_cert(
        "zone-1", is_server=True, ip_addresses=["10.0.0.5"], dns_names=["agent.local"]
    )
    leaf = x509.load_pem_x509_certificate(cert_pem.encode())
    eku = leaf.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
    san = leaf.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert "10.0.0.5" in [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
    assert "agent.local" in san.get_values_for_type(x509.DNSName)
    assert "PRIVATE KEY" in key_pem


def test_issue_client_cert_eku(ca_settings):
    cert_pem, _ = ca.issue_cert("cp-client", is_server=False)
    leaf = x509.load_pem_x509_certificate(cert_pem.encode())
    eku = leaf.extensions.get_extension_for_oid(ExtensionOID.EXTENDED_KEY_USAGE).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku


def test_issued_cert_is_signed_by_ca(ca_settings):
    ca_cert, _ = ca.get_ca()
    cert_pem, _ = ca.issue_cert("zone-1", is_server=True, ip_addresses=["10.0.0.5"])
    leaf = x509.load_pem_x509_certificate(cert_pem.encode())
    # Verifying the leaf's signature with the CA public key must not raise.
    ca_cert.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf.signature_hash_algorithm,
    )
    assert leaf.issuer == ca_cert.subject


def test_fingerprint_stable(ca_settings):
    cert_pem, _ = ca.issue_cert("zone-1", is_server=True)
    fp1 = ca.cert_fingerprint(cert_pem)
    fp2 = ca.cert_fingerprint(cert_pem)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_stage_zone_certs_roundtrip(tmp_path, monkeypatch):
    """A zone's client key is decrypted on stage and written 0600; the public
    certs round-trip verbatim."""
    from server import docker_manager
    from server.security import encrypt_secret

    monkeypatch.setattr(docker_manager, "get_settings", lambda: Settings(data_dir=tmp_path))

    key_pem = "-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n"
    zone = SimpleNamespace(
        id=7,
        ca_cert_pem="CA-CERT-PEM",
        client_cert_pem="CLIENT-CERT-PEM",
        client_key_enc=encrypt_secret(key_pem),
    )
    cert_path, key_path, ca_path = docker_manager.stage_zone_certs(zone)

    assert open(ca_path).read() == "CA-CERT-PEM"
    assert open(cert_path).read() == "CLIENT-CERT-PEM"
    assert open(key_path).read() == key_pem  # decrypted back to plaintext
    for p in (cert_path, key_path, ca_path):
        assert oct(os.stat(p).st_mode)[-3:] == "600"


def test_mtls_handshake_end_to_end(ca_settings, tmp_path):
    """The issued server + client certs complete a real mutual-TLS handshake:
    the client verifies the server's SAN against the CA, and the server requires
    a CA-signed client cert. This is the actual transport the control plane uses
    to dial a zone."""
    import socket
    import ssl
    import threading

    server_cert, server_key = ca.issue_cert("agent", is_server=True, ip_addresses=["127.0.0.1"])
    client_cert, client_key = ca.issue_cert("cp", is_server=False)
    ca_pem = ca.ca_cert_pem()

    paths = {}
    for name, content in {
        "ca.crt": ca_pem,
        "s.crt": server_cert,
        "s.key": server_key,
        "c.crt": client_cert,
        "c.key": client_key,
    }.items():
        p = tmp_path / name
        p.write_text(content)
        paths[name] = str(p)

    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(paths["s.crt"], paths["s.key"])
    sctx.load_verify_locations(paths["ca.crt"])
    sctx.verify_mode = ssl.CERT_REQUIRED  # require a client cert (mutual)

    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    cctx.load_cert_chain(paths["c.crt"], paths["c.key"])
    cctx.load_verify_locations(paths["ca.crt"])

    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(1)
    port = lsock.getsockname()[1]
    result = {}

    def serve():
        try:
            conn, _ = lsock.accept()
            with sctx.wrap_socket(conn, server_side=True) as ss:
                result["client_cert_seen"] = ss.getpeercert() is not None
                ss.sendall(b"ok")
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            result["error"] = repr(exc)

    t = threading.Thread(target=serve)
    t.start()
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
            with cctx.wrap_socket(raw, server_hostname="127.0.0.1") as cs:
                data = cs.recv(2)
    finally:
        t.join(timeout=5)
        lsock.close()

    assert result.get("error") is None, result.get("error")
    assert data == b"ok"
    assert result.get("client_cert_seen") is True


def test_zone_has_mtls():
    from server.docker_manager import _zone_has_mtls

    assert not _zone_has_mtls(
        SimpleNamespace(ca_cert_pem=None, client_cert_pem=None, client_key_enc=None)
    )
    assert _zone_has_mtls(
        SimpleNamespace(ca_cert_pem="a", client_cert_pem="b", client_key_enc="c")
    )
