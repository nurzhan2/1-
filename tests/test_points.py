"""Парсинг перечня точек учёта из xlsx заказчика."""
from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from app.points import _clean_title, load_points

DATA_POINTS = Path(__file__).resolve().parent.parent / "data" / "points.xlsx"


def test_load_real_points_file_has_56():
    points = load_points(DATA_POINTS)
    assert len(points) == 56
    # Все номера уникальны и отсортированы по возрастанию
    numbers = [p.number for p in points]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == 56


def test_prefix_stripped():
    assert _clean_title("ъ_Котельная южная") == "Котельная южная"
    assert _clean_title("  Котельная   двойной   пробел ") == "Котельная двойной пробел"
    # Реальный файл: ни одно наименование не должно начинаться с «ъ_»
    for p in load_points(DATA_POINTS):
        assert not p.title.startswith("ъ_")


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_points(tmp_path / "нет-такого.xlsx")


def test_free_layout_header_detection(tmp_path):
    """Заголовок не в первой строке, данные со смещением по колонкам."""
    wb = Workbook()
    ws = wb.active
    ws.append([None, None, None])
    ws.append([None, "Служебная шапка", None])
    ws.append([None, "Наименование точки", "Номер"])
    ws.append([None, "ъ_Котельная А", 39])
    ws.append([None, "Котельная Б", 32])
    ws.append([None, "строка без номера", None])
    path = tmp_path / "custom.xlsx"
    wb.save(path)

    points = load_points(path)
    assert [(p.number, p.title) for p in points] == [
        (32, "Котельная Б"),
        (39, "Котельная А"),
    ]


def test_empty_file_raises(tmp_path):
    wb = Workbook()
    wb.active.append(["мусор", "без", "заголовка"])
    path = tmp_path / "empty.xlsx"
    wb.save(path)
    with pytest.raises(ValueError):
        load_points(path)
