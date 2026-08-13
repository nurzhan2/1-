"""Маскирование секретов в логах."""
from __future__ import annotations

import logging

from app.logging_utils import SecretMaskingFilter, install_secret_masking, mask


def test_mask_helper_replaces_secret():
    text = "Authorization: MAX_TOKEN_SECRET_abcdef ok"
    assert "MAX_TOKEN_SECRET_abcdef" not in mask(text, ["MAX_TOKEN_SECRET_abcdef"])
    assert "***" in mask(text, ["MAX_TOKEN_SECRET_abcdef"])


def test_short_secrets_ignored():
    # Слишком короткие значения не маскируем (пустой пароль, «1» и т.п.)
    assert mask("значение 12345", ["12345"]) == "значение 12345"


def test_filter_masks_formatted_args():
    flt = SecretMaskingFilter(["LERS_KEY_SECRET_0001"])
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="ключ=%s принят", args=("LERS_KEY_SECRET_0001",), exc_info=None,
    )
    flt.filter(record)
    assert "LERS_KEY_SECRET_0001" not in record.getMessage()
    assert "***" in record.getMessage()


def test_install_masking_on_root():
    import io

    root = logging.getLogger()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.INFO)
    root.addHandler(handler)
    prev_level = root.level
    root.setLevel(logging.INFO)
    try:
        # Фильтр вешается в т.ч. на уже добавленные хендлеры корня.
        install_secret_masking(["SUPER_SECRET_TOKEN_123"])
        logging.getLogger("some.child").info("токен SUPER_SECRET_TOKEN_123 в заголовке")
        handler.flush()
        out = stream.getvalue()
        assert "SUPER_SECRET_TOKEN_123" not in out
        assert "***" in out
    finally:
        root.removeHandler(handler)
        root.setLevel(prev_level)
