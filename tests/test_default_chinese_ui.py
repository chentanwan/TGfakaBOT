from pathlib import Path


def test_default_language_is_chinese() -> None:
    config = Path("app/core/config.py").read_text()
    i18n = Path("app/core/i18n.py").read_text()
    assert 'bot_language: str = "zh"' in config
    assert 'fallback: str = "zh"' in i18n


def test_storefront_buttons_are_chinese_by_default() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    expected = (
        "立即购买",
        "余额不足 · 立即充值",
        "我的订单",
        "创建支付订单",
        "商品分类",
        "查看已交付商品",
        "取消",
        "首页",
    )
    for label in expected:
        assert label in source
    forbidden_button_labels = (
        'text="Custom"',
        'text="« Back"',
        'text="🏠 Home"',
        'text="✖ Cancel"',
        'text="📦 My orders"',
        'text="✅ Create payment order"',
        'text="🔐 View delivered product"',
    )
    for label in forbidden_button_labels:
        assert label not in source


def test_chinese_workflow_prompts_are_present() -> None:
    source = Path("app/handlers/catalog.py").read_text()
    assert "请输入数量（例如2）" in source
    assert "请选择 USDT 支付方式" in source
    assert "请输入已支付的美元金额" in source
    assert "付款已自动核验" in source
    assert "订单详情" in source
    assert "使用帮助" in source
