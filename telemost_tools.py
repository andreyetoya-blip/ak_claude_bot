"""Создание онлайн-встреч в Яндекс.Телемост + событие в Google Calendar.

Telemost API сам по себе не хранит дату/время — он только создаёт конференцию
и отдаёт постоянную ссылку для подключения. Чтобы встреча была «запланирована»
на конкретное московское время, ссылка дополнительно кладётся в событие
Google Calendar Андрея (через calendar_tools).

Авторизация в Telemost — OAuth-токен учётки a@kipfinance.ru в env var:
    YANDEX_TELEMOST_TOKEN

Токен получается один раз вручную: зарегистрировать приложение на
https://oauth.yandex.ru/ со scope telemost-api:conferences.create (плюс .read
и .update при желании), авторизоваться под a@kipfinance.ru и сохранить токен.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

import httpx

import calendar_tools
import google_auth

logger = logging.getLogger(__name__)

API_URL = "https://cloud-api.yandex.net/v1/telemost-api/conferences"
TOKEN_ENV = "YANDEX_TELEMOST_TOKEN"

DEFAULT_DURATION_MINUTES = 60
DEFAULT_ACCESS_LEVEL = "PUBLIC"
CALENDAR_RETRIES = 2


def is_configured() -> bool:
    return bool(os.getenv(TOKEN_ENV))


def _create_conference(access_level: str = DEFAULT_ACCESS_LEVEL) -> dict:
    token = os.environ[TOKEN_ENV]
    body = {"waiting_room_level": access_level}
    resp = httpx.post(
        API_URL,
        headers={
            "Authorization": f"OAuth {token}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Telemost API вернул {resp.status_code}: {resp.text[:500]}"
        )
    return resp.json()


def _create_event_with_retry(**kwargs) -> dict:
    """Создать событие в календаре с парой повторов на случай разовых сбоев Google."""
    last_exc: Exception | None = None
    for attempt in range(1, CALENDAR_RETRIES + 1):
        try:
            return calendar_tools.create_event(**kwargs)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "create_event failed (попытка %d/%d): %s", attempt, CALENDAR_RETRIES, exc
            )
            if attempt < CALENDAR_RETRIES:
                time.sleep(1.0 * attempt)
    raise last_exc  # type: ignore[misc]


def _compute_end(start: str, end: str | None, duration_minutes: int | None) -> str:
    if end:
        return end
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    minutes = duration_minutes or DEFAULT_DURATION_MINUTES
    return (start_dt + timedelta(minutes=minutes)).isoformat()


def create_telemost_meeting(
    summary: str,
    start: str,
    end: str | None = None,
    duration_minutes: int | None = None,
    access_level: str = DEFAULT_ACCESS_LEVEL,
) -> dict:
    """Создать встречу в Яндекс.Телемост и (если есть Google) положить её в календарь.

    start/end — ISO 8601 с московской таймзоной, например 2026-06-18T15:00:00+03:00.
    Возвращает join_url, conference_id, время встречи и, если событие создано,
    ссылку на событие календаря.
    """
    if access_level not in ("PUBLIC", "ORGANIZATION", "ADMINS"):
        access_level = DEFAULT_ACCESS_LEVEL

    conference = _create_conference(access_level=access_level)
    join_url = conference.get("join_url")
    conference_id = conference.get("id")

    end_value = _compute_end(start, end, duration_minutes)

    result: dict[str, Any] = {
        "join_url": join_url,
        "conference_id": conference_id,
        "summary": summary,
        "start": start,
        "end": end_value,
        "access_level": access_level,
        "calendar_event_created": False,
    }

    if google_auth.is_configured():
        description = f"Онлайн-встреча в Яндекс.Телемост\nСсылка для подключения: {join_url}"
        try:
            event = _create_event_with_retry(
                summary=summary,
                start=start,
                end=end_value,
                description=description,
                location=join_url,
            )
            result["calendar_event_created"] = True
            result["calendar_event_link"] = event.get("html_link")
            result["calendar_event_id"] = event.get("id")
        except Exception as exc:
            logger.exception("Не удалось создать событие календаря для встречи '%s'", summary)
            result["calendar_event_error"] = str(exc)
            result["warning"] = (
                "ВНИМАНИЕ: встреча в Телемосте создана и ссылка работает, "
                "но событие в Google Calendar НЕ создано из-за ошибки. "
                "Обязательно сообщи об этом Андрею и предложи добавить событие вручную или повторить."
            )

    return result


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "create_telemost_meeting",
        "description": (
            "Создать онлайн-встречу в Яндекс.Телемост (учётка a@kipfinance.ru) и вернуть ссылку для подключения. "
            "Если подключён Google Calendar — дополнительно создаёт событие на указанное московское время с этой ссылкой. "
            "На вход: название, дата и время (по Москве). Это ЗАПИСЬ — вызывай только когда Андрей явно попросил создать встречу."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Название встречи"},
                "start": {
                    "type": "string",
                    "description": "Начало встречи, ISO 8601 с московской таймзоной, например 2026-06-18T15:00:00+03:00",
                },
                "end": {
                    "type": "string",
                    "description": "Конец встречи, ISO 8601 с таймзоной. Необязательно — если не задан, берётся duration_minutes.",
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Длительность в минутах, если не задан end. По умолчанию 60.",
                },
                "access_level": {
                    "type": "string",
                    "enum": ["PUBLIC", "ORGANIZATION", "ADMINS"],
                    "description": (
                        "Кто заходит без стука. PUBLIC — любой по ссылке (по умолчанию, для внешних участников); "
                        "ORGANIZATION — только сотрудники kipfinance.ru, остальные стучатся; ADMINS — только админы."
                    ),
                },
            },
            "required": ["summary", "start"],
        },
    },
]


TOOL_HANDLERS = {
    "create_telemost_meeting": create_telemost_meeting,
}


def dispatch(name: str, arguments: dict) -> Any:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Неизвестный инструмент: {name}")
    return handler(**arguments)
