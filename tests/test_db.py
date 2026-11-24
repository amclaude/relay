"""Tests for the database layer."""

from __future__ import annotations

from datetime import timedelta

from relay.core.db import Database, utcnow


def test_ensure_user_is_idempotent(services):
    first = services.db.ensure_user(1)
    second = services.db.ensure_user(1)
    assert first.telegram_id == second.telegram_id == 1


def test_user_settings_round_trip(services):
    assert services.db.get_user_settings(5) == {}
    services.db.set_user_settings(5, {"theme": "dark"})
    assert services.db.get_user_settings(5) == {"theme": "dark"}


def test_upsert_creates_account(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    account = services.db.get_gmail_account(1)
    assert account.email == "a@example.com"
    assert account.encrypted_refresh_token == "cipher"
    assert account.alerts_enabled is False


def test_upsert_keeps_alert_settings(services):
    services.db.upsert_gmail_account(1, "a@example.com", "old")
    services.db.set_alerts(1, True)
    services.db.set_filters(1, {"query": "label:work"})
    services.db.upsert_gmail_account(1, "b@example.com", "new")
    account = services.db.get_gmail_account(1)
    assert account.email == "b@example.com"
    assert account.encrypted_refresh_token == "new"
    assert account.alerts_enabled is True
    assert services.db.get_filters(1) == {"query": "label:work"}


def test_accounts_are_separate_for_each_user(services):
    services.db.upsert_gmail_account(1, "a@example.com", "one")
    services.db.upsert_gmail_account(2, "b@example.com", "two")
    assert services.db.get_gmail_account(1).email == "a@example.com"
    assert services.db.get_gmail_account(2).email == "b@example.com"


def test_missing_account_is_none(services):
    assert services.db.get_gmail_account(404) is None


def test_delete_account(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    assert services.db.delete_gmail_account(1) is True
    assert services.db.get_gmail_account(1) is None
    assert services.db.delete_gmail_account(1) is False


def test_set_alerts_needs_an_account(services):
    assert services.db.set_alerts(1, True) is False


def test_alerts_on_resets_last_checked(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    before = utcnow()
    services.db.set_alerts(1, True)
    assert services.db.get_gmail_account(1).last_checked >= before


def test_list_alert_accounts_only_returns_enabled(services):
    services.db.upsert_gmail_account(1, "a@example.com", "one")
    services.db.upsert_gmail_account(2, "b@example.com", "two")
    services.db.set_alerts(2, True)
    assert [a.telegram_id for a in services.db.list_alert_accounts()] == [2]


def test_touch_last_checked(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    moment = utcnow() + timedelta(hours=1)
    services.db.touch_last_checked(1, moment)
    assert services.db.get_gmail_account(1).last_checked.replace(microsecond=0) == moment.replace(microsecond=0)


def test_filters_round_trip(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    assert services.db.get_filters(1) == {}
    assert services.db.set_filters(1, {"query": "is:starred"}) is True
    assert services.db.get_filters(1) == {"query": "is:starred"}
    assert services.db.set_filters(99, {}) is False


def test_delete_user_data_removes_everything(services):
    services.db.upsert_gmail_account(1, "a@example.com", "cipher")
    services.db.save_oauth_state("pending", 1, utcnow() + timedelta(minutes=5))
    services.db.upsert_gmail_account(2, "b@example.com", "other")
    services.db.delete_user_data(1)
    assert services.db.get_gmail_account(1) is None
    assert services.db.pop_oauth_state("pending") is None
    assert services.db.get_gmail_account(2) is not None


def test_pop_oauth_state_deletes_the_row(services):
    expires = utcnow() + timedelta(minutes=5)
    services.db.save_oauth_state("abc", 7, expires)
    found = services.db.pop_oauth_state("abc")
    assert found is not None
    assert found[0] == 7
    assert found[1].tzinfo is not None
    assert services.db.pop_oauth_state("abc") is None


def test_purge_expired_states(services):
    now = utcnow()
    services.db.save_oauth_state("old", 1, now - timedelta(minutes=1))
    services.db.save_oauth_state("new", 1, now + timedelta(minutes=5))
    assert services.db.purge_expired_states(now) == 1
    assert services.db.pop_oauth_state("old") is None
    assert services.db.pop_oauth_state("new") is not None


def test_file_database_creates_parent_folder(tmp_path):
    path = tmp_path / "nested" / "dir" / "app.db"
    db = Database(path)
    db.create_all()
    db.ensure_user(1)
    assert path.exists()
