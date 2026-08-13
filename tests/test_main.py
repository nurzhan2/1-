"""CLI: --check и --once не падают при недоступном ЛЭРС."""
from __future__ import annotations

from datetime import date

import httpx
import respx

import app.main as main
from app.config import LersSettings, Settings

from .conftest import DATA_DIR, make_onec_settings


def _settings(tmp_path):
    from app.config import ReportSettings

    return Settings(
        lers=LersSettings(base_url="http://lers.test:10000", api_key="K",
                          retries=2, retry_base_delay=0.0, connect_timeout=2, timeout=5),
        onec=make_onec_settings(mode="manual", manual_file=tmp_path / "нет.xlsx"),
        report=ReportSettings(points_file=DATA_DIR / "points.xlsx", output_dir=tmp_path / "out"),
        db_path=tmp_path / "archive.sqlite3",
    )


async def test_run_check_survives_dead_server(tmp_path, monkeypatch, capsys, isolated_routes_cache):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    root = settings.lers.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{root}/.*").mock(return_value=httpx.Response(503))
        rc = await main.run_check()
    assert rc == 0
    out = capsys.readouterr().out
    assert "НЕТ СВЯЗИ" in out
    assert "1С (режим manual)" in out
    # Все 4 заблокированных пункта показаны как предупреждения
    for i in range(1, 5):
        assert f"{i}." in out


async def test_run_once_writes_file_when_lers_down(tmp_path, monkeypatch, isolated_routes_cache):
    settings = _settings(tmp_path)
    monkeypatch.setattr(main, "settings", settings)
    root = settings.lers.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=rf"{root}/.*").mock(return_value=httpx.Response(503))
        rc = await main.run_once("week")
    assert rc == 0
    files = list((tmp_path / "out").glob("*.xlsx"))
    assert files, "отчёт не создан"
