"""Интеграция ReportService: опрос ЛЭРС, сохранение в SQLite, устойчивость."""
from __future__ import annotations

from datetime import date

import httpx
import respx

from app.config import LersSettings, Settings
from app.models import DailyRecord
from app.service import ReportService
from app.storage import Storage

from .conftest import DATA_DIR, load_fixture, make_onec_settings

START = date(2026, 8, 3)
END = date(2026, 8, 9)


def _settings(tmp_path):
    from app.config import ReportSettings

    return Settings(
        lers=LersSettings(
            base_url="http://lers.test:10000", api_key="K",
            retries=2, retry_base_delay=0.0, connect_timeout=2, timeout=5,
        ),
        onec=make_onec_settings(mode="manual", manual_file=tmp_path / "нет-затрат.xlsx"),
        report=ReportSettings(
            gas_calorific_kcal=8200.0, efficiency_threshold=80.0,
            points_file=DATA_DIR / "points.xlsx", output_dir=tmp_path / "out",
        ),
        db_path=tmp_path / "archive.sqlite3",
    )


async def test_collect_fetches_saves_and_writes(tmp_path, isolated_routes_cache):
    settings = _settings(tmp_path)
    root = settings.lers.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{root}/ServerInfo").mock(return_value=httpx.Response(200, json={"v": 1}))
        mock.get(url__regex=rf"{root}/Core/Nodes.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_nodes.json"))
        )
        mock.get(url__regex=rf"{root}/Core/MeasurePoints.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_points.json"))
        )
        mock.get(url__regex=rf"{root}/Data/MeasurePoints/\d+/Consumption/.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_consumption_flat.json"))
        )
        service = ReportService(settings)
        report, daily, path = await service.collect(START, END)

    assert path.exists()
    # Точки из фикстуры (39, 32, 19) получили данные
    assert sum(1 for r in report.rows if r.days_with_data) == 3
    # Данные осели в SQLite
    stored = Storage(settings.db_path).load_daily(START, END)
    assert len(stored) == 3
    assert service.storage.last_success("lers") is not None


async def test_collect_uses_stored_data_when_lers_down(tmp_path, isolated_routes_cache):
    settings = _settings(tmp_path)
    # Заранее кладём данные в базу (как будто прошлый успешный опрос)
    Storage(settings.db_path).init().save_daily([
        DailyRecord(39, date(2026, 8, 3), heat_gcal=2.0, gas_nm3=300.0),
        DailyRecord(39, date(2026, 8, 4), heat_gcal=2.1, gas_nm3=310.0),
    ])

    root = settings.lers.api_root
    with respx.mock(assert_all_called=False) as mock:
        # Сервер «лежит»: на все ping-адреса отвечает 503 -> ping бросит LersError
        mock.get(url__regex=rf"{root}/.*").mock(return_value=httpx.Response(503))
        service = ReportService(settings)
        report, daily, path = await service.collect(START, END)

    assert report.lers_ok is False
    assert path.exists()
    # Данные из базы попали в отчёт
    assert 39 in daily and len(daily[39]) == 2
    assert any("ранее сохранённые" in n for n in report.notes)
