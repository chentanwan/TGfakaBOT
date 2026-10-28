from app.core.i18n import MESSAGES, SUPPORTED_LANGUAGES, text


def test_every_supported_language_has_complete_menu_catalog() -> None:
    required = set(MESSAGES["en"])
    assert set(MESSAGES) == set(SUPPORTED_LANGUAGES)
    for language in SUPPORTED_LANGUAGES:
        assert required <= set(MESSAGES[language])
        assert text(language, "products") != "products"


def test_bot_menu_contains_only_start_and_help() -> None:
    source = open("app/main.py").read()
    assert 'BotCommand(command="start"' in source
    assert 'BotCommand(command="help"' in source
    assert 'BotCommand(command="shop"' not in source
