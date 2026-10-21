from app.core.config import Settings


def test_admin_ids_parse_csv() -> None:
    settings = Settings(
        bot_token="token",
        admin_ids="1, 2,3",
        card_encryption_key="key",
        card_fingerprint_key="fingerprint",
    )

    assert settings.admin_ids == (1, 2, 3)
