"""Сборка отчёта: агрегация суточных архивов ЛЭРС и затрат из 1С."""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable, Optional

from .models import DailyRecord, MeasurePoint, ReportData, ReportRow
from .onec_client import costs_by_number, match_costs

log = logging.getLogger(__name__)


def week_period(today: Optional[date] = None) -> tuple[date, date]:
    """Прошедшая календарная неделя (пн—вс) относительно сегодняшнего дня."""
    today = today or date.today()
    last_sunday = today - timedelta(days=today.weekday() + 1)
    last_monday = last_sunday - timedelta(days=6)
    return last_monday, last_sunday


def month_period(today: Optional[date] = None) -> tuple[date, date]:
    """Прошедший календарный месяц."""
    today = today or date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


def build_report(
    points: Iterable[MeasurePoint],
    daily: dict[int, list[DailyRecord]],
    costs: dict[str, float],
    start: date,
    end: date,
    cost_mapping: Optional[dict[str, int]] = None,
    calorific_kcal: float = 8200.0,
) -> ReportData:
    """Формирует итоговую таблицу по ТЗ.

    ``cost_mapping`` — таблица соответствий «имя 1С → номер котельной». Если задана,
    затраты берутся по номеру котельной; иначе (и как запасной вариант для строк без
    соответствия) — сопоставление по названию.

    ``calorific_kcal`` нужен только для проверки правдоподобности КПД в примечаниях;
    сам КПД считается формулой в Excel, чтобы пересчитываться при правке значения.
    """
    days_expected = (end - start).days + 1
    report = ReportData(period_start=start, period_end=end)
    by_number = costs_by_number(costs, cost_mapping) if cost_mapping else {}

    for point in points:
        records = daily.get(point.number, [])
        # Тепло и газ суммируем только за сутки, где отчитались ОБА прибора.
        # Иначе тепло за 7 суток делится на газ за 6 и КПД уходит выше 100 %.
        # Проверено на боевых данных: объект №4 давал 112,8 % именно из-за этого,
        # после отбрасывания непарных суток — 96,9 %.
        heat_days = {r.day for r in records if r.heat_gcal is not None}
        gas_days = {r.day for r in records if r.gas_nm3 is not None}
        paired = heat_days & gas_days
        skipped = 0
        if heat_days and gas_days and paired:
            skipped = len(heat_days | gas_days) - len(paired)
            heat = _sum([r.heat_gcal for r in records if r.day in paired])
            gas = _sum([r.gas_nm3 for r in records if r.day in paired])
        else:
            heat = _sum([r.heat_gcal for r in records])
            gas = _sum([r.gas_nm3 for r in records])
        days_with_data = len({r.day for r in records if r.heat_gcal is not None or r.gas_nm3 is not None})

        cost = by_number.get(point.number)
        if cost is None:
            cost = match_costs(costs, point.title)
        row = ReportRow(
            title=point.title,
            point_number=point.number,
            heat_gcal=heat,
            gas_nm3=gas,
            costs_rub=cost,
            days_with_data=days_with_data,
            days_expected=days_expected,
        )

        notes: list[str] = []
        if point.lers_id is None:
            notes.append("нет доступа к точке в ЛЭРС")
        elif days_with_data == 0:
            notes.append("нет суточных архивов за период")
        elif row.has_gaps:
            notes.append(f"данные за {days_with_data} из {days_expected} сут.")
        if gas is None:
            notes.append("расход газа не заведён в ЛЭРС")
        if skipped:
            notes.append(
                f"исключено суток без парных показаний: {skipped} "
                "(тепло и газ считаются за одни и те же сутки)"
            )

        # Проверка правдоподобности. КПД выше 100 % физически невозможен;
        # ниже 60 % у газовой котельной почти всегда означает, что часть
        # отпущенного тепла учтена на отдельном объекте-потребителе.
        value = efficiency(heat, gas, calorific_kcal)
        if value is not None:
            if value > 100:
                notes.append(
                    f"ПРОВЕРИТЬ: КПД {value:.0f}% — выше физически возможного, "
                    "сверить состав узлов учёта"
                )
            elif value < 60:
                notes.append(
                    f"ПРОВЕРИТЬ: КПД {value:.0f}% — вероятно, часть тепла "
                    "учитывается на отдельном объекте-потребителе"
                )

        if row.costs_rub is None:
            notes.append("затраты не получены из 1С")
        row.comment = "; ".join(notes)

        report.rows.append(row)

    report.rows.sort(key=lambda r: r.title.lower())
    filled = sum(1 for r in report.rows if r.days_with_data)
    report.notes.append(f"Данные получены по {filled} из {len(report.rows)} точек учёта.")
    if not costs:
        report.onec_ok = False
        report.notes.append(
            "Затраты из 1С за этот период не получены. По ТЗ 1С опрашивается раз "
            "в месяц, поэтому в недельном отчёте колонка затрат обычно пуста — "
            "она заполняется в месячном отчёте."
        )
    return report


def efficiency(
    heat_gcal: Optional[float], gas_nm3: Optional[float], calorific_kcal: float
) -> Optional[float]:
    """КПД котельной, %.

    В ТЗ формула записана как ``тепло / (газ * 8200)``. В чистом виде она не даёт
    процентов: тепло измеряется в Гкал, а произведение «газ × теплотворность» —
    в ккал. Приводим к одной размерности (1 Гкал = 10^6 ккал):

        КПД, % = Q[Гкал] / (V[Нм3] * 8200[ккал/Нм3] / 10^6) * 100
    """
    if not heat_gcal or not gas_nm3 or calorific_kcal <= 0:
        return None
    gas_gcal = gas_nm3 * calorific_kcal / 1_000_000
    if gas_gcal <= 0:
        return None
    return heat_gcal / gas_gcal * 100


def _sum(values: list[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if isinstance(v, (int, float))]
    return round(sum(clean), 3) if clean else None
