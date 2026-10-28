from pathlib import Path


def test_initial_schema_marks_current_metadata_bootstrap() -> None:
    source = Path("migrations/versions/bb4a86adf422_initial_schema.py").read_text()
    assert "Base.metadata.create_all" in source
    assert "_tgfakabot_schema_bootstrap" in source


def test_followup_migrations_skip_after_metadata_bootstrap() -> None:
    migrations = list(Path("migrations/versions").glob("*.py"))
    followups = [p for p in migrations if not p.name.startswith("bb4a86adf422")]
    assert followups
    for path in followups:
        source = path.read_text()
        assert "SELECT to_regclass('_tgfakabot_schema_bootstrap')" in source, path.name
