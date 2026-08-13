"""Клиент ЛЭРС: разбор ответа (обе формы) и подбор маршрута при 404."""
from __future__ import annotations

from datetime import date

import httpx
import respx

from app.lers_client import (
    CONSUMPTION_ROUTES,
    NODE_LIST_ROUTES,
    POINT_LIST_ROUTES,
    LersClient,
    _parse_consumption,
)
from app.models import MeasurePoint

from .conftest import load_fixture

START = date(2026, 8, 3)
END = date(2026, 8, 5)


# --------------------------------------------------------------- разбор ответа
def test_parse_flat_form():
    payload = load_fixture("lers_consumption_flat.json")
    records = _parse_consumption(payload, point_number=39)
    assert len(records) == 2
    first = records[0]
    assert first.day == date(2026, 8, 3)
    assert first.heat_gcal == 1.2
    assert first.volume_m3 == 10.0
    assert first.gas_nm3 is None


def test_parse_array_form_with_gas_merge():
    payload = load_fixture("lers_consumption_array.json")
    records = _parse_consumption(payload, point_number=39)
    # Тепло и газ пришли отдельными записями за одну дату -> должны слиться в одну
    assert len(records) == 1
    rec = records[0]
    assert rec.day == date(2026, 8, 3)
    assert rec.heat_gcal == 1.2
    assert rec.volume_m3 == 10.0
    assert rec.gas_nm3 == 500.0


def test_parse_real_dayconsumption_form():
    # Формат, подтверждённый на боевом сервере ЛЭРС 3.67: суточный архив в
    # dayConsumption, значения в values[].dataParameter, недостоверные (isBad)
    # игнорируются, тепло и газ за одну дату сливаются в одну запись.
    payload = load_fixture("lers_consumption_real.json")
    records = _parse_consumption(payload, point_number=90)
    assert len(records) == 1
    rec = records[0]
    assert rec.day == date(2026, 7, 1)
    assert rec.heat_gcal == 0.8
    assert rec.volume_m3 == 596.7
    assert rec.gas_nm3 == 3.0  # газовая запись (resourceKind=Gas) слита сюда


def test_parse_empty_payload():
    assert _parse_consumption({}, 1) == []
    assert _parse_consumption([], 1) == []


# ----------------------------------------------------- подбор маршрута при 404
async def test_consumption_route_fallback_on_404(lers_settings, isolated_routes_cache):
    root = lers_settings.api_root
    url_404 = CONSUMPTION_ROUTES[0].format(root=root, id=39, start="2026-08-03", end="2026-08-05")
    url_ok = CONSUMPTION_ROUTES[1].format(root=root, id=39, start="2026-08-03", end="2026-08-05")

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url_404).mock(return_value=httpx.Response(404, text="not found"))
        mock.get(url_ok).mock(
            return_value=httpx.Response(200, json=load_fixture("lers_consumption_flat.json"))
        )
        point = MeasurePoint(number=39, title="Котельная", lers_id=39)
        async with LersClient(lers_settings) as client:
            records = await client.fetch_daily(point, START, END)
            # Рабочий маршрут запомнен именно второй кандидат
            assert client._routes["consumption"] == CONSUMPTION_ROUTES[1]

    assert len(records) == 2
    # Кэш маршрутов записан на изолированный путь
    assert isolated_routes_cache.exists()


async def test_resolve_matches_nodes_and_groups_points(lers_settings, isolated_routes_cache):
    # Перечень заказчика — это котельные (узлы): «Номер» = number узла.
    # resolve сопоставляет с узлами и раскладывает точки по nodeId.
    root = lers_settings.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(NODE_LIST_ROUTES[0].format(root=root)).mock(
            return_value=httpx.Response(200, json=load_fixture("lers_nodes.json"))
        )
        mock.get(POINT_LIST_ROUTES[0].format(root=root)).mock(
            return_value=httpx.Response(200, json=load_fixture("lers_points.json"))
        )
        points = [
            MeasurePoint(number=39, title="A"),
            MeasurePoint(number=32, title="B"),
            MeasurePoint(number=99, title="Нет в ЛЭРС"),
        ]
        async with LersClient(lers_settings) as client:
            resolved = await client.resolve_ids(points)
            # У узла 1001 (котельная №39) две точки: тепло и газ
            assert len(client._points_by_node[1001]) == 2

    by_number = {p.number: p for p in resolved}
    assert by_number[39].lers_id == 1001   # id узла, а не точки
    assert by_number[32].lers_id == 1002
    assert by_number[99].lers_id is None


async def test_fetch_daily_aggregates_heat_and_gas_of_node(lers_settings, isolated_routes_cache):
    # КПД считается по котельной: тепло и газ разных точек узла сливаются по дате.
    root = lers_settings.api_root
    with respx.mock(assert_all_called=False) as mock:
        mock.get(NODE_LIST_ROUTES[0].format(root=root)).mock(
            return_value=httpx.Response(200, json=load_fixture("lers_nodes.json"))
        )
        mock.get(POINT_LIST_ROUTES[0].format(root=root)).mock(
            return_value=httpx.Response(200, json=load_fixture("lers_points.json"))
        )
        # Точка тепла (5001) отдаёт Q, газовая (5002) — V_std
        mock.get(url__regex=rf"{root}/Data/MeasurePoints/5001/Consumption/.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_consumption_flat.json"))
        )
        mock.get(url__regex=rf"{root}/Data/MeasurePoints/5002/Consumption/.*").mock(
            return_value=httpx.Response(200, json=load_fixture("lers_consumption_gas.json"))
        )
        async with LersClient(lers_settings) as client:
            resolved = await client.resolve_ids([MeasurePoint(number=39, title="Котельная")])
            records = await client.fetch_daily(resolved[0], START, END)

    day1 = next(r for r in records if r.day == date(2026, 8, 3))
    assert day1.heat_gcal == 1.2      # тепло из точки 5001
    assert day1.gas_nm3 == 95.0       # газ из точки 5002 — именно V_std (Нм3), не V=100
