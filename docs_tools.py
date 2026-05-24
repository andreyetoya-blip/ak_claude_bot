"""Google Docs API — структурный доступ к содержимому документов.

Scope: documents.
"""

from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build

import google_auth


@lru_cache(maxsize=1)
def _service() -> Any:
    return build("docs", "v1", credentials=google_auth.get_credentials(), cache_discovery=False)


def _extract_text(doc: dict) -> str:
    parts: list[str] = []
    for element in doc.get("body", {}).get("content", []):
        para = element.get("paragraph")
        if not para:
            continue
        for run in para.get("elements", []):
            text_run = run.get("textRun")
            if text_run and text_run.get("content"):
                parts.append(text_run["content"])
    return "".join(parts)


def read_doc(document_id: str) -> dict:
    doc = _service().documents().get(documentId=document_id).execute()
    text = _extract_text(doc)
    return {
        "document_id": document_id,
        "title": doc.get("title"),
        "text": text,
        "char_count": len(text),
    }


def append_text(document_id: str, text: str) -> dict:
    doc = _service().documents().get(documentId=document_id, fields="body(content(endIndex))").execute()
    end_index = doc["body"]["content"][-1]["endIndex"] - 1

    requests = [
        {"insertText": {"location": {"index": end_index}, "text": text}}
    ]
    resp = (
        _service()
        .documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute()
    )
    return {"document_id": document_id, "replies_count": len(resp.get("replies", []))}


def replace_text(document_id: str, find: str, replace: str, match_case: bool = True) -> dict:
    requests = [
        {
            "replaceAllText": {
                "containsText": {"text": find, "matchCase": match_case},
                "replaceText": replace,
            }
        }
    ]
    resp = (
        _service()
        .documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute()
    )
    occurrences = sum(r.get("replaceAllText", {}).get("occurrencesChanged", 0) for r in resp.get("replies", []))
    return {"document_id": document_id, "occurrences_changed": occurrences}


def create_doc(title: str, initial_text: str | None = None) -> dict:
    doc = _service().documents().create(body={"title": title}).execute()
    doc_id = doc["documentId"]
    if initial_text:
        append_text(doc_id, initial_text)
    return {"document_id": doc_id, "title": title, "url": f"https://docs.google.com/document/d/{doc_id}/edit"}


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "read_doc",
        "description": "Прочитать содержимое Google Doc как простой текст (без форматирования).",
        "input_schema": {
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
        },
    },
    {
        "name": "append_to_doc",
        "description": (
            "ЗАПИСЬ: добавить текст в конец Google Doc. Перед вызовом подтверди у Андрея, "
            "какой документ и какой текст добавляешь."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "text": {"type": "string", "description": "Текст для добавления. Начни с '\\n' если нужен перенос строки."},
            },
            "required": ["document_id", "text"],
        },
    },
    {
        "name": "replace_in_doc",
        "description": (
            "ЗАПИСЬ: заменить все вхождения текста в Google Doc. Перед вызовом подтверди у Андрея: "
            "документ, что искать, на что заменять."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string"},
                "find": {"type": "string"},
                "replace": {"type": "string"},
                "match_case": {"type": "boolean", "description": "По умолчанию true"},
            },
            "required": ["document_id", "find", "replace"],
        },
    },
    {
        "name": "create_doc",
        "description": "ЗАПИСЬ: создать новый Google Doc. Перед вызовом подтверди у Андрея.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "initial_text": {"type": "string", "description": "Необязательно — стартовый текст"},
            },
            "required": ["title"],
        },
    },
]


TOOL_HANDLERS = {
    "read_doc": read_doc,
    "append_to_doc": append_text,
    "replace_in_doc": replace_text,
    "create_doc": create_doc,
}
