from pathlib import Path

from app.core.i18n import LANGUAGE_NAMES, normalize_language, text


def test_supported_language_layout_is_complete() -> None:
    assert tuple(LANGUAGE_NAMES) == ("en", "fr", "ar", "ru", "zh", "es", "id")


def test_language_normalization_follows_telegram_code() -> None:
    assert normalize_language("zh-CN") == "zh"
    assert normalize_language("fr") == "fr"
    assert normalize_language("unknown") == "en"


def test_main_menu_text_formats_live_account_values() -> None:
    rendered = text("zh", "main_menu", balance="12.34", points=8)
    assert "余额：$12.34" in rendered
    assert "积分：8" in rendered


def test_account_configuration_variables_are_documented() -> None:
    source = Path(".env.example").read_text()
    for variable in (
        "BOT_USERNAME",
        "BRAND_NAME",
        "CHANNEL_HANDLE",
        "SUPPORT_HANDLE",
        "ADMINISTRATOR_HANDLE",
        "REFERRAL_REWARD_PERCENT",
        "POINTS_PER_USD",
    ):
        assert f"{variable}=" in source
