"""Общие фикстуры тестов."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import (
    LersSettings,
    MaxSettings,
    OneCSettings,
    ReportSettings,
)

FIXTURES = Path(__file__).parent / "fixtures"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_fixture(name: str):
    """Читает JSON-фикстуру ответа внешнего API."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Убирает реальные паузы (ретраи, upload_settle) — тесты идут мгновенно."""
    async def _noop(*_a, **_kw):
        return None

    monkeypatch.setattr(asyncio, "sleep", _noop)


@pytest.fixture
def isolated_routes_cache(monkeypatch, tmp_path):
    """Изолирует кэш маршрутов ЛЭРС, чтобы тесты не читали/писали боевой файл."""
    import app.lers_client as lers_client

    cache = tmp_path / "lers_routes.json"
    monkeypatch.setattr(lers_client, "ROUTES_CACHE", cache)
    return cache


@pytest.fixture
def lers_settings():
    return LersSettings(
        base_url="http://lers.test:10000",
        api_key="LERS_KEY_SECRET_0001",
        login="",
        password="",
        timeout=5,
        connect_timeout=2,
        verify_ssl=False,
        concurrency=4,
        retries=3,
        retry_base_delay=0.0,
    )


@pytest.fixture
def max_settings():
    return MaxSettings(
        token="MAX_TOKEN_SECRET_abcdef",
        base_url="https://max.test",
        timeout=5,
        broadcast_chats=(),
        allowed_users=(),
        upload_settle_seconds=0.0,
        retries=3,
        retry_base_delay=0.0,
        attachment_retries=3,
    )


@pytest.fixture
def report_settings(tmp_path):
    return ReportSettings(
        gas_calorific_kcal=8200.0,
        efficiency_threshold=80.0,
        points_file=DATA_DIR / "points.xlsx",
        output_dir=tmp_path / "out",
    )


def make_onec_settings(**overrides) -> OneCSettings:
    base = dict(
        mode="manual",
        base_url="http://onec.test",
        username="user",
        password="ONEC_PASSWORD_SECRET",
        timeout=5,
        odata_resource="AccumulationRegister_Затраты/Turnovers",
        field_object="Объект",
        field_amount="СуммаОборот",
        http_path="/hs/costs/period",
    )
    base.update(overrides)
    return OneCSettings(**base)
