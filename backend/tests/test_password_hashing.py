"""
Password hashing uses bcrypt directly, replacing passlib.

passlib is unmaintained since 2020. Against bcrypt 5.0 its backend self-test
raises "password cannot be longer than 72 bytes" on every hash, which broke
account registration outright. Pinning bcrypt back to 3.2.2 to avoid that made
requirements.txt uninstallable, because chromadb requires bcrypt>=4.0.1.

These tests pin the replacement, including that hashes created under passlib
still verify, since existing accounts depend on it.
"""
import pytest

from routers.auth import (
    BCRYPT_MAX_BYTES,
    hash_password,
    password_too_long,
    verify_password,
)


def test_round_trip():
    h = hash_password("correct horse battery")
    assert verify_password("correct horse battery", h)


def test_wrong_password_is_rejected():
    assert not verify_password("wrong", hash_password("right-password"))


def test_hash_is_standard_bcrypt_format():
    """$2b$ format keeps hashes portable across bcrypt implementations."""
    h = hash_password("abcdef")
    assert h.startswith("$2b$12$") and len(h) == 60


def test_hashes_are_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_legacy_passlib_hash_still_verifies():
    """Accounts created before the change must keep working."""
    passlib = pytest.importorskip("passlib.context")
    legacy = passlib.CryptContext(schemes=["bcrypt"], deprecated="auto").hash("legacy-pass-1")
    assert verify_password("legacy-pass-1", legacy)
    assert not verify_password("legacy-pass-2", legacy)


@pytest.mark.parametrize("stored", ["", "not-a-hash", "$2b$12$tooshort", None])
def test_malformed_stored_hash_denies_rather_than_raises(stored):
    """A corrupt row must mean access denied, never a 500."""
    assert verify_password("anything", stored) is False


def test_empty_password_is_denied():
    assert verify_password("", hash_password("real-one")) is False


def test_non_ascii_passwords_work():
    h = hash_password("pässwörd-தமிழ்")
    assert verify_password("pässwörd-தமிழ்", h)
    assert not verify_password("passwort-tamil", h)


def test_seventy_two_byte_boundary():
    assert not password_too_long("a" * BCRYPT_MAX_BYTES)
    assert password_too_long("a" * (BCRYPT_MAX_BYTES + 1))


def test_length_is_measured_in_bytes_not_characters():
    """A 30-character Tamil password can exceed 72 UTF-8 bytes."""
    tamil = "த" * 30              # 3 bytes each in UTF-8
    assert len(tamil) == 30
    assert password_too_long(tamil)


def test_registration_rejects_over_long_passwords():
    """Refused at sign-up rather than silently truncated."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "routers" / "auth.py").read_text(encoding="utf-8")
    assert "password_too_long(body.password)" in src


def test_passlib_is_no_longer_imported():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "routers" / "auth.py").read_text(encoding="utf-8")
    assert "passlib" not in src.split('"""')[0] or "from passlib" not in src
    assert "from passlib" not in src
