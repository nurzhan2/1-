"""Формирует демонстрационный отчёт на реальном перечне объектов.

Показывает готовую форму выгрузки до подключения к боевым источникам:
объекты берутся из перечня заказчика, а показания генерируются синтетически
и помечены в файле как демонстрационные.

    python tools/make_demo.py
"""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.excel_writer import write_report  # noqa: E402
from app.models import DailyRecord, MeasurePoint  # noqa: E402
from app.points import load_points  # noqa: E402
from app.report import build_report, week_period  # noqa: E402

BANNER = (
    "ДЕМОНСТРАЦИОННЫЙ ФАЙЛ. Перечень объектов — реальный, показания и затраты — "
    "тестовые. Боевые цифры подставятся после опроса ЛЭРС и выдачи доступа к 1С."
)


def synth_daily(points: list[MeasurePoint], start: date, end: date, seed: int = 7):
    """Генерирует правдоподобные суточные архивы для демонстрации формы отчёта."""
    rnd = random.Random(seed)
    daily: dict[int, list[DailyRecord]] = {}
    days = (end - start).days + 1

    for point in points:
        # Часть точек намеренно оставляем без данных — так же, как бывает в реальном опросе
        if rnd.random() < 0.12:
            daily[point.number] = []
            continue

        base_heat = rnd.uniform(1.5, 22.0)
        # Целевой КПД: у большинства норма, у части — ниже порога (для подсветки)
        target_eff = rnd.choice([0.92, 0.89, 0.86, 0.83, 0.78, 0.71, 0.68])
        has_gas = not point.is_consumer and rnd.random() > 0.15

        records: list[DailyRecord] = []
        for offset in range(days):
            if rnd.random() < 0.05:      # пропуск связи с прибором
                continue
            heat = round(base_heat * rnd.uniform(0.85, 1.15), 3)
            gas = None
            if has_gas:
                gas_gcal = heat / target_eff
                gas = round(gas_gcal * 1_000_000 / settings.report.gas_calorific_kcal, 0)
            records.append(
                DailyRecord(
                    point_number=point.number,
                    day=start + timedelta(days=offset),
                    heat_gcal=heat,
                    volume_m3=round(heat * rnd.uniform(18, 26), 2),
                    gas_nm3=gas,
                    source="demo",
                )
            )
        daily[point.number] = records
    return daily


def synth_costs(points: list[MeasurePoint], daily, seed: int = 11) -> dict[str, float]:
    """Затраты «как из 1С»: заполнены не по всем объектам."""
    rnd = random.Random(seed)
    costs: dict[str, float] = {}
    for point in points:
        if rnd.random() < 0.25:
            continue
        heat = sum(r.heat_gcal or 0 for r in daily.get(point.number, []))
        if heat <= 0:
            continue
        costs[point.title] = round(heat * rnd.uniform(2100, 3400), 2)
    return costs


def main() -> int:
    start, end = week_period()
    points = load_points(settings.report.points_file)
    # В демонстрации считаем, что все точки доступны в ЛЭРС
    points = [MeasurePoint(p.number, p.title, lers_id=p.number) for p in points]

    daily = synth_daily(points, start, end)
    costs = synth_costs(points, daily)
    report = build_report(points, daily, costs, start, end)
    report.notes.insert(0, "ФАЙЛ СФОРМИРОВАН НА ДЕМОНСТРАЦИОННЫХ ДАННЫХ.")

    path = settings.report.output_dir / f"Отчет_котельные_ДЕМО_{start:%Y%m%d}-{end:%Y%m%d}.xlsx"
    write_report(
        report,
        path,
        calorific_kcal=settings.report.gas_calorific_kcal,
        threshold=settings.report.efficiency_threshold,
        daily=daily,
        banner=BANNER,
    )
    print(f"Объектов: {len(report.rows)}")
    print(f"Суточных записей: {sum(len(v) for v in daily.values())}")
    print(f"Файл: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
