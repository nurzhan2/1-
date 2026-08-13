"""Клиент REST API «ЛЭРС УЧЁТ».

Документация: https://docs.lers.ru/dev/rest/
Авторизация — заголовок ``x-lers-api-key``. Полная спецификация конкретного сервера
всегда доступна по адресу ``http://<сервер>:10000/api/swagger`` (OpenAPI).

Между версиями ЛЭРС (3.3x–3.4x) пути к данным отличаются, поэтому клиент при первом
запуске подбирает рабочий маршрут из списка кандидатов и запоминает его в
``data/lers_routes.json``. Это избавляет от переписывания кода при обновлении сервера
заказчика и от «угадывания» схемы вслепую.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Any, Iterable, Optional

import httpx

from .config import BASE_DIR, LersSettings
from .models import DailyRecord, MeasurePoint
from .retry import request_with_retry

log = logging.getLogger(__name__)

ROUTES_CACHE = BASE_DIR / "data" / "lers_routes.json"

# Кандидаты маршрутов. {root} — http://host:port/api/v1
# Первым — маршрут, подтверждённый на боевом сервере ЛЭРС 3.67 (2026).
POINT_LIST_ROUTES = (
    "{root}/Core/MeasurePoints",
    "{root}/MeasurePoints",
    "{root}/MeasurePoints/List",
)

# Список объектов учёта (котельных). Перечень заказчика — это узлы, а не точки:
# «Номер» = number узла, внутри узла лежат точки тепло- и газоснабжения.
NODE_LIST_ROUTES = (
    "{root}/Core/Nodes",
    "{root}/Nodes",
)

# {id} — внутренний Id точки, {start}/{end} — даты в формате YYYY-MM-DD.
# Подтверждено на боевом сервере: суточный архив лежит в поле dayConsumption,
# параметр запроса — dataTypes (множественное число), includeCalculated добирает
# расчётные значения за сутки без полного часового архива.
CONSUMPTION_ROUTES = (
    "{root}/Data/MeasurePoints/{id}/Consumption/{start}/{end}?dataTypes=Day&includeCalculated=true",
    "{root}/Data/MeasurePoints/{id}/Consumption/{start}/{end}?dataType=Day",
    "{root}/Data/MeasurePoints/{id}/Consumption/Day/{start}/{end}",
    "{root}/Data/MeasurePoints/{id}?dataType=Day&startDate={start}&endDate={end}",
)

# Названия параметров ЛЭРС -> поля модели
HEAT_KEYS = ("q", "q_", "heat", "энергия", "гкал")
VOLUME_KEYS = ("v", "v1", "vol", "объем", "объём")
# Для газа берём объём, приведённый к нормальным условиям (Нм3): на боевом сервере
# это V_std. Рабочий объём V берём только если V_std нет. Это важно для КПД.
GAS_VOLUME_KEYS = ("v_std", "vstd", "vc", "vp", "v_norm", "vнорм")
MASS_KEYS = ("m", "m1", "mass")


class LersError(RuntimeError):
    pass


class LersClient:
    def __init__(self, cfg: LersSettings):
        self.cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._routes: dict[str, str] = _load_routes()
        self._sem = asyncio.Semaphore(cfg.concurrency)
        # nodeId -> список точек учёта этого узла (заполняется в resolve_ids)
        self._points_by_node: dict[int, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------ infra
    async def __aenter__(self) -> "LersClient":
        headers = {"Accept": "application/json"}
        if self.cfg.api_key:
            headers["x-lers-api-key"] = self.cfg.api_key
        auth = None
        if not self.cfg.api_key and self.cfg.login:
            auth = (self.cfg.login, self.cfg.password)
        self._client = httpx.AsyncClient(
            headers=headers,
            auth=auth,
            timeout=httpx.Timeout(self.cfg.timeout, connect=self.cfg.connect_timeout),
            verify=self.cfg.verify_ssl,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    async def _get(self, url: str, *, retry: bool = True) -> httpx.Response:
        """GET с ограничением параллелизма и (опционально) ретраями.

        Для проверки связи (``ping``) ретраи выключаем: при недоступном сервере
        нет смысла ждать несколько экспоненциальных пауз — команда ``--check``
        должна отвечать быстро.
        """
        assert self._client is not None, "LersClient используется вне контекстного менеджера"

        async def _send() -> httpx.Response:
            async with self._sem:
                return await self._client.get(url)

        if not retry:
            return await _send()
        return await request_with_retry(
            _send,
            retries=self.cfg.retries,
            base_delay=self.cfg.retry_base_delay,
            label="ЛЭРС",
        )

    # --------------------------------------------------------------- проверка
    async def ping(self) -> dict[str, Any]:
        """Проверка доступности сервера и корректности ключа."""
        root = self.cfg.api_root
        # Подтверждено на боевом сервере 3.67: liveness — /ServerInfo, список точек — /Core/MeasurePoints.
        for url in (f"{root}/ServerInfo", f"{root}/Core/MeasurePoints", f"{root}/ServerInfo/Extra"):
            try:
                resp = await self._get(url, retry=False)
            except httpx.HTTPError as exc:
                log.debug("ping %s: %s", url, exc)
                continue
            if resp.status_code == 200:
                return {"ok": True, "url": url, "payload": _safe_json(resp)}
            if resp.status_code in (401, 403):
                raise LersError(
                    f"Сервер ЛЭРС ответил {resp.status_code}: ключ API отклонён. "
                    "Проверьте x-lers-api-key и права учётной записи."
                )
        raise LersError(
            f"Сервер ЛЭРС недоступен по адресу {self.cfg.base_url}. "
            "Проверьте, открыт ли порт снаружи (по умолчанию доступ только из локальной сети)."
        )

    # -------------------------------------------------------------- точки учёта
    async def fetch_measure_points(self) -> list[dict[str, Any]]:
        """Получает список точек учёта, доступных учётной записи."""
        route = self._routes.get("points")
        candidates = (route,) if route else POINT_LIST_ROUTES
        last_error = ""
        for template in candidates:
            url = template.format(root=self.cfg.api_root)
            try:
                resp = await self._get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if resp.status_code == 200:
                data = _safe_json(resp)
                items = _as_list(data, ("measurePoints", "items", "data", "result"))
                if items:
                    self._remember("points", template)
                    return items
            last_error = f"{resp.status_code} {resp.text[:200]}"
        raise LersError(f"Не удалось получить список точек учёта. Последняя ошибка: {last_error}")

    async def fetch_nodes(self) -> list[dict[str, Any]]:
        """Получает список объектов учёта (котельных, узлов)."""
        route = self._routes.get("nodes")
        candidates = (route,) if route else NODE_LIST_ROUTES
        last_error = ""
        for template in candidates:
            url = template.format(root=self.cfg.api_root)
            try:
                resp = await self._get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if resp.status_code == 200:
                items = _as_list(_safe_json(resp), ("nodes", "items", "data", "result"))
                if items:
                    self._remember("nodes", template)
                    return items
            last_error = f"{resp.status_code} {resp.text[:200]}"
        raise LersError(f"Не удалось получить список котельных (узлов). Последняя ошибка: {last_error}")

    async def resolve_ids(self, points: Iterable[MeasurePoint]) -> list[MeasurePoint]:
        """Сопоставляет «Номер» из перечня заказчика с узлом (котельной) ЛЭРС.

        На боевом сервере перечень заказчика — это список ОБЪЕКТОВ учёта (котельных):
        «Номер» = number узла (подтверждено по совпадению названий). Внутри узла лежат
        точки тепло- и газоснабжения; их складываем по nodeId, чтобы затем считать КПД
        по котельной (сумма тепла точек узла / газ узла).
        """
        nodes = await self.fetch_nodes()
        node_by_number: dict[int, int] = {}
        for node in nodes:
            number = _first_int(node, ("number", "Number", "customId"))
            ident = _first_int(node, ("id", "Id"))
            if number is not None and ident is not None:
                node_by_number[number] = ident

        # Разбивка точек по узлам — для агрегации тепла и газа в fetch_daily.
        self._points_by_node = {}
        try:
            for item in await self.fetch_measure_points():
                node_id = _first_int(item, ("nodeId", "NodeId"))
                if node_id is not None:
                    self._points_by_node.setdefault(node_id, []).append(item)
        except LersError as exc:
            log.warning("Не удалось получить точки для разбивки по котельным: %s", exc)

        resolved: list[MeasurePoint] = []
        missing: list[int] = []
        for p in points:
            ident = node_by_number.get(p.number)
            if ident is None:
                missing.append(p.number)
            resolved.append(MeasurePoint(number=p.number, title=p.title, lers_id=ident))
        if missing:
            log.warning(
                "Не найдены в ЛЭРС котельные с номерами: %s",
                ", ".join(map(str, sorted(missing))),
            )
        return resolved

    # ------------------------------------------------------------------ данные
    async def fetch_daily(self, point: MeasurePoint, start: date, end: date) -> list[DailyRecord]:
        """Суточные данные по котельной: агрегирует тепло и газ всех её точек.

        ``point.lers_id`` — это id узла (котельной). Для каждой точки узла тянем
        суточный архив и складываем по датам: тепло (Q) — со всех тепловых точек,
        газ (V газовых точек) — со всех газовых. Итог — одна запись на дату с
        суммарными Q и газом, по которой считается КПД котельной.
        """
        if point.lers_id is None:
            return []

        children = self._points_by_node.get(point.lers_id)
        if children:
            point_ids = [
                pid for pid in (_first_int(ch, ("id", "Id")) for ch in children) if pid is not None
            ]
        else:
            # Запасной путь: если разбивка по узлам недоступна, трактуем lers_id
            # как id самой точки (совместимость и офлайн-тесты).
            point_ids = [point.lers_id]

        per_day: dict[date, DailyRecord] = {}
        for pid in point_ids:
            for rec in await self._fetch_point_records(pid, point.number, start, end):
                slot = per_day.get(rec.day)
                if slot is None:
                    slot = DailyRecord(point_number=point.number, day=rec.day)
                    per_day[rec.day] = slot
                if rec.heat_gcal is not None:
                    slot.heat_gcal = (slot.heat_gcal or 0.0) + rec.heat_gcal
                if rec.volume_m3 is not None:
                    slot.volume_m3 = (slot.volume_m3 or 0.0) + rec.volume_m3
                if rec.gas_nm3 is not None:
                    slot.gas_nm3 = (slot.gas_nm3 or 0.0) + rec.gas_nm3
        return [per_day[d] for d in sorted(per_day)]

    async def _fetch_point_records(
        self, point_id: int, point_number: int, start: date, end: date
    ) -> list[DailyRecord]:
        """Суточный архив по одной точке учёта (с подбором маршрута)."""
        route = self._routes.get("consumption")
        candidates = (route,) if route else CONSUMPTION_ROUTES
        last_error = ""
        for template in candidates:
            url = template.format(
                root=self.cfg.api_root,
                id=point_id,
                start=start.isoformat(),
                end=end.isoformat(),
            )
            try:
                resp = await self._get(url)
            except httpx.HTTPError as exc:
                last_error = str(exc)
                continue
            if resp.status_code == 200:
                records = _parse_consumption(_safe_json(resp), point_number)
                self._remember("consumption", template)
                return records
            last_error = f"{resp.status_code} {resp.text[:200]}"
        log.warning(
            "Точка id=%s (котельная №%s): данные не получены (%s)", point_id, point_number, last_error
        )
        return []

    async def fetch_daily_bulk(
        self, points: Iterable[MeasurePoint], start: date, end: date
    ) -> dict[int, list[DailyRecord]]:
        """Параллельный (с ограничением по нагрузке) опрос всех точек."""
        points = [p for p in points if p.lers_id is not None]
        tasks = [self.fetch_daily(p, start, end) for p in points]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[int, list[DailyRecord]] = {}
        for point, result in zip(points, results):
            if isinstance(result, Exception):
                log.warning("Точка №%s: ошибка опроса — %s", point.number, result)
                out[point.number] = []
            else:
                out[point.number] = result
        return out

    # ------------------------------------------------------------------ helpers
    def _remember(self, key: str, template: str) -> None:
        if self._routes.get(key) == template:
            return
        self._routes[key] = template
        try:
            ROUTES_CACHE.parent.mkdir(parents=True, exist_ok=True)
            ROUTES_CACHE.write_text(
                json.dumps(self._routes, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:  # кэш не критичен
            log.debug("Не удалось сохранить кэш маршрутов: %s", exc)


# --------------------------------------------------------------------- парсинг
def _load_routes() -> dict[str, str]:
    if ROUTES_CACHE.exists():
        try:
            return json.loads(ROUTES_CACHE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {}


def _as_list(data: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in keys:
            for variant in (key, key[:1].upper() + key[1:]):
                value = data.get(variant)
                if isinstance(value, list):
                    return [x for x in value if isinstance(x, dict)]
    return []


def _first_int(item: dict[str, Any], keys: tuple[str, ...]) -> Optional[int]:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_str(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, str):
        text = value.replace("Z", "").split(".")[0]
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def _parse_consumption(payload: Any, point_number: int) -> list[DailyRecord]:
    """Разбирает ответ ЛЭРС в суточные записи.

    ЛЭРС отдаёт данные в виде списка записей, где значения параметров лежат либо
    плоско (``{"Q": 1.2}``), либо массивом ``values: [{"dataParameter": "Q", "value": 1.2}]``.
    Поддерживаем оба варианта, чтобы не зависеть от версии сервера.
    """
    container = payload
    if isinstance(payload, dict):
        # dayConsumption — подтверждённый на боевом сервере ключ суточного архива.
        for key in ("dayConsumption", "consumption", "records", "data", "items", "result"):
            value = payload.get(key) or payload.get(key[:1].upper() + key[1:])
            if isinstance(value, list):
                container = value
                break
            if isinstance(value, dict) and isinstance(value.get("consumption"), list):
                container = value["consumption"]
                break
    if not isinstance(container, list):
        return []

    records: list[DailyRecord] = []
    for raw in container:
        if not isinstance(raw, dict):
            continue
        day = None
        for key in ("dateTime", "DateTime", "date", "Date", "day"):
            day = _parse_date(raw.get(key))
            if day:
                break
        if day is None:
            continue

        resource = str(
            raw.get("resourceKind") or raw.get("ResourceKind") or raw.get("resource") or ""
        ).lower()
        values = _flatten_values(raw)

        record = DailyRecord(point_number=point_number, day=day)

        if "gas" in resource or "газ" in resource:
            # Газ: приоритет — объём при нормальных условиях (V_std, Нм3).
            record.gas_nm3 = _pick(values, GAS_VOLUME_KEYS)
            if record.gas_nm3 is None:
                record.gas_nm3 = _pick(values, VOLUME_KEYS)
        else:
            record.heat_gcal = _pick(values, HEAT_KEYS)
            record.volume_m3 = _pick(values, VOLUME_KEYS)

        if record.heat_gcal is None and record.gas_nm3 is None and record.volume_m3 is None:
            continue
        records.append(record)

    return _merge_by_day(records)


def _flatten_values(raw: dict[str, Any]) -> dict[str, float]:
    """Сводит любую из форм ответа к словарю {параметр: значение}."""
    out: dict[str, float] = {}

    array = raw.get("values") or raw.get("Values")
    if isinstance(array, list):
        for item in array:
            if not isinstance(item, dict):
                continue
            # Недостоверные значения (isBad) в расчёт не берём.
            if item.get("isBad") or item.get("IsBad"):
                continue
            name = str(
                item.get("dataParameter") or item.get("DataParameter") or item.get("name") or ""
            ).strip().lower()
            value = item.get("value", item.get("Value"))
            if name and isinstance(value, (int, float)) and not isinstance(value, bool):
                out[name] = float(value)

    for key, value in raw.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out.setdefault(str(key).strip().lower(), float(value))
    return out


def _pick(values: dict[str, float], keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in values:
            return values[key]
    for name, value in values.items():
        if any(name.startswith(key) for key in keys):
            return value
    return None


def _merge_by_day(records: list[DailyRecord]) -> list[DailyRecord]:
    """Одна дата может прийти несколькими записями (тепло и газ раздельно)."""
    merged: dict[date, DailyRecord] = {}
    for rec in records:
        current = merged.get(rec.day)
        if current is None:
            merged[rec.day] = rec
            continue
        current.heat_gcal = _coalesce(current.heat_gcal, rec.heat_gcal)
        current.volume_m3 = _coalesce(current.volume_m3, rec.volume_m3)
        current.gas_nm3 = _coalesce(current.gas_nm3, rec.gas_nm3)
    return [merged[key] for key in sorted(merged)]


def _coalesce(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None:
        return b
    if b is None:
        return a
    return a + b if a != b else a
