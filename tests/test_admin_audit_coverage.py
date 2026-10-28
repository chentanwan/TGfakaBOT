from pathlib import Path


def test_product_inventory_toggle_and_broadcast_actions_are_audited() -> None:
    service = Path("app/services/admin.py").read_text()
    handlers = Path("app/handlers/admin.py").read_text()
    for action in ("product_create", "inventory_import", "product_toggle"):
        assert f'action="{action}"' in service
    assert 'action="user_broadcast"' in handlers
    assert "admin_telegram_id=message.from_user.id" in handlers
