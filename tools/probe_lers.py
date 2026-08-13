"""Диагностика подключения к серверу ЛЭРС УЧЁТ.

Запускать с машины, у которой есть доступ к серверу заказчика:

    python tools/probe_lers.py                 # проверка связи + сопоставление точек
    python tools/probe_lers.py --point 39      # выгрузить сутки по одной точке
    python tools/probe_lers.py --days 7        # период проверки, по умолчанию 3 дня

Скрипт печатает, какой именно маршрут API сработал, и сохраняет сырой ответ
в out/lers_raw_<точка>.json — это снимает вопросы о структуре данных
при разборе с технической службой заказчика.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.lers_client import CONSUMPTION_ROUTES, LersClient, LersError  # noqa: E402
from app.points import load_points  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--point", type=int, help="номер точки учёта из перечня")
    parser.add_argument("--days", type=int, default=3, help="за сколько последних суток")
    args = parser.parse_args()

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=args.days - 1)

    points = load_points(settings.report.points_file)
    print(f"Перечень заказчика: {len(points)} точек учёта")
    print(f"Сервер: {settings.lers.base_url}")
    print(f"Ключ API: {'задан' if settings.lers.api_key else 'НЕ ЗАДАН'}\n")

    try:
        async with LersClient(settings.lers) as lers:
            info = await lers.ping()
            print(f"[OK] Связь есть. Рабочая точка входа: {info['url']}\n")

            raw = await lers.fetch_measure_points()
            print(f"[OK] Учётной записи доступно точек учёта: {len(raw)}")
            if raw:
                print(f"     Пример объекта API: {json.dumps(raw[0], ensure_ascii=False)[:400]}\n")

            resolved = await lers.resolve_ids(points)
            found = [p for p in resolved if p.lers_id is not None]
            missing = [p for p in resolved if p.lers_id is None]
            print(f"[OK] Сопоставлено по номеру: {len(found)} из {len(points)}")
            if missing:
                print("[!] Не найдены / нет прав на точки:")
                for p in missing:
                    print(f"    №{p.number:<4} {p.title}")
            print()

            targets = [p for p in found if args.point is None or p.number == args.point]
            if args.point is not None and not targets:
                print(f"[!] Точка №{args.point} недоступна")
                return 1

            probe = targets[: 1 if args.point else 3]
            out_dir = settings.report.output_dir
            out_dir.mkdir(parents=True, exist_ok=True)

            for point in probe:
                records = await lers.fetch_daily(point, start, end)
                print(f"Точка №{point.number} ({point.title[:45]}): суток получено {len(records)}")
                for rec in records:
                    print(
                        f"    {rec.day:%d.%m.%Y}  Q={rec.heat_gcal}  V={rec.volume_m3}  газ={rec.gas_nm3}"
                    )
                dump = out_dir / f"lers_raw_{point.number}.json"
                dump.write_text(
                    json.dumps([rec.__dict__ for rec in records], ensure_ascii=False, default=str, indent=2),
                    encoding="utf-8",
                )
                print(f"    сырые данные -> {dump}")

    except LersError as exc:
        print(f"[ОШИБКА] {exc}\n")
        print("Проверьте по порядку:")
        print("  1. Открыт ли порт 10000 снаружи (по умолчанию ЛЭРС слушает только LAN).")
        print("  2. Выпущен ли ключ приложения для учётной записи (вкладка «Приложения»).")
        print("  3. Есть ли у учётной записи права на нужные объекты учёта.")
        print(f"  4. Список доступных методов: {settings.lers.base_url}/api/swagger")
        print(f"\nПеребирались маршруты данных: {len(CONSUMPTION_ROUTES)} вариантов.")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
