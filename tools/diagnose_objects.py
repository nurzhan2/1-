"""Разбор объектов с неправдоподобным КПД и проверка измеряемой теплотворности.

Одноразовая диагностика: смотрим состав узлов учёта у проблемных объектов
и наличие параметра CalorificValue (фактическая теплотворность газа).

    python tools\\diagnose_objects.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
BASE = os.getenv("LERS_BASE_URL", "").rstrip("/")
KEY = os.getenv("LERS_API_KEY", "")
START, END = date(2026, 1, 13), date(2026, 1, 19)

TARGETS = [19, 50, 43, 1, 4]


def main() -> int:
    with httpx.Client(
        base_url=BASE + "/api/v1",
        headers={"x-lers-api-key": KEY},
        timeout=httpx.Timeout(60.0, connect=10.0),
    ) as cli:
        nodes = cli.get("/Core/Nodes").json().get("nodes", [])
        points = cli.get("/Core/MeasurePoints").json().get("measurePoints", [])

        by_node: dict[int, list[dict]] = {}
        for mp in points:
            by_node.setdefault(mp.get("nodeId"), []).append(mp)

        for number in TARGETS:
            node = next((n for n in nodes if n.get("number") == number), None)
            if not node:
                print(f"\n№{number}: объект не найден")
                continue

            print("=" * 76)
            print(f"№{number}  {node.get('title')}")
            print(f"   адрес: {node.get('address')}")

            for mp in by_node.get(node.get("id"), []):
                system = mp.get("systemType")
                print(f"   [{mp.get('id'):>4}] {str(mp.get('title'))[:34]:<34} {system}")

                url = f"/Data/MeasurePoints/{mp.get('id')}/Consumption/{START}/{END}"
                resp = cli.get(url, params={"dataTypes": "Day"})
                if resp.status_code != 200:
                    print(f"          данные: HTTP {resp.status_code}")
                    continue
                recs = resp.json().get("dayConsumption") or []
                totals: dict[str, float] = {}
                for rec in recs:
                    for item in rec.get("values", []):
                        name = str(item.get("dataParameter"))
                        value = item.get("value")
                        if value is not None:
                            totals[name] = totals.get(name, 0.0) + float(value)
                keep = {
                    k: round(v, 2)
                    for k, v in totals.items()
                    if k in ("Q_delta", "Q_in", "Q_out", "V", "V_std", "CalorificValue", "M_in")
                }
                calor = totals.get("CalorificValue")
                avg = f"  средняя теплотворность: {calor / len(recs):.0f}" if calor and recs else ""
                print(f"          суток: {len(recs)}  итоги: {keep}{avg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
