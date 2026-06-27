"""Private certificate authority for zone mTLS.

The control plane is its own CA: it generates a self-signed root once (stored as
files under ``data_dir/ca/`` like the JWT secret key), then issues short-lived
leaf certs for each enrolled zone — a server cert the agent presents and a client
cert the control plane presents when dialing that agent. Both sides verify against
the CA, so only certs this CA signed are trusted (mutual TLS).

Leaf private keys are sensitive: the agent keeps its own; the control plane's
per-zone client key is encrypted at rest (security.encrypt_secret) on the Zone row.
The CA private key never leaves the control plane.
"""

import datetime
import hashlib
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from server.config import get_settings

# Leaf cert lifetime. ~13 months — long enough to be practical, short enough to
# force rotation (Phase 7) rather than minting effectively-permanent credentials.
_LEAF_VALID_DAYS = 397
_CA_VALID_DAYS = 3650


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _generate_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Cove Zone CA")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=_CA_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return cert, key


def get_ca() -> tuple[x509.Certificate, rsa.RSAPrivateKey]:
    """Load the CA cert+key, generating and persisting them on first use.

    Mirrors ``Settings.get_secret_key``: the key file is written 0600 under
    ``data_dir/ca/``; the cert is world-readable (it's public).
    """
    settings = get_settings()
    cert_path = settings.ca_cert_file
    key_path = settings.ca_key_file
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        return cert, key

    cert, key = _generate_ca()
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(key_path, 0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(cert_path, 0o644)
    return cert, key


def ca_cert_pem() -> str:
    """The CA certificate (PEM) — handed to agents so they trust the control
    plane's client cert, and pinned by the control plane for each zone."""
    cert, _ = get_ca()
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _san(dns_names, ip_addresses) -> x509.SubjectAlternativeName | None:
    entries: list[x509.GeneralName] = []
    for d in dns_names or []:
        entries.append(x509.DNSName(d))
    for ip in ip_addresses or []:
        entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
    return x509.SubjectAlternativeName(entries) if entries else None


def issue_cert(
    common_name: str,
    *,
    is_server: bool,
    dns_names: list[str] | None = None,
    ip_addresses: list[str] | None = None,
    valid_days: int = _LEAF_VALID_DAYS,
) -> tuple[str, str]:
    """Issue a CA-signed leaf cert. Returns ``(cert_pem, key_pem)``.

    ``is_server`` sets the EKU to serverAuth (the agent's listener) vs clientAuth
    (the control plane dialing the agent). ``dns_names``/``ip_addresses`` populate
    the SAN so hostname/IP verification succeeds against the agent's endpoint.
    """
    ca_cert, ca_key = get_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    eku = ExtendedKeyUsageOID.SERVER_AUTH if is_server else ExtendedKeyUsageOID.CLIENT_AUTH

    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
    )
    san = _san(dns_names, ip_addresses)
    if san is not None:
        builder = builder.add_extension(san, critical=False)

    cert = builder.sign(ca_key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def sign_csr(
    csr_pem: str,
    common_name: str,
    *,
    is_server: bool,
    dns_names: list[str] | None = None,
    ip_addresses: list[str] | None = None,
    valid_days: int = _LEAF_VALID_DAYS,
) -> str:
    """Sign an externally-generated CSR and return the leaf cert PEM.

    Used at enrollment so the agent's server *private key never leaves the host*:
    the agent generates the keypair + CSR locally and we sign only the public key.
    The subject CN, EKU and SAN are set by us (not trusted from the CSR), so a
    malicious CSR can't request a cert for an identity/host we didn't intend.
    """
    ca_cert, ca_key = get_ca()
    csr = x509.load_pem_x509_csr(csr_pem.encode())
    if not csr.is_signature_valid:
        raise ValueError("CSR signature is invalid")
    eku = ExtendedKeyUsageOID.SERVER_AUTH if is_server else ExtendedKeyUsageOID.CLIENT_AUTH

    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + datetime.timedelta(days=valid_days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([eku]), critical=False)
    )
    san = _san(dns_names, ip_addresses)
    if san is not None:
        builder = builder.add_extension(san, critical=False)

    cert = builder.sign(ca_key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def cert_fingerprint(cert_pem: str) -> str:
    """SHA-256 fingerprint (hex) of a PEM certificate — used to pin an agent's
    server cert at enrollment."""
    cert = x509.load_pem_x509_certificate(cert_pem.encode())
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
