"""Сведение тепла и газа за парные сутки и проверка правдоподобности КПД.

Числа взяты с боевого сервера заказчика за 13–19.01.2026 — на них и была
обнаружена проблема: тепло за 7 суток, делённое на газ за 6, давало 112,8 %.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.models import DailyRecord, MeasurePoint
from app.report import build_report, efficiency


def _point(number: int = 4, title: str = "с. Филипповское") -> MeasurePoint:
    return MeasurePoint(number=number, title=title, lers_id=100 + number)


def _records(pairs: list[tuple[int, float | None, float | None]]) -> list[DailyRecord]:
    return [
        DailyRecord(point_number=4, day=date(2026, 1, day), heat_gcal=heat, gas_nm3=gas)
        for day, heat, gas in pairs
    ]


def test_непарные_сутки_исключаются_из_расчёта():
    """Сутки, где отчитался только тепловой счётчик, в сумму не попадают."""
    records = _records(
        [
            (13, 3.0, 400.0),
            (14, 3.0, 400.0),
            (15, 3.3, None),  # газ молчал — сутки исключаются целиком
        ]
    )
    report = build_report([_point()], {4: records}, {}, date(2026, 1, 13), date(2026, 1, 15))
    row = report.rows[0]

    assert row.heat_gcal == 6.0, "тепло за непарные сутки не должно суммироваться"
    assert row.gas_nm3 == 800.0
    assert "исключено суток без парных показаний: 1" in row.comment


def test_полные_данные_не_режутся():
    records = _records([(13, 3.0, 400.0), (14, 3.0, 400.0)])
    report = build_report([_point()], {4: records}, {}, date(2026, 1, 13), date(2026, 1, 14))
    row = report.rows[0]

    assert row.heat_gcal == 6.0
    assert "исключено суток" not in row.comment


def test_кпд_выше_ста_помечается():
    """Даже при парных сутках завышенный КПД должен быть виден в примечании."""
    records = _records([(13, 10.0, 400.0)])
    report = build_report([_point()], {4: records}, {}, date(2026, 1, 13), date(2026, 1, 13))

    assert "ПРОВЕРИТЬ" in report.rows[0].comment
    assert "выше физически возможного" in report.rows[0].comment


def test_заниженный_кпд_помечается():
    """Вязники Советская: 12.178 Гкал против 2724.1 Нм3 — это 54,5 %."""
    records = _records([(13, 12.178, 2724.1)])
    point = MeasurePoint(number=19, title="г. Вязники Советская", lers_id=119)
    report = build_report([point], {19: records}, {}, date(2026, 1, 13), date(2026, 1, 13))

    assert "ПРОВЕРИТЬ" in report.rows[0].comment
    assert "объекте-потребителе" in report.rows[0].comment


def test_нормальный_кпд_не_помечается():
    """Добрынское: 35.564 Гкал против 5002.4 Нм3 — 86,7 %, замечаний быть не должно."""
    records = _records([(13, 35.564, 5002.4)])
    point = MeasurePoint(number=1, title="с. Добрынское школа", lers_id=101)
    report = build_report([point], {1: records}, {}, date(2026, 1, 13), date(2026, 1, 13))

    assert "ПРОВЕРИТЬ" not in report.rows[0].comment


def test_формула_кпд_на_боевых_числах():
    """Контроль единиц: 1 Гкал = 10^6 ккал, иначе результат меньше в миллион раз."""
    assert efficiency(35.564, 5002.4, 8200.0) == pytest.approx(86.7, abs=0.05)
    assert efficiency(12.178, 2724.1, 8200.0) == pytest.approx(54.5, abs=0.05)
    assert efficiency(None, 5002.4, 8200.0) is None
    assert efficiency(35.564, None, 8200.0) is None
