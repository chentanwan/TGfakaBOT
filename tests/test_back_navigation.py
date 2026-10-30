from pathlib import Path


def test_admin_lists_have_back_navigation() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert 'text="« 管理后台", callback_data="adm:home"' in source
    assert 'text="« 返回审核列表", callback_data="adm:usdt:list"' in source
    assert 'text="« 返回审核列表", callback_data="adm:topups:list"' in source


def test_exception_and_charge_pages_have_admin_return() -> None:
    payment = Path("app/handlers/payment_admin.py").read_text()
    charge = Path("app/handlers/charge_admin.py").read_text()
    assert 'text="« 管理后台", callback_data="adm:home"' in payment
    assert 'text="« 管理后台", callback_data="adm:home"' in charge
    assert 'text="取消", callback_data="adm:charges:list"' in charge


def test_storefront_terminal_pages_have_navigation() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert 'text="« Categories", callback_data="shop:categories"' in source
    assert 'text="« My orders", callback_data="my_orders"' in source
    assert 'callback_data=f"order_detail:{order.public_id}"' in source


def test_supplier_pages_return_to_parent_menus() -> None:
    source = Path("plugins/prodseller/plugin.py").read_text()
    assert 'text="« 供应商管理", callback_data="adm:suppliers"' in source
    assert 'text="« 商品与库存", callback_data="adm:list"' in source
