"""Личная библиотека Андрея — лениво подгружаемые темы из ./context/.

В системный промпт всегда инжектится только манифест (оглавление).
Содержимое тем читается по требованию через инструменты.
"""

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONTEXT_DIR = BASE_DIR / "context"
MANIFEST_FILE = CONTEXT_DIR / "manifest.json"


def load_manifest() -> dict:
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def list_topics() -> dict:
    manifest = load_manifest()
    return {
        topic_id: {
            "description": info.get("description", ""),
            "when_to_use": info.get("when_to_use", ""),
        }
        for topic_id, info in manifest.items()
    }


def read_topic(topic_id: str) -> dict:
    manifest = load_manifest()
    if topic_id not in manifest:
        return {
            "error": f"Темы '{topic_id}' нет в манифесте",
            "available_topics": list(manifest.keys()),
        }
    file_path = CONTEXT_DIR / f"{topic_id}.md"
    try:
        content = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"error": f"Файл темы '{topic_id}' не найден на диске"}
    return {
        "topic_id": topic_id,
        "content": content,
        "char_count": len(content),
    }


def build_manifest_for_prompt() -> str:
    """Короткое оглавление библиотеки — для инжекта в системный промпт."""
    manifest = load_manifest()
    if not manifest:
        return ""
    lines = []
    for topic_id, info in manifest.items():
        desc = info.get("description", "")
        when = info.get("when_to_use", "")
        lines.append(f"- <b>{topic_id}</b>: {desc}. <i>Когда читать:</i> {when}")
    return "\n".join(lines)


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "list_context_topics",
        "description": (
            "Показать оглавление личной библиотеки Андрея: какие темы доступны и когда их стоит читать. "
            "Используй, если оглавление уже в системном промпте недостаточно понятно или прошло много шагов."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_context_topic",
        "description": (
            "Прочитать конкретную тему из библиотеки Андрея. topic_id берётся из оглавления "
            "(см. системный промпт или list_context_topics). Читай только когда контекст темы реально нужен "
            "для качественного ответа — это экономит токены и держит фокус. Для технических задач "
            "(поиск в интернете, календарь, Drive-навигация, простые ответы) личный контекст обычно не нужен."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic_id": {
                    "type": "string",
                    "description": "Идентификатор темы из манифеста, например 'andrey_brief'",
                }
            },
            "required": ["topic_id"],
        },
    },
]


TOOL_HANDLERS = {
    "list_context_topics": list_topics,
    "read_context_topic": read_topic,
}
