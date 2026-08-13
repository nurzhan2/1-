"""Health-эндпоинт для облака: отвечает 200 и не роняет сервис при занятом порте."""
import http.client
import threading
from http.server import ThreadingHTTPServer

from app.health import _HealthHandler, start_health_server


def test_health_handler_returns_200():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"OK" in resp.read()
    finally:
        srv.shutdown()
        srv.server_close()


def test_start_health_server_does_not_raise_on_free_port():
    # Порт 0 -> ОС выберет свободный; демон-поток, вызов не блокирует и не падает.
    start_health_server(0)
