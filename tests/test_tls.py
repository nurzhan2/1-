"""Тесты построения TLS-контекста с доп. корневыми сертификатами."""
import ssl
from pathlib import Path

from app.tls import build_ssl_context

CERTS_DIR = Path(__file__).resolve().parent.parent / "data" / "certs"


def test_verify_off_returns_false():
    # verify=False -> httpx получает False и не проверяет сертификат
    assert build_ssl_context("data/certs", verify=False) is False


def test_verify_on_returns_context():
    ctx = build_ssl_context(None, verify=True)
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED


def test_loads_russian_ca_from_dir():
    # Каталог с Russian Trusted CA должен подхватываться без ошибок,
    # а число доверенных корней — вырасти относительно базового набора.
    if not CERTS_DIR.exists():  # на случай отсутствия сертификатов в окружении
        return
    base = len(build_ssl_context(None, verify=True).get_ca_certs())
    with_extra = len(build_ssl_context(CERTS_DIR, verify=True).get_ca_certs())
    assert with_extra >= base + 1


def test_single_file_path(tmp_path):
    # Путь к одному файлу тоже принимается (не только каталог).
    root = CERTS_DIR / "russian_trusted_root_ca.pem"
    if not root.exists():
        return
    ctx = build_ssl_context(root, verify=True)
    assert isinstance(ctx, ssl.SSLContext)
