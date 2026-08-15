from db import repo
from db.schema import get_connection


def test_get_auto_reply_enabled_returns_default_when_no_rows():
    conn = get_connection(":memory:")
    assert repo.get_auto_reply_enabled(conn, default=False) is False
    assert repo.get_auto_reply_enabled(conn, default=True) is True


def test_set_auto_reply_enabled_overrides_default():
    conn = get_connection(":memory:")
    repo.set_auto_reply_enabled(conn, enabled=True, changed_by=111)
    assert repo.get_auto_reply_enabled(conn, default=False) is True


def test_get_auto_reply_enabled_uses_latest_row():
    conn = get_connection(":memory:")
    repo.set_auto_reply_enabled(conn, enabled=True, changed_by=111)
    repo.set_auto_reply_enabled(conn, enabled=False, changed_by=111)
    assert repo.get_auto_reply_enabled(conn, default=True) is False
