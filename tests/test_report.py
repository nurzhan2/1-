"""Расчёт КПД и сборка отчёта."""
from __future__ import annotations

from datetime import date

from app.models import DailyRecord, MeasurePoint
from app.report import build_report, efficiency, month_period, week_period


# ------------------------------------------------------------------- КПД
def test_efficiency_normal():
    # Q=1 Гкал, газ=125 Нм3, теплотворность=8000 -> газ=1 Гкал -> КПД=100%
    assert efficiency(1.0, 125.0, 8000.0) == 100.0


def test_efficiency_below_100():
    eff = efficiency(0.8, 125.0, 8000.0)
    assert round(eff, 3) == 80.0


def test_efficiency_zero_gas_returns_none():
    assert efficiency(1.2, 0.0, 8200.0) is None


def test_efficiency_none_values_return_none():
    assert efficiency(None, 100.0, 8200.0) is None
    assert efficiency(1.2, None, 8200.0) is None
    assert efficiency(None, None, 8200.0) is None


def test_efficiency_zero_calorific_returns_none():
    assert efficiency(1.2, 100.0, 0.0) is None


# --------------------------------------------------------------- периоды
def test_week_period_is_previous_mon_sun():
    # Среда 2026-08-12 -> прошлая неделя пн 03.08 .. вс 09.08
    start, end = week_period(date(2026, 8, 12))
    assert start == date(2026, 8, 3)
    assert end == date(2026, 8, 9)
    assert start.weekday() == 0 and end.weekday() == 6


def test_month_period_is_previous_month():
    start, end = month_period(date(2026, 8, 11))
    assert start == date(2026, 7, 1)
    assert end == date(2026, 7, 31)


# --------------------------------------------------------------- build_report
def test_build_report_aggregates_and_flags_gaps():
    points = [MeasurePoint(number=39, title="Котельная A", lers_id=1001)]
    daily = {
        39: [
            DailyRecord(39, date(2026, 8, 3), heat_gcal=1.2, gas_nm3=200.0),
            DailyRecord(39, date(2026, 8, 4), heat_gcal=1.4, gas_nm3=210.0),
        ]
    }
    report = build_report(points, daily, costs={}, start=date(2026, 8, 3), end=date(2026, 8, 5))
    row = report.rows[0]
    assert row.heat_gcal == 2.6
    assert row.gas_nm3 == 410.0
    # 2 суток из 3 -> есть пропуск
    assert row.days_with_data == 2
    assert row.days_expected == 3
    assert row.has_gaps
    assert report.onec_ok is False  # затраты не переданы


def test_build_report_marks_missing_lers_access():
    points = [MeasurePoint(number=39, title="Котельная A", lers_id=None)]
    report = build_report(points, {}, {}, date(2026, 8, 3), date(2026, 8, 5))
    assert "нет доступа" in report.rows[0].comment.lower()


def test_build_report_matches_costs_by_name():
    points = [MeasurePoint(number=39, title="Котельная на ул. Строителей", lers_id=1001)]
    daily = {39: [DailyRecord(39, date(2026, 8, 3), heat_gcal=1.0, gas_nm3=100.0)]}
    costs = {"Котельная на улице Строителей": 12345.0}
    report = build_report(points, daily, costs, date(2026, 8, 3), date(2026, 8, 3))
    assert report.rows[0].costs_rub == 12345.0
