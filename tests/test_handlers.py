"""Команды бота: маршрутизация, ограничение доступа, /status."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import respx

from app.config import LersSettings, MaxSettings, Settings
from app.handlers import HELP_TEXT, BotHandlers
from app.models import ReportData
from app.report import month_period, week_period

from .conftest import load_fixture, make_onec_settings


class FakeStorage:
    def __init__(self):
        self._last = {}

    def last_success(self, source):
        return self._last.get(source)


class FakeService:
    def __init__(self):
        self.calls = []
        self.storage = FakeStorage()

    async def collect(self, start, end):
        self.calls.append((start, end))
        report = ReportData(period_start=start, period_end=end)
        return report, {}, Path("dummy.xlsx")


class FakeBot:
    def __init__(self):
        self.messages = []
        self.files = []

    async def send_message(self, text, chat_id=None, user_id=None):
        self.messages.append(text)

    async def send_file(self, path, caption="", chat_id=None, user_id=None):
        self.files.append((path, caption))


def _update(text, user_id=5, chat_id=7):
    return {
        "update_type": "message_created",
        "message": {
            "body": {"text": text},
            "sender": {"user_id": user_id},
            "recipient": {"chat_id": chat_id},
        },
    }


def _settings(**max_overrides):
    return Settings(
        lers=LersSettings(base_url="http://lers.test:10000", api_key="K"),
        onec=make_onec_settings(mode="manual"),
        max_bot=MaxSettings(token="x", base_url="https://max.test", **max_overrides),
    )


async def test_help_command():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/help"))
    assert bot.messages and bot.messages[-1] == HELP_TEXT


async def test_report_week_dispatch():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/report week"))
    assert service.calls == [week_period()]
    assert bot.files  # файл отправлен


async def test_report_month_dispatch():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/report month"))
    assert service.calls == [month_period()]


async def test_report_custom_period_iso():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/report 2026-07-01 2026-07-31"))
    assert service.calls == [(date(2026, 7, 1), date(2026, 7, 31))]


async def test_report_bad_period_shows_format():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/report позавчера"))
    assert not service.calls
    assert any("Формат" in m for m in bot.messages)


async def test_disallowed_user_rejected():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings(allowed_users=(999,)))
    await h.handle(_update("/report week", user_id=5))
    assert not service.calls
    assert any("ограничен" in m.lower() for m in bot.messages)


async def test_allowed_user_passes():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings(allowed_users=(5,)))
    await h.handle(_update("/report week", user_id=5))
    assert service.calls == [week_period()]


async def test_unknown_command():
    bot, service = FakeBot(), FakeService()
    h = BotHandlers(bot, service, _settings())
    await h.handle(_update("/погода"))
    assert any("Не знаю такой команды" in m for m in bot.messages)


async def test_status_reports_sources(isolated_routes_cache):
    bot, service = FakeBot(), FakeService()
    settings = _settings()
    h = BotHandlers(bot, service, settings)
    root = settings.lers.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{root}/ServerInfo").mock(return_value=httpx.Response(200, json={"v": 1}))
        mock.get(url__regex=rf"{root}/Core/MeasurePoints.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_points.json"))
        )
        await h.handle(_update("/status"))
    text = "\n".join(bot.messages)
    assert "ЛЭРС" in text
    assert "1С" in text
    assert "последний успешный опрос" in text
