from pathlib import Path


def test_payment_settings_only_show_essential_controls() -> None:
    source = Path("app/handlers/admin.py").read_text()
    service = Path("app/services/payment_settings.py").read_text()

    assert 'text="📥 设置收款信息"' in source
    assert 'text="💵 设置最低金额"' in source
    assert 'text="⛔ 关闭" if method.enabled else "✅ 开启"' in source
    assert "✏️ 名称" not in source
    assert "✏️ 说明" not in source
    assert "✏️ 排序" not in source
    assert '"label": "请输入新的支付方式名称："' not in source
    assert '"description": "请输入新的用户说明："' not in source
    assert '"sort": "请输入排序数字' not in source
    assert '_EDITABLE_FIELDS = {"destination", "minimum_topup_minor"}' in service


def test_payment_overview_is_compact() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert "已启用渠道" in source
    assert "自动核验" in source
    assert "轮询：" not in source
    assert "EVM 确认：" not in source
    assert "TRON 确认：" not in source
    assert " · 排序：" not in source
