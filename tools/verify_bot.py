"""Проверка связи с ботом MAX и параметров расписания. Ничего не отправляет."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import settings
from app.max_bot import MaxBot


async def main() -> int:
    s = settings.schedule
    print("Расписание:")
    print(f"   недельный отчёт: день={s.weekly_day}, час={s.weekly_hour}")
    print(f"   месячный отчёт : число={s.monthly_day}, час={s.monthly_hour}")
    print(f"   тайм-зона      : {s.timezone}")

    print("\nПолучатели отчёта (MAX_BROADCAST_CHAT_IDS):")
    ids = settings.max_bot.broadcast_chats
    print(f"   {ids if ids else 'НЕ ЗАДАНЫ — бот не будет никому слать отчёт'}")

    print("\nСвязь с ботом MAX (только чтение, сообщения не отправляются):")
    try:
        async with MaxBot(settings.max_bot) as bot:
            me = await bot.get_me()
            print(f"   бот: {me.get('name')} (@{me.get('username')}), id={me.get('user_id')}")
    except Exception as exc:  # noqa: BLE001 — диагностика, показываем любую ошибку
        print(f"   ОШИБКА: {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
