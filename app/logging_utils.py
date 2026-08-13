"""Маскирование секретов в логах.

Токены и пароли не должны попадать в логи (правило безопасности проекта).
Фильтр вешается на корневой логгер и заменяет известные секретные строки
на ``***`` в любом сообщении — где бы оно ни было сформировано.
"""
from __future__ import annotations

import logging
from typing import Iterable

MASK = "***"
# Слишком короткие значения не маскируем — риск испортить осмысленный текст
# и всё равно это не секрет (пустой пароль и т.п.).
_MIN_SECRET_LEN = 6


class SecretMaskingFilter(logging.Filter):
    """Заменяет секреты в тексте лог-записи и в её аргументах."""

    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        # Длинные — первыми, чтобы не «съесть» вложенные подстроки.
        self._secrets = sorted(
            {s for s in secrets if s and len(s) >= _MIN_SECRET_LEN},
            key=len,
            reverse=True,
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        # Форматируем заранее, чтобы промаскировать и подставленные аргументы.
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover — не мешаем логированию
            return True
        masked = self._mask(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True

    def _mask(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, MASK)
        return text


def install_secret_masking(secrets: Iterable[str]) -> None:
    """Добавляет фильтр маскирования на корневой логгер (идемпотентно)."""
    root = logging.getLogger()
    flt = SecretMaskingFilter(secrets)
    if not flt._secrets:
        return
    # Фильтр на логгере срабатывает не для всех хендлеров — вешаем и туда.
    root.addFilter(flt)
    for handler in root.handlers:
        handler.addFilter(flt)


def mask(text: str, secrets: Iterable[str]) -> str:
    """Разовое маскирование строки (для сообщений об ошибках и т.п.)."""
    return SecretMaskingFilter(secrets)._mask(text)
