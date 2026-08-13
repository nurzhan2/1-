"""Состояние сервиса в SQLite (data/archive.sqlite3).

Зачем:
  * суточные записи ЛЭРС сохраняются, поэтому повторный запуск за тот же период
    не теряет уже выгруженные данные (и не зависит от того, что сервер снова
    оказался недоступен);
  * отправленные отчёты помечаются, чтобы после перезапуска планировщик не
    разослал дубль за тот же период;
  * фиксируется время последнего успешного опроса каждого источника — это
    показывает команда ``/status``.

Объём данных маленький (56 точек × 31 день), поэтому используем обычный
синхронный sqlite3: открываем соединение на операцию, всё в одном потоке
цикла событий.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from .models import DailyRecord

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (
    point_number INTEGER NOT NULL,
    day          TEXT    NOT NULL,
    heat_gcal    REAL,
    volume_m3    REAL,
    gas_nm3      REAL,
    source       TEXT    NOT NULL DEFAULT 'lers',
    updated_at   TEXT    NOT NULL,
    PRIMARY KEY (point_number, day, source)
);

CREATE TABLE IF NOT EXISTS poll_state (
    source       TEXT PRIMARY KEY,
    last_success TEXT
);

CREATE TABLE IF NOT EXISTS sent_reports (
    kind         TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    sent_at      TEXT NOT NULL,
    PRIMARY KEY (kind, period_start, period_end)
);
"""


class Storage:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._ready = False

    # ------------------------------------------------------------------ infra
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init(self) -> "Storage":
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
        self._ready = True
        return self

    def _ensure(self) -> None:
        if not self._ready:
            self.init()

    # ---------------------------------------------------------------- суточные
    def save_daily(self, records: Iterable[DailyRecord]) -> int:
        """Сохраняет/обновляет суточные записи. Возвращает число записей."""
        self._ensure()
        now = datetime.now().isoformat(timespec="seconds")
        rows = [
            (r.point_number, r.day.isoformat(), r.heat_gcal, r.volume_m3,
             r.gas_nm3, r.source, now)
            for r in records
        ]
        if not rows:
            return 0
        with closing(self._connect()) as conn, conn:
            conn.executemany(
                """INSERT INTO daily
                     (point_number, day, heat_gcal, volume_m3, gas_nm3, source, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(point_number, day, source) DO UPDATE SET
                     heat_gcal=excluded.heat_gcal,
                     volume_m3=excluded.volume_m3,
                     gas_nm3=excluded.gas_nm3,
                     updated_at=excluded.updated_at""",
                rows,
            )
        return len(rows)

    def load_daily(self, start: date, end: date) -> dict[int, list[DailyRecord]]:
        """Возвращает сохранённые суточные записи за период, сгруппированные по точке."""
        self._ensure()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """SELECT point_number, day, heat_gcal, volume_m3, gas_nm3, source
                     FROM daily
                    WHERE day >= ? AND day <= ?
                    ORDER BY point_number, day""",
                (start.isoformat(), end.isoformat()),
            )
            out: dict[int, list[DailyRecord]] = {}
            for row in cur.fetchall():
                rec = DailyRecord(
                    point_number=row["point_number"],
                    day=date.fromisoformat(row["day"]),
                    heat_gcal=row["heat_gcal"],
                    volume_m3=row["volume_m3"],
                    gas_nm3=row["gas_nm3"],
                    source=row["source"],
                )
                out.setdefault(rec.point_number, []).append(rec)
        return out

    # ------------------------------------------------------- состояние опроса
    def mark_success(self, source: str, when: Optional[datetime] = None) -> None:
        self._ensure()
        ts = (when or datetime.now()).isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO poll_state (source, last_success) VALUES (?, ?)
                   ON CONFLICT(source) DO UPDATE SET last_success=excluded.last_success""",
                (source, ts),
            )

    def last_success(self, source: str) -> Optional[datetime]:
        self._ensure()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                "SELECT last_success FROM poll_state WHERE source = ?", (source,)
            )
            row = cur.fetchone()
        if row and row["last_success"]:
            try:
                return datetime.fromisoformat(row["last_success"])
            except ValueError:  # pragma: no cover
                return None
        return None

    # --------------------------------------------------------- дедуп отчётов
    def already_sent(self, kind: str, start: date, end: date) -> bool:
        self._ensure()
        with closing(self._connect()) as conn:
            cur = conn.execute(
                """SELECT 1 FROM sent_reports
                    WHERE kind = ? AND period_start = ? AND period_end = ?""",
                (kind, start.isoformat(), end.isoformat()),
            )
            return cur.fetchone() is not None

    def mark_sent(self, kind: str, start: date, end: date,
                  when: Optional[datetime] = None) -> None:
        self._ensure()
        ts = (when or datetime.now()).isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO sent_reports (kind, period_start, period_end, sent_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(kind, period_start, period_end) DO UPDATE SET
                     sent_at=excluded.sent_at""",
                (kind, start.isoformat(), end.isoformat(), ts),
            )
