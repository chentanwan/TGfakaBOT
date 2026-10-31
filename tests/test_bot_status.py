from app.services.bot_status import _status_text


def test_bot_status_messages_are_compact_and_consistent() -> None:
    online = _status_text("online")
    offline = _status_text("offline")
    recovered = _status_text("recovered")
    stopping = _status_text("stopping")

    assert "🟢 <b>运行正常</b>" in online
    assert "🔴 <b>服务异常</b>" in offline
    assert "🟢 <b>服务已恢复</b>" in recovered
    assert "🟡 <b>服务重启</b>" in stopping
    for text in (online, offline, recovered, stopping):
        assert "🕒 <code>" in text
        assert "🖥 <code>" in text
        assert "Bot 状态通知" not in text
        assert len(text.splitlines()) == 4


def test_bot_status_custom_detail_is_html_escaped() -> None:
    text = _status_text("offline", "RPC <down> & retry")
    assert "RPC &lt;down&gt; &amp; retry" in text
