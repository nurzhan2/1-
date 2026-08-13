"""Доменные модели."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class MeasurePoint:
    """Точка учёта из перечня заказчика."""

    number: int          # номер точки учёта в ЛЭРС (колонка «Номер»)
    title: str           # наименование объекта
    lers_id: Optional[int] = None   # внутренний Id точки в ЛЭРС (заполняется при сопоставлении)

    @property
    def is_consumer(self) -> bool:
        """Точки-потребители помечены в перечне словом «потребитель»."""
        return "потребител" in self.title.lower()


@dataclass
class DailyRecord:
    """Одна суточная запись архива по точке учёта."""

    point_number: int
    day: date
    heat_gcal: Optional[float] = None      # Q, Гкал
    volume_m3: Optional[float] = None      # V, м3 (теплоноситель)
    gas_nm3: Optional[float] = None        # V газа, Нм3
    source: str = "lers"


@dataclass
class ReportRow:
    """Строка итоговой таблицы по ТЗ."""

    title: str
    point_number: Optional[int] = None
    heat_gcal: Optional[float] = None
    gas_nm3: Optional[float] = None
    costs_rub: Optional[float] = None
    days_with_data: int = 0
    days_expected: int = 0
    comment: str = ""

    @property
    def has_gaps(self) -> bool:
        return self.days_expected > 0 and self.days_with_data < self.days_expected


@dataclass
class ReportData:
    """Готовый набор данных для выгрузки в Excel."""

    period_start: date
    period_end: date
    rows: list[ReportRow] = field(default_factory=list)
    lers_ok: bool = True
    onec_ok: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def period_label(self) -> str:
        return f"{self.period_start:%d.%m.%Y} — {self.period_end:%d.%m.%Y}"
