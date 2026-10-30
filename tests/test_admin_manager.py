from pathlib import Path


def test_dynamic_admins_are_database_backed_and_root_protected() -> None:
    access = Path("app/services/admin_access.py").read_text()
    plugin = Path("plugins/admin_manager/plugin.py").read_text()
    assert "BotAdministrator" in access
    assert "root_admin_ids() | _dynamic_admin_ids" in access
    assert "is_root_admin(telegram_id)" in plugin
    assert "不能删除自己的管理员权限" in plugin
    assert 'action="administrator_add"' in plugin
    assert 'action="administrator_remove"' in plugin


def test_all_admin_handlers_use_dynamic_authorization() -> None:
    for path in (
        "app/handlers/admin.py",
        "app/handlers/payment_admin.py",
        "app/handlers/charge_admin.py",
        "plugins/prodseller/plugin.py",
    ):
        source = Path(path).read_text()
        assert "settings.admin_ids" not in source
        assert "get_settings().admin_ids" not in source


def test_status_notifications_have_external_offline_watchdog() -> None:
    main = Path("app/main.py").read_text()
    compose = Path("docker-compose.yml").read_text()
    watchdog = Path("app/status_watchdog.py").read_text()
    assert "notify_online" in main
    assert "notify_stopping" in main
    assert "heartbeat_loop" in main
    assert "status-watchdog:" in compose
    assert "HEARTBEAT_KEY" in watchdog
    assert '"offline"' in watchdog
