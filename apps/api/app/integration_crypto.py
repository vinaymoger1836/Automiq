"""Authenticated encryption for workspace integration credentials."""

import base64
import binascii
import json
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

from app.config import get_settings


def active_key_version() -> int:
    return get_settings().integration_active_key_version


def _cipher(version: int) -> AESGCM:
    settings = get_settings()
    keyring_text = settings.integration_keyring.get_secret_value()
    if keyring_text:
        try:
            encoded = json.loads(keyring_text)[str(version)]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(
                status_code=503, detail="Integration encryption is not configured"
            ) from None
    else:
        encoded = settings.integration_encryption_key.get_secret_value() if version == 1 else ""
    try:
        key = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error):
        key = b""
    if len(key) != 32:
        raise HTTPException(status_code=503, detail="Integration encryption is not configured")
    return AESGCM(key)


def _associated(workspace_id: uuid.UUID, integration_id: uuid.UUID, provider: str) -> bytes:
    return f"{workspace_id}:{integration_id}:{provider}".encode()


def encrypt_credentials(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    provider: str,
    values: dict[str, str],
    key_version: int,
) -> bytes:
    import secrets

    wrap_nonce = secrets.token_bytes(12)
    data_nonce = secrets.token_bytes(12)
    data_key = secrets.token_bytes(32)
    plaintext = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    associated = _associated(workspace_id, integration_id, provider)
    wrapped_key = _cipher(key_version).encrypt(wrap_nonce, data_key, associated)
    ciphertext = AESGCM(data_key).encrypt(data_nonce, plaintext, associated)
    return b"v1" + wrap_nonce + wrapped_key + data_nonce + ciphertext


def decrypt_credentials(
    workspace_id: uuid.UUID,
    integration_id: uuid.UUID,
    provider: str,
    ciphertext: bytes,
    key_version: int,
) -> dict[str, str]:
    try:
        if len(ciphertext) < 90 or ciphertext[:2] != b"v1":
            raise ValueError("Invalid credential envelope")
        associated = _associated(workspace_id, integration_id, provider)
        data_key = _cipher(key_version).decrypt(ciphertext[2:14], ciphertext[14:62], associated)
        plaintext = AESGCM(data_key).decrypt(ciphertext[62:74], ciphertext[74:], associated)
        values = json.loads(plaintext)
    except (InvalidTag, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=503, detail="Integration credential unavailable") from None
    if not isinstance(values, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in values.items()
    ):
        raise HTTPException(status_code=503, detail="Integration credential unavailable")
    return values
