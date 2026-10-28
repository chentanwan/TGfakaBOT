from __future__ import annotations

SUPPORTED_LANGUAGES = ("en", "fr", "ar", "ru", "zh", "es", "id")
LANGUAGE_NAMES = {
    "en": "🇬🇧 English",
    "fr": "🇫🇷 Français",
    "ar": "🇸🇦 العربية",
    "ru": "🇷🇺 Русский",
    "zh": "🇨🇳 中文",
    "es": "🇪🇸 Español",
    "id": "🇮🇩 Bahasa Indonesia",
}

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "welcome": "🎉 Welcome!\n📣 Channel: {channel}\n🆘 Support: {support}",
        "main_menu": "Main menu\n💵 Balance: ${balance} | ⭐ Points: {points}",
        "products": "🛍️ Products",
        "balance": "💰 My balance",
        "topup": "💳 Top up",
        "methods": "📖 Methods",
        "profile": "👤 Profile",
        "api": "🔗 API",
        "language": "🌐 Language",
        "support": "🆘 Support",
        "back": "« Back",
        "choose_language": "🌐 Choose your language:",
    },
    "zh": {
        "welcome": "🎉 欢迎来到!\n📣 频道：{channel}\n🆘 客服：{support}",
        "main_menu": "主菜单\n💵 余额：${balance} | ⭐ 积分：{points}",
        "products": "🛍️ 商品",
        "balance": "💰 我的余额",
        "topup": "💳 充值",
        "methods": "📖 方法",
        "profile": "👤 个人资料",
        "api": "🔗 API",
        "language": "🌐 语言",
        "support": "🆘 客服",
        "back": "« 返回",
        "choose_language": "🌐 请选择您的语言：",
    },
}

# Until each translation is professionally reviewed, non-Chinese languages use
# the complete English catalog rather than scattering untranslated literals.
for _language in SUPPORTED_LANGUAGES:
    MESSAGES.setdefault(_language, dict(MESSAGES["en"]))


def normalize_language(language_code: str | None, fallback: str = "en") -> str:
    candidate = (language_code or "").split("-", 1)[0].lower()
    if candidate in SUPPORTED_LANGUAGES:
        return candidate
    return fallback if fallback in SUPPORTED_LANGUAGES else "en"


def text(language: str, key: str, **values: object) -> str:
    catalog = MESSAGES.get(normalize_language(language), MESSAGES["en"])
    template = catalog.get(key, MESSAGES["en"].get(key, key))
    return template.format(**values)
