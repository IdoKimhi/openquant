import pytest
from app.security import encrypt, decrypt, create_access_token, verify_token, hash_password, verify_password


def test_encrypt_decrypt_roundtrip():
    plaintext = "test-secret-key"
    encrypted = encrypt(plaintext)
    assert encrypted != plaintext.encode()
    assert decrypt(encrypted) == plaintext


def test_jwt_create_verify():
    token = create_access_token({"sub": "test"})
    payload = verify_token(token)
    assert payload["sub"] == "test"


def test_password_hash_verify():
    hashed = hash_password("mypassword")
    assert verify_password("mypassword", hashed)
    assert not verify_password("wrong", hashed)