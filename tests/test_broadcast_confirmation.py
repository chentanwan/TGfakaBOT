from pathlib import Path


def test_broadcast_requires_confirmation_and_can_cancel() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert "AdminBroadcast.confirmation" in source
    assert "adm:broadcast:confirm" in source
    assert "adm:broadcast:cancel" in source
    assert "broadcast_content" in source


def test_broadcast_obeys_retry_after() -> None:
    source = Path("app/handlers/admin.py").read_text()
    assert "except TelegramRetryAfter as exc" in source
    assert "await asyncio.sleep(exc.retry_after)" in source
