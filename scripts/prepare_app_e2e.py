"""Generate short-lived synthetic TLS files for the custom-app E2E provider."""

import base64
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def main() -> None:
    target = Path("artifacts/e2e/app-connections/tls")
    target.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-app")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("fake-app")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    (target / "cert.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path = target / "key.key"
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    env_path = Path(".env.app-e2e.local")
    env_path.write_text(
        "INTEGRATION_ENCRYPTION_KEY=" + base64.b64encode(secrets.token_bytes(32)).decode() + "\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    print("Synthetic app TLS and ignored E2E environment file are ready")


if __name__ == "__main__":
    main()
