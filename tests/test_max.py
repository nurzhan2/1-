"""Клиент MAX: upload → token → sendMessage, ретраи."""
from __future__ import annotations

import httpx
import pytest
import respx

from app.max_bot import MaxApiError, MaxBot

from .conftest import load_fixture


async def test_get_me(max_settings):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://max.test/me").mock(
            return_value=httpx.Response(200, json=load_fixture("max_me.json"))
        )
        async with MaxBot(max_settings) as bot:
            me = await bot.get_me()
    assert me["user_id"] == 42


async def test_request_retries_on_5xx(max_settings):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://max.test/me").mock(
            side_effect=[
                httpx.Response(503, text="overloaded"),
                httpx.Response(200, json=load_fixture("max_me.json")),
            ]
        )
        async with MaxBot(max_settings) as bot:
            me = await bot.get_me()
    assert me["name"] == "Котельный бот"


async def test_upload_then_send_file(max_settings, tmp_path):
    f = tmp_path / "report.xlsx"
    f.write_bytes(b"xlsx-bytes")

    with respx.mock(assert_all_called=True) as mock:
        mock.post(url__regex=r"https://max\.test/uploads.*").mock(
            return_value=httpx.Response(200, json={"url": "https://upload.test/put"})
        )
        mock.post("https://upload.test/put").mock(
            return_value=httpx.Response(200, json={"token": "FILETOK"})
        )
        sent = mock.post(url__regex=r"https://max\.test/messages.*").mock(
            return_value=httpx.Response(200, json={"message_id": 555})
        )
        async with MaxBot(max_settings) as bot:
            result = await bot.send_file(f, caption="Отчёт", chat_id=123)

    assert result["message_id"] == 555
    # В сообщение ушёл токен вложения, полученный на шаге загрузки
    body = sent.calls.last.request.content.decode("utf-8")
    assert "FILETOK" in body


async def test_send_file_retries_on_attachment_not_ready(max_settings, tmp_path):
    f = tmp_path / "report.xlsx"
    f.write_bytes(b"data")

    with respx.mock(assert_all_called=True) as mock:
        mock.post(url__regex=r"https://max\.test/uploads.*").mock(
            return_value=httpx.Response(200, json={"url": "https://upload.test/put", "token": "T"})
        )
        mock.post("https://upload.test/put").mock(
            return_value=httpx.Response(200, json={"token": "FILETOK"})
        )
        mock.post(url__regex=r"https://max\.test/messages.*").mock(
            side_effect=[
                httpx.Response(400, json={"code": "attachment.not.ready"}),
                httpx.Response(400, json={"code": "attachment.not.ready"}),
                httpx.Response(200, json={"message_id": 7}),
            ]
        )
        async with MaxBot(max_settings) as bot:
            result = await bot.send_file(f, caption="c", chat_id=1)
    assert result["message_id"] == 7


async def test_send_file_gives_up_after_retries(max_settings, tmp_path):
    f = tmp_path / "r.xlsx"
    f.write_bytes(b"data")
    with respx.mock(assert_all_called=False) as mock:
        mock.post(url__regex=r"https://max\.test/uploads.*").mock(
            return_value=httpx.Response(200, json={"url": "https://upload.test/put", "token": "T"})
        )
        mock.post("https://upload.test/put").mock(
            return_value=httpx.Response(200, json={"token": "FILETOK"})
        )
        mock.post(url__regex=r"https://max\.test/messages.*").mock(
            return_value=httpx.Response(400, json={"code": "attachment.not.ready"})
        )
        async with MaxBot(max_settings) as bot:
            with pytest.raises(MaxApiError):
                await bot.send_file(f, caption="c", chat_id=1)


async def test_missing_token_raises():
    from app.config import MaxSettings

    with pytest.raises(MaxApiError):
        MaxBot(MaxSettings(token="", base_url="https://max.test"))
