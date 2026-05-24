"""Google Slides API — чтение текста из презентаций и создание новых.

Scope: presentations.
"""

from functools import lru_cache
from typing import Any

from googleapiclient.discovery import build

import google_auth


@lru_cache(maxsize=1)
def _service() -> Any:
    return build("slides", "v1", credentials=google_auth.get_credentials(), cache_discovery=False)


def _slide_text(slide: dict) -> str:
    parts: list[str] = []
    for element in slide.get("pageElements", []):
        shape = element.get("shape")
        if not shape:
            continue
        text = shape.get("text", {})
        for te in text.get("textElements", []):
            run = te.get("textRun")
            if run and run.get("content"):
                parts.append(run["content"])
    return "".join(parts).strip()


def read_slides(presentation_id: str) -> dict:
    pres = _service().presentations().get(presentationId=presentation_id).execute()
    slides_out = []
    for index, slide in enumerate(pres.get("slides", []), start=1):
        slides_out.append(
            {
                "index": index,
                "object_id": slide.get("objectId"),
                "text": _slide_text(slide),
            }
        )
    return {
        "presentation_id": presentation_id,
        "title": pres.get("title"),
        "slide_count": len(slides_out),
        "slides": slides_out,
    }


def create_presentation(title: str) -> dict:
    pres = _service().presentations().create(body={"title": title}).execute()
    pid = pres["presentationId"]
    return {
        "presentation_id": pid,
        "title": title,
        "url": f"https://docs.google.com/presentation/d/{pid}/edit",
    }


TOOL_SCHEMAS: list[dict] = [
    {
        "name": "read_slides_text",
        "description": (
            "Прочитать текст со всех слайдов Google Slides. Возвращает массив слайдов: "
            "index, object_id, text. Без форматирования и изображений."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"presentation_id": {"type": "string"}},
            "required": ["presentation_id"],
        },
    },
    {
        "name": "create_presentation",
        "description": (
            "ЗАПИСЬ: создать пустую Google Slides. Перед вызовом подтверди у Андрея. "
            "Заполнение слайдов содержимым через API сложное — после создания дай ссылку и предложи "
            "наполнить вручную или попроси Андрея описать структуру."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
]


TOOL_HANDLERS = {
    "read_slides_text": read_slides,
    "create_presentation": create_presentation,
}
