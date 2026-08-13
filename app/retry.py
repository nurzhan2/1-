"""Ретраи с экспоненциальной паузой для HTTP-запросов к внешним сервисам.

Повторяем запрос при сетевых ошибках (таймаут, обрыв соединения) и при кодах
429 / 5xx — то есть когда повтор действительно имеет шанс помочь. Клиентские
ошибки 4xx (кроме 429) не ретраятся: они означают проблему в самом запросе.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

import httpx

log = logging.getLogger(__name__)

# Коды, при которых повтор осмыслен: перегрузка/временный сбой сервера.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


async def request_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_statuses: frozenset[int] = RETRY_STATUSES,
    label: str = "",
) -> httpx.Response:
    """Выполняет ``send`` с повторами.

    :param send: корутина без аргументов, возвращающая ответ httpx.
    :param retries: максимальное число попыток (включая первую).
    :param base_delay: базовая пауза, удваивается с каждой попыткой.
    :param max_delay: потолок паузы между попытками.
    :param retry_statuses: HTTP-коды, при которых повторяем.
    :param label: подпись для логов (например, "ЛЭРС" или "MAX").
    """
    delay = base_delay
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = await send()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt >= retries:
                raise
            wait = min(delay, max_delay)
            log.warning(
                "%s: сетевая ошибка (%s), попытка %d/%d, пауза %.1f c",
                label or "HTTP", exc.__class__.__name__, attempt, retries, wait,
            )
        else:
            if resp.status_code in retry_statuses and attempt < retries:
                wait = _retry_after(resp) or min(delay, max_delay)
                log.warning(
                    "%s: ответ %d, попытка %d/%d, пауза %.1f c",
                    label or "HTTP", resp.status_code, attempt, retries, wait,
                )
            else:
                return resp
        await asyncio.sleep(wait)
        delay *= 2
    # Сюда попадаем, только если последняя попытка была сетевой ошибкой,
    # но исключение уже проброшено выше; на всякий случай:
    if last_exc is not None:  # pragma: no cover
        raise last_exc
    raise RuntimeError("request_with_retry: недостижимая ветка")  # pragma: no cover


def _retry_after(resp: httpx.Response) -> Optional[float]:
    """Учитывает заголовок Retry-After (в секундах), если он есть."""
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
