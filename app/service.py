"""Прикладной слой: опрос источников и формирование готового файла отчёта."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .config import Settings
from .excel_writer import default_filename, write_report
from .lers_client import LersClient, LersError
from .models import DailyRecord, ReportData
from .onec_client import OneCClient, OneCError, load_cost_mapping
from .points import load_points
from .report import build_report, load_consumers, month_period, week_period
from .storage import Storage

log = logging.getLogger(__name__)


class ReportService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = Storage(settings.db_path)

    async def collect(self, start: date, end: date) -> tuple[ReportData, dict, Path]:
        """Опрашивает ЛЭРС и 1С за период и сохраняет .xlsx.

        Суточные архивы кладутся в SQLite, а отчёт затем собирается из базы —
        так повторный запуск не теряет ранее выгруженные данные и даёт цифры
        даже если сервер ЛЭРС в этот раз недоступен.

        Возвращает (данные отчёта, суточные архивы, путь к файлу).
        """
        self.storage.init()
        points = load_points(self.settings.report.points_file)
        log.info("Период %s — %s, точек учёта: %d", start, end, len(points))

        lers_ok = True
        try:
            async with LersClient(self.settings.lers) as lers:
                await lers.ping()
                points = await lers.resolve_ids(points)
                fetched = await lers.fetch_daily_bulk(points, start, end)
            saved = self.storage.save_daily(
                rec for recs in fetched.values() for rec in recs
            )
            self.storage.mark_success("lers")
            log.info("Сохранено суточных записей в БД: %d", saved)
        except LersError as exc:
            lers_ok = False
            log.error("ЛЭРС недоступен: %s", exc)

        # Отчёт собираем из БД: это объединение свежих и ранее сохранённых данных.
        daily: dict[int, list[DailyRecord]] = self.storage.load_daily(start, end)

        costs: dict[str, float] = {}
        onec_ok = True
        try:
            costs = await OneCClient(self.settings.onec).fetch_costs(start, end)
            if costs:
                self.storage.mark_success("onec")
        except OneCError as exc:
            onec_ok = False
            log.error("1С недоступна: %s", exc)

        cost_mapping = load_cost_mapping(self.settings.onec.mapping_file)
        consumers = load_consumers(self.settings.report.points_file.parent / "consumers.csv")
        if consumers:
            log.info("Объекты-потребители в зачёт котельных: %s", consumers)
        report = build_report(
            points,
            daily,
            costs,
            start,
            end,
            cost_mapping=cost_mapping,
            calorific_kcal=self.settings.report.gas_calorific_kcal,
            consumers=consumers,
        )
        report.lers_ok = lers_ok
        report.onec_ok = report.onec_ok and onec_ok
        if not lers_ok:
            if daily:
                report.notes.insert(
                    0,
                    "Сервер ЛЭРС УЧЁТ недоступен — в отчёте использованы ранее "
                    "сохранённые суточные данные из локальной базы.",
                )
            else:
                report.notes.insert(
                    0, "Сервер ЛЭРС УЧЁТ недоступен — данные по теплу не получены."
                )

        path = self.settings.report.output_dir / default_filename(start, end)
        write_report(
            report,
            path,
            calorific_kcal=self.settings.report.gas_calorific_kcal,
            threshold=self.settings.report.efficiency_threshold,
            daily=daily,
        )
        return report, daily, path

    async def weekly(self, today: date | None = None) -> tuple[ReportData, Path]:
        start, end = week_period(today)
        report, _, path = await self.collect(start, end)
        return report, path

    async def monthly(self, today: date | None = None) -> tuple[ReportData, Path]:
        start, end = month_period(today)
        report, _, path = await self.collect(start, end)
        return report, path


def summary_text(report: ReportData) -> str:
    """Короткое текстовое резюме, которое бот отправляет вместе с файлом."""
    filled = sum(1 for r in report.rows if r.days_with_data)
    heat = sum(r.heat_gcal or 0 for r in report.rows)
    lines = [
        f"Отчёт по котельным за {report.period_label}",
        f"Объектов в отчёте: {len(report.rows)}, с данными: {filled}",
        f"Суммарное потребление тепла: {heat:,.1f} Гкал".replace(",", " "),
    ]
    if not report.lers_ok:
        lines.append("Внимание: сервер ЛЭРС был недоступен при формировании.")
    if not report.onec_ok:
        lines.append("Внимание: затраты из 1С не получены.")
    return "\n".join(lines)
