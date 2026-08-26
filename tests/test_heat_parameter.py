"""Выбор параметра тепла: потреблено, а не подано в контур.

Объект №39 (три колледжа) за 01–07.01.2025 отдавал три параметра сразу:
    Q_in 1016.66, Q_out 912.83, Q_delta 103.83
Раньше выбирался Q_in — КПД получался 949 % вместо 92 %.
"""
from __future__ import annotations

import pytest

from app.lers_client import _pick_heat


def test_q_delta_имеет_приоритет_над_q_in():
    values = {"Q_in": 1016.66, "Q_out": 912.83, "Q_delta": 103.83, "M_in": 18481.39}
    assert _pick_heat(values) == 103.83


def test_без_q_delta_считается_разность():
    """Если прибор не отдаёт Q_delta, потребление = подача минус обратка."""
    assert _pick_heat({"Q_in": 1016.66, "Q_out": 912.83}) == pytest.approx(103.83)


def test_одиночный_q_используется_как_есть():
    assert _pick_heat({"Q": 4.6369}) == 4.6369


def test_только_подача_берётся_последней():
    assert _pick_heat({"Q_in": 12.5}) == 12.5


def test_нет_тепловых_параметров():
    assert _pick_heat({"V_std": 646.0, "T_in": 70.2}) is None


def test_кпд_объекта_39_после_исправления():
    """Контрольный расчёт на боевых числах: должно получиться ~92 %."""
    heat = _pick_heat({"Q_in": 1016.66, "Q_out": 912.83, "Q_delta": 103.83})
    gas_nm3 = 13805.95
    assert heat * 1_000_000 / (gas_nm3 * 8200) * 100 == pytest.approx(91.7, abs=0.5)
