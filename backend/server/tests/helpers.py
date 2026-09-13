"""Shared helpers for the API test modules."""

from server.db import SessionLocal
from server.models import WorkspaceImage


def setup_admin(client, username="admin", password="password123"):
    """Run first-run setup and return (token, response)."""
    resp = client.post(
        "/api/auth/setup", json={"username": username, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"], resp


def login(client, username, password):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def create_user_via_admin(client, admin_token, username, password="password123", is_admin=False):
    resp = client.post(
        "/api/admin/users",
        json={"username": username, "password": password, "is_admin": is_admin},
        headers=auth_header(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def set_workspace_status(ws_id, status):
    """Force a workspace's status directly in the DB.

    The tests' fake DockerManager is a no-op MagicMock, so it never transitions a
    workspace out of "creating"/"stopping". Tests that need a resting workspace
    (e.g. to edit or migrate it) set the precondition here."""
    from server.models import Workspace

    db = SessionLocal()
    try:
        ws = db.get(Workspace, ws_id)
        ws.status = status
        db.commit()
    finally:
        db.close()


def add_image(name="Ubuntu Desktop", docker_image="lscr.io/linuxserver/webtop:latest",
              image_type="desktop", url_env=None, internal_port=3000, logo_url=None,
              icon_png=None):
    """Insert a WorkspaceImage directly into the DB and return its id.

    Used because the create-image API path currently rejects the "browser"
    image_type, so direct insertion is the reliable way to seed a browser image.
    """
    db = SessionLocal()
    try:
        img = WorkspaceImage(
            name=name,
            docker_image=docker_image,
            image_type=image_type,
            url_env=url_env,
            internal_port=internal_port,
            logo_url=logo_url,
            icon_png=icon_png,
            enabled=True,
        )
        db.add(img)
        db.commit()
        db.refresh(img)
        return img.id
    finally:
        db.close()


def make_csr() -> str:
    """A throwaway CSR (the agent's server key never leaves the agent; tests
    generate one locally the same way the installer does)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cove-zone")]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def enroll_zone(client, name="LAN", host="10.0.0.5", port=8443):
    """Register + fully enroll a remote zone (mTLS material issued). Returns
    ``(zone_id, enroll_response_json)``. Since 1.1.0 a zone is only usable once
    enrolled — a manually-registered endpoint alone stays 'pending'."""
    zid = client.post(
        "/api/admin/zones",
        json={"name": name, "endpoint_host": host, "endpoint_port": port},
    ).json()["id"]
    token = client.post(f"/api/admin/zones/{zid}/enroll-token").json()["token"]
    resp = client.post(
        f"/api/zones/enroll?token={token}",
        json={"csr_pem": make_csr(), "endpoint_host": host, "endpoint_port": port},
    )
    assert resp.status_code == 200, resp.text
    return zid, resp.json()
