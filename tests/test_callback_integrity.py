from pathlib import Path


def test_known_dynamic_callbacks_fit_telegram_limit() -> None:
    uuid_value = "12345678-1234-1234-1234-123456789012"
    token = "abcdefghijkl"
    callbacks = [
        f"pp:e:{uuid_value}",
        f"ps:ap:p:{uuid_value}",
        f"ps:sc:{uuid_value}:9",
        f"bb:{uuid_value}:100:{token}",
        f"uc:usdt_arbitrum:{uuid_value}",
        f"uo:usdt_arbitrum:{uuid_value}",
        f"px:d:r:{uuid_value}:{token}",
        f"ch:d:{uuid_value}:{token}",
        f"ud:d:a:{uuid_value}:{token}",
    ]
    assert all(len(value.encode()) <= 64 for value in callbacks)


def test_product_list_uses_one_button_per_product_and_detail_actions() -> None:
    source = Path("app/handlers/admin.py").read_text()
    list_block = source[
        source.index("async def admin_product_list") : source.index(
            "async def admin_product_detail"
        )
    ]
    detail_block = source[
        source.index("async def admin_product_detail") : source.index("async def cards_start")
    ]
    assert 'callback_data=f"adm:product:{product.id}"' in list_block
    assert 'callback_data=f"adm:cards:{product.id}"' not in list_block
    assert 'callback_data=f"adm:cards:{product.id}"' in detail_block
    assert "plugin.admin_product_button(product.id)" in detail_block


def test_old_callbacks_get_an_answer_instead_of_spinning() -> None:
    main = Path("app/main.py").read_text()
    fallback = Path("app/handlers/fallback.py").read_text()
    assert "dispatcher.include_router(fallback.router)" in main
    assert "该菜单已更新" in fallback


def test_language_keyboard_has_no_extra_indonesian_append() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    language_block = source[
        source.index("async def choose_language") : source.index("async def set_language")
    ]
    assert language_block.count('callback_data="account:setlang:id"') == 0
