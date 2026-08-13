"""Ретраи с экспоненциальной паузой на 429/5xx/таймаут."""
from __future__ import annotations

import httpx
import pytest

from app.retry import _retry_after, request_with_retry


def _sequence(responses):
    calls = {"n": 0}

    async def send():
        item = responses[calls["n"]]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return send, calls


async def test_retries_on_503_then_ok():
    send, calls = _sequence([httpx.Response(503), httpx.Response(200)])
    resp = await request_with_retry(send, retries=3, base_delay=0.0)
    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_retries_on_429_then_ok():
    send, calls = _sequence([httpx.Response(429), httpx.Response(200)])
    resp = await request_with_retry(send, retries=3, base_delay=0.0)
    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_retries_on_timeout_then_ok():
    send, calls = _sequence([httpx.ConnectTimeout("timeout"), httpx.Response(200)])
    resp = await request_with_retry(send, retries=3, base_delay=0.0)
    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_no_retry_on_404():
    send, calls = _sequence([httpx.Response(404), httpx.Response(200)])
    resp = await request_with_retry(send, retries=3, base_delay=0.0)
    assert resp.status_code == 404
    assert calls["n"] == 1


async def test_returns_last_response_when_exhausted():
    send, calls = _sequence([httpx.Response(503), httpx.Response(503)])
    resp = await request_with_retry(send, retries=2, base_delay=0.0)
    assert resp.status_code == 503
    assert calls["n"] == 2


async def test_raises_on_persistent_timeout():
    send, _ = _sequence([httpx.ConnectTimeout("t"), httpx.ConnectTimeout("t")])
    with pytest.raises(httpx.TimeoutException):
        await request_with_retry(send, retries=2, base_delay=0.0)


def test_retry_after_header():
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "5"})) == 5.0
    assert _retry_after(httpx.Response(429)) is None
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "nope"})) is None
