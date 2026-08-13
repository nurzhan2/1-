"""Клиент Bot API мессенджера MAX (МАКС).

Документация: https://dev.max.ru/docs-api

Ключевые особенности актуальной версии API, учтённые здесь:
  * запросы идут на домен ``platform-api2.max.ru``;
  * токен передаётся заголовком ``Authorization`` (query-параметр больше не поддерживается);
  * отправка сообщений — ``POST /messages`` с ``chat_id`` или ``user_id`` в query;
  * файл сначала загружается через ``POST /uploads?type=file``, затем полученный token
    прикрепляется вложением; между загрузкой и отправкой нужна пауза, иначе сервер
    отвечает ``attachment.not.ready``;
  * ограничение платформы — 30 запросов в секунду.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from .config import MaxSettings
from .retry import request_with_retry
from .tls import build_ssl_context

log = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], "asyncio.Future | None"]


class MaxApiError(RuntimeError):
    pass


class MaxBot:
    def __init__(self, cfg: MaxSettings):
        if not cfg.token:
            raise MaxApiError("Не задан MAX_BOT_TOKEN")
        self.cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._marker: Optional[int] = None
        self._handlers: list[Handler] = []
        # Контекст TLS с доверием к Russian Trusted CA (сертификат *.max.ru).
        self._verify = build_ssl_context(cfg.ca_bundle, cfg.verify_ssl)

    async def __aenter__(self) -> "MaxBot":
        self._client = httpx.AsyncClient(
            base_url=self.cfg.base_url.rstrip("/"),
            headers={"Authorization": self.cfg.token, "Accept": "application/json"},
            timeout=self.cfg.timeout,
            verify=self._verify,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------ методы
    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def set_commands(self, commands: list[dict[str, str]]) -> dict[str, Any]:
        """Регистрирует список команд бота (PATCH /me/commands)."""
        return await self._request("PATCH", "/me", json={"commands": commands})

    async def send_message(
        self,
        text: str,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        fmt: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if chat_id is not None:
            params["chat_id"] = chat_id
        elif user_id is not None:
            params["user_id"] = user_id
        else:
            raise MaxApiError("Нужно указать chat_id или user_id")

        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if fmt:
            body["format"] = fmt
        return await self._request("POST", "/messages", params=params, json=body)

    async def send_file(
        self,
        path: Path,
        caption: str = "",
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        retries: Optional[int] = None,
    ) -> dict[str, Any]:
        """Загружает файл и отправляет его вложением."""
        token = await self.upload_file(path)
        attachments = [{"type": "file", "payload": {"token": token}}]

        retries = retries or self.cfg.attachment_retries
        delay = self.cfg.upload_settle_seconds
        last_error: Optional[Exception] = None
        for attempt in range(retries):
            await asyncio.sleep(delay)
            try:
                return await self.send_message(
                    caption, chat_id=chat_id, user_id=user_id, attachments=attachments
                )
            except MaxApiError as exc:
                # сервер MAX ещё обрабатывает вложение — увеличиваем паузу
                if "not.ready" not in str(exc) and "not_ready" not in str(exc):
                    raise
                last_error = exc
                delay *= 2
                log.info("Вложение ещё обрабатывается, попытка %d/%d", attempt + 2, retries)
        raise MaxApiError(f"Файл загружен, но отправить не удалось: {last_error}")

    async def upload_file(self, path: Path) -> str:
        """POST /uploads -> url -> multipart-загрузка -> token."""
        path = Path(path)
        if not path.exists():
            raise MaxApiError(f"Файл не найден: {path}")

        upload = await self._request("POST", "/uploads", params={"type": "file"})
        url = upload.get("url")
        if not url:
            raise MaxApiError(f"MAX не вернул URL для загрузки: {upload}")

        async with httpx.AsyncClient(
            timeout=max(self.cfg.timeout, 300), verify=self._verify
        ) as raw:
            with path.open("rb") as fh:
                resp = await raw.post(url, files={"data": (path.name, fh)})
        if resp.status_code >= 400:
            raise MaxApiError(f"Загрузка файла не удалась: {resp.status_code} {resp.text[:300]}")

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        token = payload.get("token") or upload.get("token")
        if not token:
            raise MaxApiError(f"Не получен token файла. Ответ: {resp.text[:300]}")
        return token

    # --------------------------------------------------------------- обновления
    def on_update(self, handler: Handler) -> None:
        self._handlers.append(handler)

    async def poll(self, stop_event: Optional[asyncio.Event] = None, timeout: int = 30) -> None:
        """Long polling GET /updates.

        Подходит для отладки и небольших внутренних ботов. Для production
        документация MAX рекомендует webhook (POST /subscriptions) — см. README.
        """
        log.info("Запущен long polling MAX")
        while not (stop_event and stop_event.is_set()):
            params: dict[str, Any] = {"timeout": timeout}
            if self._marker is not None:
                params["marker"] = self._marker
            try:
                data = await self._request("GET", "/updates", params=params, timeout=timeout + 15)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Long polling не должен падать насмерть: логируем и продолжаем.
                log.warning("Ошибка получения обновлений: %s", exc)
                await asyncio.sleep(5)
                continue

            self._marker = data.get("marker", self._marker)
            for update in data.get("updates", []):
                for handler in self._handlers:
                    try:
                        result = handler(update)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:  # обработчик не должен ронять polling
                        log.exception("Ошибка в обработчике обновления")

    # ------------------------------------------------------------------ helpers
    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        assert self._client is not None, "MaxBot используется вне контекстного менеджера"
        req_timeout = timeout or self.cfg.timeout

        async def _send() -> httpx.Response:
            return await self._client.request(
                method, path, params=params, json=json, timeout=req_timeout
            )

        resp = await request_with_retry(
            _send,
            retries=self.cfg.retries,
            base_delay=self.cfg.retry_base_delay,
            label="MAX",
        )
        if resp.status_code >= 400:
            raise MaxApiError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError:
            return {}


# ------------------------------------------------------------------ разбор Update
def extract_text(update: dict[str, Any]) -> str:
    message = update.get("message") or {}
    body = message.get("body") or {}
    return (body.get("text") or update.get("payload") or "").strip()


def extract_chat_id(update: dict[str, Any]) -> Optional[int]:
    message = update.get("message") or {}
    recipient = message.get("recipient") or {}
    chat_id = recipient.get("chat_id") or update.get("chat_id")
    return int(chat_id) if chat_id is not None else None


def extract_user_id(update: dict[str, Any]) -> Optional[int]:
    message = update.get("message") or {}
    sender = message.get("sender") or update.get("user") or {}
    user_id = sender.get("user_id") or update.get("user_id")
    return int(user_id) if user_id is not None else None
