"""Учёт объектов-потребителей и пометка архивных объектов.

Числа с боевого сервера за 13–19.01.2026:
  №19 Вязники Советская — тепло 12.178 Гкал, газ 2724.1 Нм3 → 54,5 %
  №50 ОРИОН (потребитель) — тепло 8.841 Гкал
  вместе: 21.019 Гкал → 94,1 %
"""
from __future__ import annotations

from datetime import date

from app.models import DailyRecord, MeasurePoint
from app.report import build_report, load_consumers

DAY = date(2026, 1, 13)


def _boiler() -> MeasurePoint:
    return MeasurePoint(number=19, title="г. Вязники Советская", lers_id=119)


def _consumer() -> MeasurePoint:
    return MeasurePoint(number=50, title="г. Вязники_Советская_ОРИОН (потребитель)", lers_id=150)


def _daily() -> dict[int, list[DailyRecord]]:
    return {
        19: [DailyRecord(point_number=19, day=DAY, heat_gcal=12.178, gas_nm3=2724.1)],
        50: [DailyRecord(point_number=50, day=DAY, heat_gcal=8.841)],
    }


def test_без_таблицы_потребителей_тепло_не_складывается():
    report = build_report([_boiler()], _daily(), {}, DAY, DAY)
    row = report.rows[0]

    assert row.heat_gcal == 12.178
    assert "ПРОВЕРИТЬ" in row.comment, "заниженный КПД должен быть помечен"


def test_подтверждённый_потребитель_идёт_в_зачёт():
    report = build_report(
        [_boiler()], _daily(), {}, DAY, DAY, consumers={19: [50]}
    )
    row = report.rows[0]

    assert row.heat_gcal == 21.019, "тепло котельной и потребителя складывается"
    assert row.gas_nm3 == 2724.1, "газ потребителя не дублируется"
    assert "№50" in row.comment
    assert "требует подтверждения" in row.comment
    assert "ПРОВЕРИТЬ" not in row.comment, "94 % — правдоподобно, метки быть не должно"


def test_потребитель_без_данных_не_ломает_расчёт():
    daily = {19: _daily()[19]}
    report = build_report([_boiler()], daily, {}, DAY, DAY, consumers={19: [50]})

    assert report.rows[0].heat_gcal == 12.178


def test_архивный_объект_помечается_и_очищается_от_префикса():
    point = MeasurePoint(number=54, title="ъ_п. им. Кирова_Серебровская СОШ", lers_id=154)
    report = build_report([point], {}, {}, DAY, DAY)
    row = report.rows[0]

    assert not row.title.startswith("ъ_")
    assert "архивный" in row.comment


def test_чтение_таблицы_потребителей(tmp_path):
    path = tmp_path / "consumers.csv"
    path.write_text(
        "# комментарий\nboiler_number;consumer_numbers;comment\n"
        "19;50;основание\n"
        "38;7, 12;две штуки\n"
        "мусор;;\n",
        encoding="utf-8",
    )

    assert load_consumers(path) == {19: [50], 38: [7, 12]}
    assert load_consumers(tmp_path / "нет.csv") == {}
