"""Конфигурация сервиса. Все параметры читаются из переменных окружения (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv не обязателен, если переменные заданы в окружении
    def load_dotenv(*_a, **_kw):
        return False

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name).replace(",", ".")
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on", "да"}


@dataclass
class LersSettings:
    """Доступ к серверу ЛЭРС УЧЁТ.

    Авторизация — заголовок ``x-lers-api-key`` (docs.lers.ru/dev/rest).
    Если API-ключа нет, но есть логин/пароль, ключ можно выпустить в карточке
    пользователя (вкладка «Приложения») либо использовать basic-auth фолбэк.
    """

    base_url: str = field(default_factory=lambda: _env("LERS_BASE_URL", "http://109.201.97.4:10000"))
    api_key: str = field(default_factory=lambda: _env("LERS_API_KEY"))
    login: str = field(default_factory=lambda: _env("LERS_LOGIN"))
    password: str = field(default_factory=lambda: _env("LERS_PASSWORD"))
    timeout: int = field(default_factory=lambda: _env_int("LERS_TIMEOUT", 60))
    # Тайм-аут установки соединения — держим коротким, чтобы --check не «висел»
    # на недоступном сервере до общего тайм-аута чтения.
    connect_timeout: int = field(default_factory=lambda: _env_int("LERS_CONNECT_TIMEOUT", 10))
    verify_ssl: bool = field(default_factory=lambda: _env_bool("LERS_VERIFY_SSL", False))
    # Максимум одновременных запросов к серверу ЛЭРС (щадящий режим для 55 точек)
    concurrency: int = field(default_factory=lambda: _env_int("LERS_CONCURRENCY", 4))
    # Ретраи на 429/5xx/таймаут (эксп. пауза от base_delay)
    retries: int = field(default_factory=lambda: _env_int("LERS_RETRIES", 4))
    retry_base_delay: float = field(default_factory=lambda: _env_float("LERS_RETRY_BASE_DELAY", 1.0))

    @property
    def api_root(self) -> str:
        return self.base_url.rstrip("/") + "/api/v1"


@dataclass
class OneCSettings:
    """Доступ к 1С.

    Поддерживаются три режима (``ONEC_MODE``):
      * ``odata``  — штатный интерфейс OData ``/<база>/odata/standard.odata`` (рекомендуется);
      * ``http``   — самописный HTTP-сервис 1С, отдающий JSON;
      * ``manual`` — затраты подгружаются из файла ``data/costs.xlsx`` (режим по умолчанию,
        пока не выданы доступы к 1С; позволяет собирать отчёт целиком).
    """

    mode: str = field(default_factory=lambda: _env("ONEC_MODE", "manual").lower())
    base_url: str = field(default_factory=lambda: _env("ONEC_BASE_URL"))
    username: str = field(default_factory=lambda: _env("ONEC_USERNAME"))
    password: str = field(default_factory=lambda: _env("ONEC_PASSWORD"))
    timeout: int = field(default_factory=lambda: _env_int("ONEC_TIMEOUT", 120))
    # Имя ресурса OData с затратами и имена полей — уточняются после выдачи доступа
    odata_resource: str = field(
        default_factory=lambda: _env("ONEC_ODATA_RESOURCE", "AccumulationRegister_Затраты/Turnovers")
    )
    field_object: str = field(default_factory=lambda: _env("ONEC_FIELD_OBJECT", "Объект"))
    field_amount: str = field(default_factory=lambda: _env("ONEC_FIELD_AMOUNT", "СуммаОборот"))
    http_path: str = field(default_factory=lambda: _env("ONEC_HTTP_PATH", "/hs/costs/period"))
    manual_file: Path = field(
        default_factory=lambda: BASE_DIR / _env("ONEC_MANUAL_FILE", "data/costs.xlsx")
    )


@dataclass
class MaxSettings:
    """Бот в мессенджере MAX (МАКС).

    Домен и способ авторизации — по актуальной документации dev.max.ru/docs-api:
    запросы идут на ``platform-api2.max.ru``, токен передаётся заголовком
    ``Authorization``. Передача токена через query-параметр больше не поддерживается.
    """

    token: str = field(default_factory=lambda: _env("MAX_BOT_TOKEN"))
    base_url: str = field(default_factory=lambda: _env("MAX_API_BASE", "https://platform-api2.max.ru"))
    timeout: int = field(default_factory=lambda: _env_int("MAX_TIMEOUT", 60))
    # Проверять TLS-сертификат сервера MAX
    verify_ssl: bool = field(default_factory=lambda: _env_bool("MAX_VERIFY_SSL", True))
    # Доп. доверенные CA (Russian Trusted Root/Sub CA Минцифры) — файл или каталог.
    # Без них проверка сертификата *.max.ru падает: корня нет в наборе certifi.
    ca_bundle: Path = field(default_factory=lambda: BASE_DIR / _env("MAX_CA_BUNDLE", "data/certs"))
    # Чаты, куда бот сам присылает отчёты по расписанию (через запятую)
    broadcast_chats: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _env("MAX_BROADCAST_CHAT_IDS").replace(" ", "").split(",") if x
        )
    )
    # Кто может запрашивать отчёты вручную. Пусто = разрешено всем, кто нашёл бота
    allowed_users: tuple[int, ...] = field(
        default_factory=lambda: tuple(
            int(x) for x in _env("MAX_ALLOWED_USER_IDS").replace(" ", "").split(",") if x
        )
    )
    # Пауза после загрузки файла перед отправкой (сервер MAX обрабатывает вложение)
    upload_settle_seconds: float = field(default_factory=lambda: _env_float("MAX_UPLOAD_SETTLE", 3.0))
    # Ретраи на 429/5xx/таймаут (эксп. пауза от base_delay)
    retries: int = field(default_factory=lambda: _env_int("MAX_RETRIES", 4))
    retry_base_delay: float = field(default_factory=lambda: _env_float("MAX_RETRY_BASE_DELAY", 1.0))
    # Сколько раз повторять отправку вложения при ответе attachment.not.ready
    attachment_retries: int = field(default_factory=lambda: _env_int("MAX_ATTACHMENT_RETRIES", 4))


@dataclass
class ReportSettings:
    """Параметры расчёта и оформления отчёта."""

    # Теплотворная способность газа, ккал/Нм3 (из ТЗ; подлежит подтверждению заказчиком)
    gas_calorific_kcal: float = field(default_factory=lambda: _env_float("GAS_CALORIFIC_KCAL", 8200.0))
    # Порог КПД, ниже которого ячейка подсвечивается красным, %
    efficiency_threshold: float = field(default_factory=lambda: _env_float("EFFICIENCY_THRESHOLD", 80.0))
    points_file: Path = field(default_factory=lambda: BASE_DIR / _env("POINTS_FILE", "data/points.xlsx"))
    output_dir: Path = field(default_factory=lambda: BASE_DIR / _env("OUTPUT_DIR", "out"))


@dataclass
class ScheduleSettings:
    """Расписание опроса (по ТЗ: ЛЭРС — раз в неделю, 1С — раз в месяц)."""

    timezone: str = field(default_factory=lambda: _env("TZ_NAME", "Europe/Moscow"))
    weekly_day: str = field(default_factory=lambda: _env("WEEKLY_DAY", "mon"))
    weekly_hour: int = field(default_factory=lambda: _env_int("WEEKLY_HOUR", 7))
    monthly_day: int = field(default_factory=lambda: _env_int("MONTHLY_DAY", 5))
    monthly_hour: int = field(default_factory=lambda: _env_int("MONTHLY_HOUR", 8))


@dataclass
class Settings:
    lers: LersSettings = field(default_factory=LersSettings)
    onec: OneCSettings = field(default_factory=OneCSettings)
    max_bot: MaxSettings = field(default_factory=MaxSettings)
    report: ReportSettings = field(default_factory=ReportSettings)
    schedule: ScheduleSettings = field(default_factory=ScheduleSettings)
    db_path: Path = field(default_factory=lambda: BASE_DIR / _env("DB_PATH", "data/archive.sqlite3"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO").upper())
    # Порт health-сервера для облака (Amvera проверяет containerPort). PORT задаёт Amvera.
    port: int = field(default_factory=lambda: _env_int("PORT", 80))


settings = Settings()
