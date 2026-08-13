"""Построение TLS-контекста с дополнительными корневыми сертификатами.

Платформа MAX (и другие российские госресурсы) использует сертификаты,
выпущенные «Russian Trusted Root CA» Минцифры. Этого корня нет в стандартном
наборе certifi, поэтому проверка сертификата по умолчанию падает с
``CERTIFICATE_VERIFY_FAILED``. Мы добавляем официальные корневой и промежуточный
сертификаты (лежат в ``data/certs``) к доверенным — и проверку TLS отключать
не приходится.
"""
from __future__ import annotations

import logging
import ssl
from pathlib import Path
from typing import Union

try:
    import certifi
    _CERTIFI = certifi.where()
except ImportError:  # certifi идёт зависимостью httpx, но подстрахуемся
    _CERTIFI = None

log = logging.getLogger(__name__)


def build_ssl_context(
    extra_ca: Union[str, Path, None] = None, verify: bool = True
) -> Union[ssl.SSLContext, bool]:
    """Возвращает SSL-контекст для httpx.

    :param extra_ca: путь к файлу .pem/.crt или к каталогу с такими файлами —
        дополнительные доверенные CA (например, Russian Trusted Root/Sub CA).
    :param verify: если ``False`` — проверка сертификата отключается (крайняя мера,
        использовать только для заведомо доверенной локальной сети).
    """
    if not verify:
        return False

    ctx = ssl.create_default_context(cafile=_CERTIFI)
    for cert_file in _iter_ca_files(extra_ca):
        try:
            ctx.load_verify_locations(str(cert_file))
            log.debug("Добавлен доверенный CA: %s", cert_file.name)
        except ssl.SSLError as exc:  # pragma: no cover — битый файл не должен ронять клиент
            log.warning("Не удалось загрузить CA %s: %s", cert_file, exc)
    return ctx


def _iter_ca_files(extra_ca: Union[str, Path, None]):
    if not extra_ca:
        return
    path = Path(extra_ca)
    if path.is_file():
        yield path
    elif path.is_dir():
        for pattern in ("*.pem", "*.crt"):
            yield from sorted(path.glob(pattern))
