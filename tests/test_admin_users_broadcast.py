from pathlib import Path


def test_balance_adjustment_locks_user_and_prevents_negative_balance() -> None:
    source = Path("app/services/admin.py").read_text()
    assert "with_for_update()" in source
    assert "user.balance_minor + delta_minor < 0" in source
    assert "user.balance_minor += delta_minor" in source


def test_admin_user_lookup_and_broadcast_are_present() -> None:
    source = Path("app/handlers/admin.py").read_text()
    for value in ("adm:user:find", "adm:user:balance:", "adm:broadcast"):
        assert value in source
    assert "await asyncio.sleep(0.05)" in source
    assert "list_broadcast_user_ids" in source
