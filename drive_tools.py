"""Тонкая обёртка над Google Drive API (только чтение метаданных).

Scope: drive.metadata.readonly.
Доступно: список файлов, имена, даты, владельцы, ссылки, mime-types.
Недоступно: содержимое файлов.
"""

from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build

import google_auth


_FILE_FIELDS = (
    "id, name, mimeType, modifiedTime, createdTime, "
    "owners(displayName,emailAddress), webViewLink, parents, size, shared, starred, trashed"
)


@lru_cache(maxsize=1)
def _service() -> Any:
    return build("drive", "v3", credentials=google_auth.get_credentials(), cache_discovery=False)


def _summarize(file: dict) -> dict:
    raw_size = file.get("size")
    return {
        "id": file.get("id"),
        "name": file.get("name"),
        "mime_type": file.get("mimeType"),
        "modified": file.get("modifiedTime"),
        "created": file.get("createdTime"),
        "owners": [
            {"name": o.get("displayName"), "email": o.get("emailAddress")}
            for o in file.get("owners", [])
        ],
        "link": file.get("webViewLink"),
        "size_bytes": int(raw_size) if raw_size else None,
        "shared": file.get("shared"),
        "starred": file.get("starred"),
    }


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def list_files(
    name_contains: str | None = None,
    modified_after: str | None = None,
    mime_type: str | None = None,
    max_results: int = 20,
) -> list[dict]:
    clauses = ["trashed = false"]
    if name_contains:
        clauses.append(f"name contains '{_escape(name_contains)}'")
    if modified_after:
        clauses.append(f"modifiedTime > '{_escape(modified_after)}'")
    if mime_type:
        clauses.append(f"mimeType = '{_escape(mime_type)}'")

    resp = (
        _service()
        .files()
        .list(
            q=" and ".join(clauses),
            pageSize=max(1, min(max_results, 100)),
            orderBy="modifiedTime desc",
            fields=f"files({_FILE_FIELDS})",
        )
        .execute()
    )
    return [_summarize(f) for f in resp.get("files", [])]


def get_file(file_id: str) -> dict:
    file = _service().files().get(fileId=file_id, fields=_FILE_FIELDS).execute()
    return _summarize(file)


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_drive_files",
        "description": (
            "Показать файлы и папки Андрея в Google Drive. Возвращает метаданные: "
            "название, mime-type, даты создания и изменения, владельцы, ссылка, размер. "
            "Содержимое файлов недоступно. По умолчанию — 20 последних изменённых файлов."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name_contains": {
                    "type": "string",
                    "description": "Подстрока для поиска в названии файла",
                },
                "modified_after": {
                    "type": "string",
                    "description": "ISO 8601 — показывать только файлы, изменённые после этого момента, например 2026-05-01T00:00:00Z",
                },
                "mime_type": {
                    "type": "string",
                    "description": (
                        "Mime-type фильтр. Примеры: 'application/vnd.google-apps.folder' (папки), "
                        "'application/vnd.google-apps.document' (Google Docs), "
                        "'application/vnd.google-apps.spreadsheet' (Sheets), "
                        "'application/pdf' (PDF)."
                    ),
                },
                "max_results": {
                    "type": "integer",
                    "description": "Максимум результатов, 1-100 (по умолчанию 20)",
                },
            },
        },
    },
    {
        "name": "get_drive_file",
        "description": "Получить метаданные конкретного файла Google Drive по его ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "ID файла из list_drive_files"},
            },
            "required": ["file_id"],
        },
    },
]


TOOL_HANDLERS = {
    "list_drive_files": list_files,
    "get_drive_file": get_file,
}
