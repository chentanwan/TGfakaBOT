from pathlib import Path

CALLBACK_MODULES = (
    "app/handlers/admin.py",
    "app/handlers/catalog.py",
    "app/handlers/payment_admin.py",
    "app/handlers/charge_admin.py",
    "plugins/admin_manager/plugin.py",
    "plugins/price_manager/plugin.py",
    "plugins/prodseller/plugin.py",
)


def test_callback_menus_replace_current_message() -> None:
    for filename in CALLBACK_MODULES:
        source = Path(filename).read_text()
        assert "callback.message.answer(" not in source, filename
        assert "replace_callback_message" in source, filename


def test_navigation_has_safe_edit_delete_send_fallback() -> None:
    source = Path("app/ui/navigation.py").read_text()
    assert "callback.message.edit_text" in source
    assert "callback.message.delete" in source
    assert "callback.message.answer" in source
    assert "message is not modified" in source


def test_customer_home_callback_replaces_message() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    start = source.index('F.data == "shop:home"')
    end = source.index('@router.message(Command("help"))', start)
    handler = source[start:end]
    assert "replace_callback_message" in handler
    assert "send_home(callback.message" not in handler
