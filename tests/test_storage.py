"""Состояние в SQLite: суточные записи, дедуп рассылки, время опроса."""
from __future__ import annotations

from datetime import date, datetime

from app.models import DailyRecord
from app.storage import Storage


def _storage(tmp_path) -> Storage:
    return Storage(tmp_path / "archive.sqlite3").init()


def test_save_and_load_daily(tmp_path):
    st = _storage(tmp_path)
    records = [
        DailyRecord(39, date(2026, 8, 3), heat_gcal=1.2, gas_nm3=200.0),
        DailyRecord(39, date(2026, 8, 4), heat_gcal=1.4, gas_nm3=210.0),
        DailyRecord(32, date(2026, 8, 3), heat_gcal=5.0),
    ]
    assert st.save_daily(records) == 3

    loaded = st.load_daily(date(2026, 8, 3), date(2026, 8, 4))
    assert set(loaded) == {39, 32}
    assert len(loaded[39]) == 2
    assert loaded[39][0].heat_gcal == 1.2


def test_load_respects_period(tmp_path):
    st = _storage(tmp_path)
    st.save_daily([
        DailyRecord(39, date(2026, 8, 1), heat_gcal=1.0),
        DailyRecord(39, date(2026, 8, 10), heat_gcal=2.0),
    ])
    loaded = st.load_daily(date(2026, 8, 2), date(2026, 8, 9))
    assert loaded == {}


def test_upsert_overwrites_same_key(tmp_path):
    st = _storage(tmp_path)
    st.save_daily([DailyRecord(39, date(2026, 8, 3), heat_gcal=1.0)])
    st.save_daily([DailyRecord(39, date(2026, 8, 3), heat_gcal=9.9)])
    loaded = st.load_daily(date(2026, 8, 3), date(2026, 8, 3))
    assert len(loaded[39]) == 1
    assert loaded[39][0].heat_gcal == 9.9


def test_poll_state(tmp_path):
    st = _storage(tmp_path)
    assert st.last_success("lers") is None
    when = datetime(2026, 8, 11, 7, 0, 0)
    st.mark_success("lers", when)
    assert st.last_success("lers") == when


def test_sent_reports_dedup(tmp_path):
    st = _storage(tmp_path)
    start, end = date(2026, 8, 3), date(2026, 8, 9)
    assert st.already_sent("week", start, end) is False
    st.mark_sent("week", start, end)
    assert st.already_sent("week", start, end) is True
    # Другой период — не считается отправленным
    assert st.already_sent("week", date(2026, 8, 10), date(2026, 8, 16)) is False


def test_init_is_lazy(tmp_path):
    # Методы работают и без явного init()
    st = Storage(tmp_path / "auto.sqlite3")
    st.save_daily([DailyRecord(1, date(2026, 8, 3), heat_gcal=1.0)])
    assert st.load_daily(date(2026, 8, 3), date(2026, 8, 3))
