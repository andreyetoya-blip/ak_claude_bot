"""Google Sheets API — структурный доступ к ячейкам и строкам.

Scope: spreadsheets.
"""

from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build

import google_auth


@lru_cache(maxsize=1)
def _service() -> Any:
    return build("sheets", "v4", credentials=google_auth.get_credentials(), cache_discovery=False)


def list_tabs(spreadsheet_id: str) -> dict:
    meta = (
        _service()
        .spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="properties(title), sheets(properties(sheetId,title,index,gridProperties))")
        .execute()
    )
    return {
        "spreadsheet_title": meta["properties"]["title"],
        "tabs": [
            {
                "sheet_id": s["properties"]["sheetId"],
                "title": s["properties"]["title"],
                "index": s["properties"]["index"],
                "rows": s["properties"].get("gridProperties", {}).get("rowCount"),
                "columns": s["properties"].get("gridProperties", {}).get("columnCount"),
            }
            for s in meta.get("sheets", [])
        ],
    }


def read_values(spreadsheet_id: str, range_a1: str) -> dict:
    resp = (
        _service()
        .spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_a1)
        .execute()
    )
    return {
        "range": resp.get("range"),
        "rows": resp.get("values", []),
        "row_count": len(resp.get("values", [])),
    }


def update_values(spreadsheet_id: str, range_a1: str, rows: list[list[Any]]) -> dict:
    resp = (
        _service()
        .spreadsheets()
        .values()
        .update(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        )
        .execute()
    )
    return {
        "updated_range": resp.get("updatedRange"),
        "updated_rows": resp.get("updatedRows"),
        "updated_columns": resp.get("updatedColumns"),
        "updated_cells": resp.get("updatedCells"),
    }


def append_values(spreadsheet_id: str, range_a1: str, rows: list[list[Any]]) -> dict:
    resp = (
        _service()
        .spreadsheets()
        .values()
        .append(
            spreadsheetId=spreadsheet_id,
            range=range_a1,
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        )
        .execute()
    )
    updates = resp.get("updates", {})
    return {
        "updated_range": updates.get("updatedRange"),
        "updated_rows": updates.get("updatedRows"),
        "updated_cells": updates.get("updatedCells"),
    }


def clear_values(spreadsheet_id: str, range_a1: str) -> dict:
    resp = (
        _service()
        .spreadsheets()
        .values()
        .clear(spreadsheetId=spreadsheet_id, range=range_a1, body={})
        .execute()
    )
    return {"cleared_range": resp.get("clearedRange")}


def create_sheet(title: str) -> dict:
    body = {"properties": {"title": title}}
    resp = _service().spreadsheets().create(body=body, fields="spreadsheetId,spreadsheetUrl").execute()
    return {
        "spreadsheet_id": resp["spreadsheetId"],
        "url": resp.get("spreadsheetUrl"),
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_sheet_tabs",
        "description": "Получить список листов (tabs) внутри Google Sheets и их размеры.",
        "input_schema": {
            "type": "object",
            "properties": {"spreadsheet_id": {"type": "string"}},
            "required": ["spreadsheet_id"],
        },
    },
    {
        "name": "read_sheet_values",
        "description": (
            "Прочитать значения из диапазона Google Sheets. Возвращает rows — массив массивов. "
            "Диапазон в нотации A1: 'Sheet1!A1:D50' или просто 'A1:D50' для первого листа. "
            "Для последних N строк можно сначала вызвать list_sheet_tabs (узнать row count), потом запросить нужный диапазон."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range_a1": {"type": "string", "description": "Например 'Лист1!A1:E20'"},
            },
            "required": ["spreadsheet_id", "range_a1"],
        },
    },
    {
        "name": "update_sheet_values",
        "description": (
            "ЗАПИСЬ: заменить значения в диапазоне (перезаписывает существующие ячейки). "
            "Перед вызовом ОБЯЗАТЕЛЬНО подтверди у Андрея: какой файл, какой диапазон, какие значения."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range_a1": {"type": "string"},
                "rows": {
                    "type": "array",
                    "items": {"type": "array"},
                    "description": "Массив строк (каждая строка — массив ячеек)",
                },
            },
            "required": ["spreadsheet_id", "range_a1", "rows"],
        },
    },
    {
        "name": "append_sheet_rows",
        "description": (
            "ЗАПИСЬ: добавить новые строки в конец таблицы. range_a1 указывает таблицу/диапазон поиска. "
            "Перед вызовом подтверди у Андрея."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range_a1": {"type": "string", "description": "Например 'Лист1!A:E'"},
                "rows": {"type": "array", "items": {"type": "array"}},
            },
            "required": ["spreadsheet_id", "range_a1", "rows"],
        },
    },
    {
        "name": "clear_sheet_values",
        "description": "ЗАПИСЬ: очистить значения в диапазоне (структуру не трогает). Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "range_a1": {"type": "string"},
            },
            "required": ["spreadsheet_id", "range_a1"],
        },
    },
    {
        "name": "create_sheet",
        "description": "ЗАПИСЬ: создать новый Google Sheets (пустой). Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
]


TOOL_HANDLERS = {
    "list_sheet_tabs": list_tabs,
    "read_sheet_values": read_values,
    "update_sheet_values": update_values,
    "append_sheet_rows": append_values,
    "clear_sheet_values": clear_values,
    "create_sheet": create_sheet,
}
