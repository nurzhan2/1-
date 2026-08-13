"""Минимальный HTTP-эндпоинт здоровья для облачного деплоя (Amvera).

Основной сервис — фоновый воркер (бот MAX + планировщик), своего HTTP-порта у него
нет. Amvera же проверяет доступность приложения по ``containerPort``. Поэтому в
боевом режиме поднимаем лёгкий health-сервер на стандартной библиотеке: он отвечает
200 на любой GET и держит контейнер «живым», не таща за собой веб-фреймворк.
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (имя метода задано stdlib)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"kotelnaya bot: OK\n")

    def log_message(self, *_args) -> None:  # не засоряем логи запросами проверки
        return


def start_health_server(port: int) -> None:
    """Запускает health-сервер в демон-потоке. Сбой не критичен для сервиса."""
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        # Локально порт может быть занят/недоступен — это не должно ронять бота.
        log.warning("Health-сервер не запущен (порт %d): %s", port, exc)
        return
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health")
    thread.start()
    log.info("Health-сервер слушает порт %d", port)
