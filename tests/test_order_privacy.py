from pathlib import Path


def test_order_list_does_not_render_secret() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    list_section = source.split("async def send_orders", 1)[1].split(
        "async def reveal_order_secret", 1
    )[0]
    assert "order.secret" not in list_section
    assert "order_secret:" in list_section


def test_secret_lookup_checks_telegram_user() -> None:
    source = Path("app/services/orders.py").read_text()
    detail_section = source.split("async def get_user_order", 1)[1].split(
        "async def list_user_orders", 1
    )[0]
    assert "User.telegram_user_id == telegram_user_id" in detail_section
    assert "Order.public_id == public_id" in detail_section


def test_privacy_command_exists() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'Command("privacy")' in source
