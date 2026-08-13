"""Точка входа сервиса.

Режимы запуска:
    python -m app.main                 — бот + планировщик (боевой режим)
    python -m app.main --once week     — сформировать недельный отчёт и выйти
    python -m app.main --once month    — сформировать месячный отчёт и выйти
    python -m app.main --check         — проверить связь с ЛЭРС и 1С
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .handlers import COMMANDS, BotHandlers
from .logging_utils import install_secret_masking
from .max_bot import MaxBot
from .service import ReportService, summary_text

# Пункты, заблокированные заказчиком: показываем как явные предупреждения,
# а не как «нет данных» (требование ТЗ).
BLOCKERS = (
    "Доступность сервера ЛЭРС снаружи не подтверждена — порт 10000 по умолчанию "
    "слушает только локальную сеть.",
    "Доступ к 1С не выдан — затраты по объектам берутся из data/costs.xlsx "
    "(режим manual), пока не выданы адрес базы и учётная запись.",
    "Токен бота MAX заводит заказчик (только на верифицированное юрлицо РФ).",
    "Привязка «котельная → её потребители тепла → её узел учёта газа» не "
    "предоставлена: без неё КПД считать не по чему.",
)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%d.%m.%Y %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    # Секреты не должны попадать в логи ни при каких обстоятельствах.
    install_secret_masking(
        [
            settings.lers.api_key,
            settings.lers.password,
            settings.onec.password,
            settings.max_bot.token,
        ]
    )


log = logging.getLogger("main")


async def run_once(kind: str) -> int:
    service = ReportService(settings)
    report, path = await (service.weekly() if kind == "week" else service.monthly())
    print(summary_text(report))
    print(f"\nФайл: {path}")
    for note in report.notes:
        print(f"  • {note}")
    return 0


async def run_check() -> int:
    from .lers_client import LersClient, LersError
    from .onec_client import OneCClient, OneCError
    from .points import load_points
    from .report import month_period

    points = load_points(settings.report.points_file)
    print(f"Перечень точек учёта: {len(points)} шт.")

    try:
        async with LersClient(settings.lers) as lers:
            info = await lers.ping()
            print(f"ЛЭРС УЧЁТ: связь есть ({info['url']})")
            resolved = await lers.resolve_ids(points)
        found = sum(1 for p in resolved if p.lers_id is not None)
        print(f"ЛЭРС УЧЁТ: сопоставлено точек {found} из {len(points)}")
    except LersError as exc:
        print(f"ЛЭРС УЧЁТ: НЕТ СВЯЗИ — {exc}")

    try:
        costs = await OneCClient(settings.onec).fetch_costs(*month_period())
        print(f"1С (режим {settings.onec.mode}): объектов с затратами — {len(costs)}")
    except OneCError as exc:
        print(f"1С (режим {settings.onec.mode}): НЕТ СВЯЗИ — {exc}")

    print("\nЗаблокировано заказчиком (не «нет данных», а ожидание решений/доступов):")
    for i, item in enumerate(BLOCKERS, start=1):
        print(f"  {i}. {item}")
    return 0


async def run_service() -> int:
    service = ReportService(settings)
    sched_cfg = settings.schedule

    async with MaxBot(settings.max_bot) as bot:
        me = await bot.get_me()
        log.info("Бот запущен: %s (@%s)", me.get("name"), me.get("username"))
        try:
            await bot.set_commands(COMMANDS)
        except Exception as exc:
            log.warning("Не удалось зарегистрировать команды: %s", exc)

        handlers = BotHandlers(bot, service, settings)
        bot.on_update(handlers.handle)

        async def broadcast(kind: str) -> None:
            targets = settings.max_bot.broadcast_chats
            report, path = await (service.weekly() if kind == "week" else service.monthly())
            if not targets:
                log.warning("MAX_BROADCAST_CHAT_IDS не задан — отчёт сформирован, но не разослан")
                return
            # Защита от дублей: если отчёт за этот период уже разослан
            # (например, после перезапуска сработал misfire), не шлём повторно.
            if service.storage.already_sent(kind, report.period_start, report.period_end):
                log.info(
                    "Отчёт (%s) за %s уже разослан ранее — пропуск", kind, report.period_label
                )
                return
            sent_any = False
            for chat_id in targets:
                try:
                    await bot.send_file(path, caption=summary_text(report), chat_id=chat_id)
                    sent_any = True
                except Exception:
                    log.exception("Не удалось отправить отчёт в чат %s", chat_id)
            if sent_any:
                service.storage.mark_sent(kind, report.period_start, report.period_end)

        scheduler = AsyncIOScheduler(timezone=sched_cfg.timezone)
        scheduler.add_job(
            broadcast,
            CronTrigger(day_of_week=sched_cfg.weekly_day, hour=sched_cfg.weekly_hour, minute=0),
            args=["week"],
            id="weekly_lers",
            misfire_grace_time=3600,
        )
        scheduler.add_job(
            broadcast,
            CronTrigger(day=sched_cfg.monthly_day, hour=sched_cfg.monthly_hour, minute=0),
            args=["month"],
            id="monthly_1c",
            misfire_grace_time=7200,
        )
        scheduler.start()
        log.info(
            "Расписание: ЛЭРС — %s %02d:00, 1С — %d-е число %02d:00 (%s)",
            sched_cfg.weekly_day,
            sched_cfg.weekly_hour,
            sched_cfg.monthly_day,
            sched_cfg.monthly_hour,
            sched_cfg.timezone,
        )

        stop = asyncio.Event()
        try:
            await bot.poll(stop_event=stop)
        except (KeyboardInterrupt, asyncio.CancelledError):
            log.info("Остановка сервиса")
        finally:
            scheduler.shutdown(wait=False)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Отчёты по котельным: ЛЭРС УЧЁТ + 1С + бот MAX")
    parser.add_argument("--once", choices=["week", "month"], help="сформировать отчёт и выйти")
    parser.add_argument("--check", action="store_true", help="проверить связь с источниками")
    args = parser.parse_args()

    setup_logging()
    if args.check:
        return asyncio.run(run_check())
    if args.once:
        return asyncio.run(run_once(args.once))
    return asyncio.run(run_service())


if __name__ == "__main__":
    sys.exit(main())
