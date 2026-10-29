from pathlib import Path


def test_core_menu_layouts_are_grouped() -> None:
    admin = Path("app/handlers/admin.py").read_text()
    catalog = Path("app/handlers/catalog.py").read_text()

    assert "rows = [field_buttons[:2], field_buttons[2:4], field_buttons[4:]]" in admin
    assert "method_buttons[index : index + 2]" in admin
    assert 'InlineKeyboardButton(text="📦 My orders", callback_data="my_orders"),' in catalog
    assert 'InlineKeyboardButton(text="🏠 Home", callback_data="shop:home"),' in catalog
    assert 'text=text(language, "topup"), callback_data="account:topup"' in catalog
    assert 'text=text(language, "topup_history"), callback_data="account:topups"' in catalog


def test_supplier_exception_actions_avoid_crowded_three_button_rows() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    detail = source.index('text=f"📋 详情 {item.public_id}"')
    recovery = source.index('text="🔎 查询恢复"', detail)
    candidates = source.index('text="🧩 候选订单"', recovery)
    between_detail_and_recovery = source[detail:recovery]
    between_recovery_and_candidates = source[recovery:candidates]
    assert "rows.append" in between_detail_and_recovery
    assert "rows.append" not in between_recovery_and_candidates
