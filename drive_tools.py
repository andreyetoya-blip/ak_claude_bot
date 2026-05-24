"""Обёртка над Google Drive API: чтение, создание, изменение файлов.

OAuth-scope — drive (полный read/write, у Google нет варианта «всё кроме удаления»).
Удаление намеренно не выставлено наружу как инструмент: чтобы Афина физически
не могла удалить файл, в TOOL_SCHEMAS и TOOL_HANDLERS нет delete_drive_file.
Если когда-нибудь захочется разрешить — добавь _service().files().delete(...) обёртку
и пропиши её в обоих реестрах.
"""

import io
from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import google_auth


MIME_FOLDER = "application/vnd.google-apps.folder"
MIME_GDOC = "application/vnd.google-apps.document"
MIME_GSHEET = "application/vnd.google-apps.spreadsheet"
MIME_GSLIDES = "application/vnd.google-apps.presentation"

EXPORT_MIMES = {
    MIME_GDOC: "text/plain",
    MIME_GSHEET: "text/csv",
    MIME_GSLIDES: "text/plain",
}

MAX_READ_BYTES = 200_000


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


def read_file_text(file_id: str) -> dict:
    meta = _service().files().get(fileId=file_id, fields="id, name, mimeType").execute()
    mime = meta.get("mimeType", "")

    if mime in EXPORT_MIMES:
        export_mime = EXPORT_MIMES[mime]
        data = _service().files().export(fileId=file_id, mimeType=export_mime).execute()
    elif mime.startswith("text/") or mime in ("application/json", "application/xml"):
        data = _service().files().get_media(fileId=file_id).execute()
    else:
        return {
            "id": meta["id"],
            "name": meta["name"],
            "mime_type": mime,
            "error": "Этот тип файла нельзя прочитать как текст (изображение, PDF, бинарник). Открой по ссылке или попроси экспорт.",
        }

    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    truncated = False
    if len(text) > MAX_READ_BYTES:
        text = text[:MAX_READ_BYTES]
        truncated = True

    return {
        "id": meta["id"],
        "name": meta["name"],
        "mime_type": mime,
        "content": text,
        "truncated": truncated,
        "size_chars": len(text),
    }


def create_text_file(
    name: str,
    content: str,
    mime_type: str = "text/plain",
    parent_folder_id: str | None = None,
) -> dict:
    body: dict[str, Any] = {"name": name, "mimeType": mime_type}
    if parent_folder_id:
        body["parents"] = [parent_folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
    )
    file = (
        _service()
        .files()
        .create(body=body, media_body=media, fields=_FILE_FIELDS)
        .execute()
    )
    return _summarize(file)


def update_file_content(file_id: str, content: str, mime_type: str = "text/plain") -> dict:
    media = MediaIoBaseUpload(
        io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False
    )
    file = (
        _service()
        .files()
        .update(fileId=file_id, media_body=media, fields=_FILE_FIELDS)
        .execute()
    )
    return _summarize(file)


def rename_file(file_id: str, new_name: str) -> dict:
    file = (
        _service()
        .files()
        .update(fileId=file_id, body={"name": new_name}, fields=_FILE_FIELDS)
        .execute()
    )
    return _summarize(file)


def move_file(file_id: str, new_parent_folder_id: str) -> dict:
    current = _service().files().get(fileId=file_id, fields="parents").execute()
    prev_parents = ",".join(current.get("parents", []))
    file = (
        _service()
        .files()
        .update(
            fileId=file_id,
            addParents=new_parent_folder_id,
            removeParents=prev_parents,
            fields=_FILE_FIELDS,
        )
        .execute()
    )
    return _summarize(file)


def create_folder(name: str, parent_folder_id: str | None = None) -> dict:
    body: dict[str, Any] = {"name": name, "mimeType": MIME_FOLDER}
    if parent_folder_id:
        body["parents"] = [parent_folder_id]
    file = _service().files().create(body=body, fields=_FILE_FIELDS).execute()
    return _summarize(file)


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_drive_files",
        "description": (
            "Показать файлы и папки в Google Drive. Возвращает метаданные: название, mime-type, "
            "даты, владельцы, ссылка, размер. По умолчанию — 20 последних изменённых."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name_contains": {
                    "type": "string",
                    "description": "Подстрока для поиска в названии",
                },
                "modified_after": {
                    "type": "string",
                    "description": "ISO 8601 — файлы, изменённые после этого момента",
                },
                "mime_type": {
                    "type": "string",
                    "description": (
                        "Mime-type фильтр. Примеры: 'application/vnd.google-apps.folder', "
                        "'application/vnd.google-apps.document', 'application/vnd.google-apps.spreadsheet', "
                        "'application/vnd.google-apps.presentation', 'application/pdf'."
                    ),
                },
                "max_results": {"type": "integer", "description": "Максимум 1-100 (по умолчанию 20)"},
            },
        },
    },
    {
        "name": "get_drive_file",
        "description": "Получить метаданные одного файла Google Drive по ID.",
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "read_drive_file_text",
        "description": (
            "Прочитать содержимое файла Google Drive как текст. Поддерживает: Google Docs (как plain text), "
            "Google Sheets (как CSV), Google Slides (как plain text), обычные текстовые файлы (txt, csv, json, md). "
            "Бинарные форматы (PDF, изображения) не поддерживаются. "
            "ВАЖНО: для структурированной работы с таблицами используй read_sheet_values, для Docs — read_doc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"file_id": {"type": "string"}},
            "required": ["file_id"],
        },
    },
    {
        "name": "create_drive_text_file",
        "description": (
            "ЗАПИСЬ: создать новый текстовый файл в Google Drive. Для создания Google Docs используй create_doc, "
            "для Google Sheets — create_sheet. Перед вызовом обязательно подтверди у Андрея."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Название файла"},
                "content": {"type": "string", "description": "Содержимое"},
                "mime_type": {"type": "string", "description": "По умолчанию text/plain"},
                "parent_folder_id": {"type": "string", "description": "ID папки, иначе в корне"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "update_drive_file_content",
        "description": (
            "ЗАПИСЬ: полностью заменить содержимое текстового файла. Для структурного редактирования Docs/Sheets "
            "используй append_to_doc/replace_in_doc/update_sheet_values. Перед вызовом подтверди у Андрея."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "content": {"type": "string"},
                "mime_type": {"type": "string", "description": "По умолчанию text/plain"},
            },
            "required": ["file_id", "content"],
        },
    },
    {
        "name": "rename_drive_file",
        "description": "ЗАПИСЬ: переименовать файл или папку. Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "new_name": {"type": "string"},
            },
            "required": ["file_id", "new_name"],
        },
    },
    {
        "name": "move_drive_file",
        "description": "ЗАПИСЬ: переместить файл в другую папку. Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string"},
                "new_parent_folder_id": {"type": "string"},
            },
            "required": ["file_id", "new_parent_folder_id"],
        },
    },
    {
        "name": "create_drive_folder",
        "description": "ЗАПИСЬ: создать папку в Google Drive. Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "parent_folder_id": {"type": "string", "description": "ID родительской папки, иначе в корне"},
            },
            "required": ["name"],
        },
    },
]


TOOL_HANDLERS = {
    "list_drive_files": list_files,
    "get_drive_file": get_file,
    "read_drive_file_text": read_file_text,
    "create_drive_text_file": create_text_file,
    "update_drive_file_content": update_file_content,
    "rename_drive_file": rename_file,
    "move_drive_file": move_file,
    "create_drive_folder": create_folder,
}
