from cryptography.fernet import Fernet

from .config import settings


_cipher = Fernet(settings.credential_encryption_key.encode("ascii"))


def encrypt_secret(value: str) -> str:
    return _cipher.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str) -> str:
    return _cipher.decrypt(value.encode("ascii")).decode("utf-8")
