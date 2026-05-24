"""Тонкая обёртка над Google Calendar API для использования из tg_bot.py.

Авторизация — через общий модуль google_auth.
Дополнительные опциональные env vars:
    GOOGLE_CALENDAR_ID  (по умолчанию "primary")
    GOOGLE_CALENDAR_TZ  (по умолчанию "Europe/Moscow")
"""

import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build

import google_auth

DEFAULT_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DEFAULT_TZ = os.getenv("GOOGLE_CALENDAR_TZ", "Europe/Moscow")


@lru_cache(maxsize=1)
def _service() -> Any:
    return build("calendar", "v3", credentials=google_auth.get_credentials(), cache_discovery=False)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_summary(event: dict) -> dict:
    start = event.get("start", {})
    end = event.get("end", {})
    return {
        "id": event.get("id"),
        "summary": event.get("summary", "(без названия)"),
        "start": start.get("dateTime") or start.get("date"),
        "end": end.get("dateTime") or end.get("date"),
        "location": event.get("location"),
        "attendees": [a.get("email") for a in event.get("attendees", []) if a.get("email")],
        "description": event.get("description"),
        "html_link": event.get("htmlLink"),
    }


def list_events(
    time_min: str | None = None,
    time_max: str | None = None,
    query: str | None = None,
    max_results: int = 20,
    calendar_id: str | None = None,
) -> list[dict]:
    now = datetime.now(timezone.utc)
    tmin = time_min or now.isoformat()
    tmax = time_max or (now + timedelta(days=7)).isoformat()
    resp = (
        _service()
        .events()
        .list(
            calendarId=calendar_id or DEFAULT_CALENDAR_ID,
            timeMin=tmin,
            timeMax=tmax,
            q=query,
            singleEvents=True,
            orderBy="startTime",
            maxResults=max_results,
        )
        .execute()
    )
    return [_event_summary(e) for e in resp.get("items", [])]


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str | None = None,
) -> dict:
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start, "timeZone": DEFAULT_TZ},
        "end": {"dateTime": end, "timeZone": DEFAULT_TZ},
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": email} for email in attendees]

    event = (
        _service()
        .events()
        .insert(calendarId=calendar_id or DEFAULT_CALENDAR_ID, body=body, sendUpdates="all")
        .execute()
    )
    return _event_summary(event)


def update_event(
    event_id: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    calendar_id: str | None = None,
) -> dict:
    cal_id = calendar_id or DEFAULT_CALENDAR_ID
    patch: dict[str, Any] = {}
    if summary is not None:
        patch["summary"] = summary
    if description is not None:
        patch["description"] = description
    if location is not None:
        patch["location"] = location
    if start is not None:
        patch["start"] = {"dateTime": start, "timeZone": DEFAULT_TZ}
    if end is not None:
        patch["end"] = {"dateTime": end, "timeZone": DEFAULT_TZ}
    if attendees is not None:
        patch["attendees"] = [{"email": email} for email in attendees]

    event = (
        _service()
        .events()
        .patch(calendarId=cal_id, eventId=event_id, body=patch, sendUpdates="all")
        .execute()
    )
    return _event_summary(event)


def delete_event(event_id: str, calendar_id: str | None = None) -> dict:
    _service().events().delete(
        calendarId=calendar_id or DEFAULT_CALENDAR_ID,
        eventId=event_id,
        sendUpdates="all",
    ).execute()
    return {"deleted": True, "event_id": event_id}


def find_free_slots(
    time_min: str,
    time_max: str,
    duration_minutes: int,
    calendar_id: str | None = None,
) -> list[dict]:
    cal_id = calendar_id or DEFAULT_CALENDAR_ID
    resp = (
        _service()
        .freebusy()
        .query(
            body={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": cal_id}],
            }
        )
        .execute()
    )
    busy = resp["calendars"][cal_id].get("busy", [])

    window_start = _parse_iso(time_min)
    window_end = _parse_iso(time_max)
    needed = timedelta(minutes=duration_minutes)

    busy_intervals = sorted(
        (_parse_iso(b["start"]), _parse_iso(b["end"])) for b in busy
    )

    cursor = window_start
    slots: list[dict] = []
    for b_start, b_end in busy_intervals:
        if b_start > cursor and (b_start - cursor) >= needed:
            slots.append({"start": cursor.isoformat(), "end": b_start.isoformat()})
        if b_end > cursor:
            cursor = b_end
    if window_end > cursor and (window_end - cursor) >= needed:
        slots.append({"start": cursor.isoformat(), "end": window_end.isoformat()})

    return slots


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_events",
        "description": (
            "Получить события календаря Андрея за указанный период. "
            "Возвращает список событий: id, summary, start, end, location, attendees, description. "
            "По умолчанию — на ближайшие 7 дней от текущего момента."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {
                    "type": "string",
                    "description": "Начало периода, ISO 8601 с таймзоной, например 2026-05-24T00:00:00+03:00",
                },
                "time_max": {
                    "type": "string",
                    "description": "Конец периода, ISO 8601 с таймзоной",
                },
                "query": {
                    "type": "string",
                    "description": "Полнотекстовый поиск по событиям (название, описание, участники)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Максимум событий (по умолчанию 20)",
                },
            },
        },
    },
    {
        "name": "create_event",
        "description": "Создать событие в календаре Андрея. Подтверждай с ним детали перед созданием, если что-то неясно.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Название события"},
                "start": {
                    "type": "string",
                    "description": "Начало, ISO 8601 с таймзоной, например 2026-05-26T14:00:00+03:00",
                },
                "end": {
                    "type": "string",
                    "description": "Конец, ISO 8601 с таймзоной",
                },
                "description": {"type": "string", "description": "Описание/заметки"},
                "location": {"type": "string", "description": "Место (адрес, ссылка на встречу)"},
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Email-адреса участников (получат приглашение)",
                },
            },
            "required": ["summary", "start", "end"],
        },
    },
    {
        "name": "update_event",
        "description": "Изменить существующее событие. Указывай только те поля, которые меняешь. event_id бери из list_events.",
        "input_schema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
                "summary": {"type": "string"},
                "start": {"type": "string", "description": "ISO 8601 с таймзоной"},
                "end": {"type": "string", "description": "ISO 8601 с таймзоной"},
                "description": {"type": "string"},
                "location": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["event_id"],
        },
    },
    {
        "name": "delete_event",
        "description": "Удалить событие. Перед удалением убедись, что это нужное событие — сверь с Андреем по названию и времени.",
        "input_schema": {
            "type": "object",
            "properties": {"event_id": {"type": "string"}},
            "required": ["event_id"],
        },
    },
    {
        "name": "find_free_slots",
        "description": "Найти свободные окна заданной длительности в указанном диапазоне времени.",
        "input_schema": {
            "type": "object",
            "properties": {
                "time_min": {"type": "string", "description": "ISO 8601 с таймзоной"},
                "time_max": {"type": "string", "description": "ISO 8601 с таймзоной"},
                "duration_minutes": {"type": "integer", "description": "Длительность окна в минутах"},
            },
            "required": ["time_min", "time_max", "duration_minutes"],
        },
    },
]


TOOL_HANDLERS = {
    "list_events": list_events,
    "create_event": create_event,
    "update_event": update_event,
    "delete_event": delete_event,
    "find_free_slots": find_free_slots,
}


def dispatch(name: str, arguments: dict) -> Any:
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Неизвестный инструмент: {name}")
    return handler(**arguments)
