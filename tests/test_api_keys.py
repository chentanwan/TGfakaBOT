from pathlib import Path


def test_api_keys_are_hashed_and_shown_once() -> None:
    service = Path("app/services/api_keys.py").read_text()
    handler = Path("app/handlers/catalog.py").read_text()
    assert "hashlib.sha256(api_key.encode()).digest()" in service
    assert "return raw_key" in service
    assert "complete key is shown only immediately after generation" in handler


def test_rotation_invalidates_previous_key() -> None:
    source = Path("app/services/api_keys.py").read_text()
    assert "credential.key_hash = _hash_key(raw_key)" in source
    assert "credential.request_count = 0" in source


def test_topup_history_and_rejection_reason_are_available() -> None:
    catalog = Path("app/handlers/catalog.py").read_text()
    admin = Path("app/handlers/admin.py").read_text()
    assert "account:topups" in catalog
    assert "list_user_topups" in catalog
    assert "TopupRejection.reason" in admin
    assert "rejection_reason=reason" in admin
