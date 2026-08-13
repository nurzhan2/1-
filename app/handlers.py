"""Команды бота в MAX."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from .config import Settings
from .max_bot import MaxBot, extract_chat_id, extract_text, extract_user_id
from .report import month_period, week_period
from .service import ReportService, summary_text

log = logging.getLogger(__name__)

COMMANDS = [
    {"name": "report", "description": "Отчёт: /report week | month | ГГГГ-ММ-ДД ГГГГ-ММ-ДД"},
    {"name": "status", "description": "Состояние источников и время последнего опроса"},
    {"name": "help", "description": "Справка по командам"},
]

HELP_TEXT = (
    "Бот формирует сводный отчёт по котельным.\n\n"
    "/report week — за прошедшую неделю (суточные архивы ЛЭРС)\n"
    "/report month — за прошедший месяц (тепло + затраты из 1С)\n"
    "/report 2026-07-01 2026-07-31 — за произвольный период\n"
    "/status — состояние ЛЭРС и 1С, время последнего успешного опроса\n"
    "/help — эта справка\n\n"
    "По расписанию отчёт приходит сам: недельный — по понедельникам, "
    "месячный — в начале месяца."
)


class BotHandlers:
    def __init__(self, bot: MaxBot, service: ReportService, settings: Settings):
        self.bot = bot
        self.service = service
        self.settings = settings
        self._busy = False

    # ---------------------------------------------------------------- маршрутизация
    async def handle(self, update: dict[str, Any]) -> None:
        update_type = update.get("update_type") or update.get("type") or ""
        if update_type not in {"message_created", "bot_started", "message_callback", ""}:
            return

        chat_id = extract_chat_id(update)
        user_id = extract_user_id(update)
        text = extract_text(update)
        # Диагностика: chat_id нужен для настройки авторассылки (MAX_BROADCAST_CHAT_IDS).
        log.info("Входящее: type=%s user_id=%s chat_id=%s text=%r", update_type, user_id, chat_id, text)

        if not self._is_allowed(user_id):
            log.info("Запрос от неразрешённого пользователя %s отклонён", user_id)
            await self._reply("Доступ к боту ограничен. Обратитесь к администратору.", chat_id, user_id)
            return

        if update_type == "bot_started" or not text:
            await self._reply(HELP_TEXT, chat_id, user_id)
            return

        command, _, args = text.partition(" ")
        command = command.lstrip("/").lower()
        args = args.strip()

        if command in {"start", "help"}:
            await self._reply(HELP_TEXT, chat_id, user_id)
        elif command == "status":
            await self._status(chat_id, user_id)
        elif command == "report":
            await self._handle_report(args, chat_id, user_id)
        # Совместимость со старыми командами
        elif command == "month":
            await self._report(*month_period(), chat_id, user_id)
        elif command == "period":
            await self._handle_report(args, chat_id, user_id)
        else:
            await self._reply("Не знаю такой команды.\n\n" + HELP_TEXT, chat_id, user_id)

    async def _handle_report(self, args: str, chat_id, user_id) -> None:
        """Разбирает аргумент /report: week | month | две даты."""
        arg = args.strip().lower()
        if arg in {"", "week", "неделя", "неделю"}:
            await self._report(*week_period(), chat_id, user_id)
        elif arg in {"month", "месяц"}:
            await self._report(*month_period(), chat_id, user_id)
        else:
            period = _parse_period(args)
            if period is None:
                await self._reply(
                    "Формат: /report week | month | 2026-07-01 2026-07-31",
                    chat_id,
                    user_id,
                )
            else:
                await self._report(period[0], period[1], chat_id, user_id)

    # ------------------------------------------------------------------ действия
    async def _report(self, start: date, end: date, chat_id, user_id) -> None:
        if self._busy:
            await self._reply("Уже формирую отчёт, подождите — опрос 55 точек занимает время.", chat_id, user_id)
            return
        self._busy = True
        try:
            await self._reply(
                f"Опрашиваю приборы за {start:%d.%m.%Y} — {end:%d.%m.%Y}. Это займёт 1–3 минуты…",
                chat_id,
                user_id,
            )
            report, _, path = await self.service.collect(start, end)
            await self.bot.send_file(
                path, caption=summary_text(report), chat_id=chat_id, user_id=user_id
            )
        except Exception as exc:
            log.exception("Не удалось сформировать отчёт")
            await self._reply(f"Не удалось сформировать отчёт: {exc}", chat_id, user_id)
        finally:
            self._busy = False

    async def _status(self, chat_id, user_id) -> None:
        from .lers_client import LersClient, LersError
        from .onec_client import OneCClient, OneCError

        lines = ["Проверка источников данных:"]
        try:
            async with LersClient(self.settings.lers) as lers:
                await lers.ping()
                points = await lers.fetch_measure_points()
            lines.append(f"ЛЭРС УЧЁТ — связь есть, доступно точек учёта: {len(points)}")
        except LersError as exc:
            lines.append(f"ЛЭРС УЧЁТ — нет связи: {exc}")
        lines.append(f"  последний успешный опрос ЛЭРС: {self._last_poll('lers')}")

        mode = self.settings.onec.mode
        try:
            costs = await OneCClient(self.settings.onec).fetch_costs(*month_period())
            lines.append(f"1С (режим {mode}) — получено объектов с затратами: {len(costs)}")
        except OneCError as exc:
            lines.append(f"1С (режим {mode}) — нет связи: {exc}")
        lines.append(f"  последний успешный опрос 1С: {self._last_poll('onec')}")

        await self._reply("\n".join(lines), chat_id, user_id)

    def _last_poll(self, source: str) -> str:
        try:
            when = self.service.storage.last_success(source)
        except Exception:  # состояние не критично для ответа /status
            return "неизвестно"
        return when.strftime("%d.%m.%Y %H:%M") if when else "ещё не было"

    # ------------------------------------------------------------------ вспомогательное
    def _is_allowed(self, user_id: Optional[int]) -> bool:
        allowed = self.settings.max_bot.allowed_users
        return not allowed or (user_id in allowed)

    async def _reply(self, text: str, chat_id, user_id) -> None:
        try:
            await self.bot.send_message(text, chat_id=chat_id, user_id=user_id)
        except Exception:
            log.exception("Не удалось отправить сообщение в MAX")


def _parse_period(args: str) -> Optional[tuple[date, date]]:
    parts = args.replace(",", " ").split()
    if len(parts) != 2:
        return None
    parsed: list[date] = []
    for part in parts:
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
            try:
                parsed.append(datetime.strptime(part, fmt).date())
                break
            except ValueError:
                continue
        else:
            return None
    start, end = sorted(parsed)
    return start, end
