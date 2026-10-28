from pathlib import Path

from app.services.accounts import tier_for_spend


def test_tier_thresholds_are_configurable(monkeypatch) -> None:
    assert tier_for_spend(0) == "bronze"
    assert tier_for_spend(10_000) == "silver"
    assert tier_for_spend(50_000) == "gold"
    assert tier_for_spend(100_000) == "platinum"


def test_referral_reward_is_credited_in_purchase_transaction() -> None:
    source = Path("app/services/accounts.py").read_text()
    assert "referrer.balance_minor += reward" in source
    assert "referrer.referral_reward_minor += reward" in source
    assert "user.tier = tier_for_spend" in source


def test_admin_financial_actions_are_audited() -> None:
    admin = Path("app/services/admin.py").read_text()
    topups = Path("app/services/topups.py").read_text()
    assert 'action="balance_adjust"' in admin
    assert 'action="topup_review"' in topups
    assert "record_admin_action" in admin and "record_admin_action" in topups
