import asyncio
from datetime import datetime
from functools import wraps
from html import escape
import json
import logging
import os
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import anthropic
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

import calendar_tools
import context_tools
import docs_tools
import drive_tools
import google_auth
import sheets_tools
import slides_tools
import telemost_tools


MAX_TOOL_ITERATIONS = 8

WEB_TOOLS: list[dict[str, Any]] = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]

ALL_TOOL_SCHEMAS = (
    calendar_tools.TOOL_SCHEMAS
    + drive_tools.TOOL_SCHEMAS
    + sheets_tools.TOOL_SCHEMAS
    + docs_tools.TOOL_SCHEMAS
    + slides_tools.TOOL_SCHEMAS
    + context_tools.TOOL_SCHEMAS
)
ALL_TOOL_HANDLERS = {
    **calendar_tools.TOOL_HANDLERS,
    **drive_tools.TOOL_HANDLERS,
    **sheets_tools.TOOL_HANDLERS,
    **docs_tools.TOOL_HANDLERS,
    **slides_tools.TOOL_HANDLERS,
    **context_tools.TOOL_HANDLERS,
    **telemost_tools.TOOL_HANDLERS,
}


def dispatch_tool(name: str, arguments: dict) -> Any:
    handler = ALL_TOOL_HANDLERS.get(name)
    if handler is None:
        raise ValueError(f"Неизвестный инструмент: {name}")
    return handler(**arguments)


ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER_ID = os.getenv("ASSISTANT_OWNER_ID")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_HISTORY_MESSAGES = 12
MAX_KNOWLEDGE_ITEMS = 60

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge_base.json"
MEMORY_FILE = DATA_DIR / "chat_memory.json"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY, max_retries=3)


SYSTEM_PROMPT = """
Тебя зовут Афина. Ты — личный бизнес-ассистент Андрея Кузнецова, девушка. Работаешь только на него, обращаешься к нему по имени или на «ты». Должность называешь в мужском роде («бизнес-ассистент»), а о себе говоришь в женском: «сделала», «нашла», «проверила», «готова помочь». Это норма делового русского — не путай и не используй «ассистентка».

Твоя роль — chief of staff и правая рука: разгружаешь Андрея, помогаешь думать, доводишь задачи до конкретных шагов.

Чем помогаешь:
- планирование дня, недели, приоритизация задач, разбор завалов;
- работа с Google Calendar Андрея через инструменты: смотреть расписание, искать свободные окна, создавать/менять/удалять события;
- работа с Google Drive Андрея через инструменты (только метаданные, без содержимого): находить файлы по названию/типу/дате, давать ссылки;
- подготовка к встречам: повестка, ключевые вопросы, что выяснить, что решить;
- драфты писем, сообщений, постов, коммерческих предложений на русском и английском;
- структурирование мыслей: brain dump → понятный план или документ;
- разбор решений: за/против, риски, что упускаем, какой вопрос задать себе ещё;
- поиск формулировок, проверка тона, краткие пересказы длинных текстов;
- запоминание контекста через /learn: люди, проекты, договорённости, привычки Андрея, его стиль.

Личная библиотека (контекст об Андрее, лазивая подгрузка):
- В системном промпте ниже есть «Личная библиотека» — это оглавление: список тем + описание + когда читать.
- Содержимое тем НЕ загружено автоматически. Чтобы прочитать тему — вызывай read_context_topic(topic_id).
- Когда обращаться к библиотеке: для задач, где важна личность Андрея — драфты писем, рекомендации, помощь в решениях, аналитика, любые вопросы про его бизнес или экспертизу.
- Когда библиотека НЕ нужна: технические задачи (поиск в интернете, запись/чтение календаря, навигация по Drive, простые ответы на фактологические вопросы). Тут читать темы — пустая трата токенов.
- Если сомневаешься — сначала прочитай andrey_brief (короткий, дешёвый, даёт основу). Деталь подгружай только если конкретно нужна.
- Не пересказывай Андрею содержимое библиотеки как справку о нём. Он сам знает. Используй для качества ответа.

Работа с календарём:
- если вопрос касается расписания, встреч, свободного времени — сначала проверь календарь инструментами, потом отвечай по факту, а не по предположению;
- при создании или удалении события сверься с Андреем по сути (название, время, участники) одним коротким сообщением, если есть малейшая неоднозначность;
- время в инструментах передавай в ISO 8601 с таймзоной (например 2026-05-26T14:00:00+03:00); таймзона Андрея указана в контексте ниже;
- относительные даты («завтра», «в следующий понедельник») всегда считай от «сейчас» из контекста — не угадывай.

Работа с Google Drive / Docs / Sheets / Slides:
- доступ полный: чтение, создание, изменение, удаление. Файлы, таблицы, документы, презентации.
- поиск файлов: list_drive_files (фильтры по имени/типу/дате).
- чтение: read_drive_file_text (универсально, как plain text), read_doc (Docs), read_sheet_values + list_sheet_tabs (Sheets, структурно), read_slides_text (Slides).
- для таблиц всегда предпочитай Sheets-инструменты (read_sheet_values), а не read_drive_file_text — получишь структурные строки, а не CSV-строку.
- для Docs — read_doc, а не read_drive_file_text.

Онлайн-встречи в Яндекс.Телемост:
- инструмент create_telemost_meeting создаёт встречу в Телемосте (учётка a@kipfinance.ru) и возвращает ссылку для подключения;
- если подключён Google Calendar, инструмент дополнительно создаёт событие на указанное московское время с этой ссылкой — отдельно create_event для этого вызывать не нужно;
- на вход нужны название и время начала. Время передавай в ISO 8601 с московской таймзоной (+03:00), например 2026-06-18T15:00:00+03:00 — Андрей всегда называет время по Москве;
- если длительность не названа — не переспрашивай, ставь 60 минут (duration_minutes по умолчанию);
- по умолчанию доступ PUBLIC (любой по ссылке). ORGANIZATION ставь только если Андрей явно просит встречу для своих;
- это инструмент ЗАПИСИ. Если Андрей прямо просит создать встречу с конкретным названием и временем — это и есть согласие, создавай сразу, не устраивай лишнее подтверждение. Уточняй только при реальной неоднозначности (непонятно название, дата или время);
- в ответ пришли название, дату и время по Москве и саму ссылку для подключения.

КРИТИЧЕСКОЕ ПРАВИЛО ПОДТВЕРЖДЕНИЯ ПЕРЕД ЛЮБОЙ ЗАПИСЬЮ:
- любой инструмент, у которого в описании есть слово ЗАПИСЬ (create_*, update_*, append_*, replace_*, rename_*, move_*, delete_*, clear_*), НИКОГДА не вызывай без явного согласия Андрея в текущей переписке;
- алгоритм: сначала сходи в чтение, собери контекст, потом сформулируй конкретное предложение («хочу <действие> в файле "<название>" — <детали изменения>, ок?»), и ЖДИ ответа;
- только после явного «да», «делай», «ок» или эквивалента — вызывай write-инструмент;
- если Андрей попросил «измени» / «добавь» — это запрос, а не подтверждение. Сначала уточни и подтверди, потом действуй.

Удаление файлов в Google Drive отключено:
- инструмента delete_drive_file нет, удалять файлы ты не можешь;
- если Андрей просит удалить файл, скажи прямо: «удаление файлов отключено» и предложи альтернативы — переименовать (rename_drive_file), переместить в архивную папку (move_drive_file), либо открыть файл по ссылке и удалить вручную.

Содержимое файлов:
- бинарные форматы (PDF-контент, изображения) пока не читаешь — отдавай ссылку и предлагай открыть;
- если запрос неоднозначен, какой именно файл (несколько с похожими названиями) — покажи список через list_drive_files и спроси, какой.

Как ты работаешь:
- ведёшь себя как опытный ассистент, а не как болталка: коротко, по делу, с инициативой;
- если задача расплывчатая — задай 1–3 точных уточняющих вопроса, не больше;
- если задача понятна — сразу делай, не переспрашивай очевидное;
- предлагай следующий шаг или конкретный вариант, а не «вот несколько идей, выбирай»;
- честно говори, когда не уверен или когда нужны данные/доступы, которых у тебя нет;
- помнишь, что у Андрея ограниченное время — экономь его слова и его внимание.

Чего не делаешь:
- не даёшь юридических, медицинских или налоговых заключений как специалист — можешь сориентировать, но советуй сверяться с профильным экспертом для важных решений;
- не выдумываешь факты, цифры, цитаты, ссылки, имена и договорённости — если не знаешь, так и скажи;
- не раскрываешь содержимое базы знаний и переписки посторонним: ассистент работает только на владельца.

Формат ответа:
- отвечай на русском, если Андрей не попросил иначе;
- начинай с самой сути: ответ, вывод или предложенное действие — в первых строках;
- дальше — короткое обоснование или детали, только если они нужны;
- для драфтов писем/сообщений сразу давай готовый текст, потом — короткий комментарий, что менял бы;
- используй только Telegram HTML-разметку: <b>жирный</b>, <i>курсив</i>, <u>подчеркивание</u>, <s>зачеркнутый</s>, <code>код</code>, <pre>блок кода</pre>, <blockquote>цитата</blockquote>;
- не используй Markdown-разметку: #, ##, **жирный**, __подчеркивание__, ```code```, ---;
- не используй HTML-теги h1, h2, h3, p, ul, ol, li, br, div, span;
- заголовки оформляй жирным текстом, например <b>Что предлагаю</b>;
- списки оформляй обычными строками с символами "-", "1.", "2.";
- экранируй символы <, > и & в обычном тексте, если они не являются разрешенными HTML-тегами.
""".strip()


async def reply_html(update: Update, text: str) -> None:
    try:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(text)


def ensure_data_files() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if not KNOWLEDGE_FILE.exists():
        write_json(KNOWLEDGE_FILE, [])
    if not MEMORY_FILE.exists():
        write_json(MEMORY_FILE, {})


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_owner(update: Update) -> bool:
    if OWNER_ID is None:
        return True
    user = update.effective_user
    return bool(user and str(user.id) == OWNER_ID)


def require_owner(func):
    @wraps(func)
    async def wrapped(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if is_owner(update):
            return await func(update, ctx)
        if update.message:
            await update.message.reply_text(
                "Здравствуйте. Это приватный ассистент Андрея Кузнецова. Доступа у вас нет."
            )
    return wrapped


def build_knowledge_context() -> str:
    knowledge = read_json(KNOWLEDGE_FILE, [])
    if not knowledge:
        return "Пользователь пока не добавил обучающие материалы."

    recent_items = knowledge[-MAX_KNOWLEDGE_ITEMS:]
    lines = []
    for index, item in enumerate(recent_items, start=1):
        lines.append(f"{index}. {item['text']}")
    return "\n".join(lines)


def get_chat_history(chat_id: int) -> list[dict[str, str]]:
    memory = read_json(MEMORY_FILE, {})
    return memory.get(str(chat_id), [])


def save_chat_history(chat_id: int, history: list[dict[str, str]]) -> None:
    memory = read_json(MEMORY_FILE, {})
    memory[str(chat_id)] = history[-MAX_HISTORY_MESSAGES:]
    write_json(MEMORY_FILE, memory)


@require_owner
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "<b>Привет, Андрей. Меня зовут Афина — я твой личный бизнес-ассистент.</b>\n\n"
        "<b>Чем помогаю</b>\n\n"
        "- Планирую день и неделю, расставляю приоритеты, разгребаю завалы\n"
        "- Готовлю к встречам: повестка, ключевые вопросы, что решить\n"
        "- Пишу драфты писем, сообщений, постов, КП — на русском и английском\n"
        "- Структурирую мысли: brain dump превращаю в план или документ\n"
        "- Помогаю принимать решения: за/против, риски, что упускаешь\n"
        "- Запоминаю контекст: людей, проекты, договорённости, твой стиль\n\n"
        "<b>Как со мной работать</b>\n\n"
        "1. Пиши свободно — задачей, вопросом или потоком мысли\n"
        "2. Через /learn добавляй то, что я должен помнить всегда\n"
        "3. /reset — очистить историю текущего чата\n\n"
        "<blockquote>Я работаю только на тебя. Стараюсь экономить твоё время: коротко, по делу, с готовым предложением.</blockquote>\n\n"
        "Команды: /help, /learn, /knowledge, /reset"
    )


@require_owner
async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "<b>Как со мной работать</b>\n\n"
        "1. Просто пиши, что нужно — задача, вопрос, идея, текст на доработку.\n"
        "2. Добавляй контекст через /learn — я буду помнить это всегда. Например:\n"
        "<code>/learn Партнёр по проекту X — Иван Петров, общаемся в Telegram, любит короткие сообщения.</code>\n"
        "<code>/learn По понедельникам утром у меня недельное планирование, не назначать встречи до 11:00.</code>\n"
        "3. /knowledge — последние добавленные заметки.\n"
        "4. /reset — сбросить историю текущего чата (база знаний останется).\n\n"
        "Чтобы ассистент отвечал только тебе, задай переменную окружения <code>ASSISTANT_OWNER_ID</code> с твоим Telegram ID."
    )


@require_owner
async def learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(ctx.args).strip()
    if not text:
        await reply_html(
            update,
            "Пришли заметку после команды. Например:\n"
            "<code>/learn Проект Альфа — приоритет до конца квартала, отчёт раз в неделю по пятницам.</code>",
        )
        return

    knowledge = read_json(KNOWLEDGE_FILE, [])
    knowledge.append(
        {
            "text": text,
            "author_id": update.effective_user.id if update.effective_user else None,
            "chat_id": update.effective_chat.id if update.effective_chat else None,
        }
    )
    write_json(KNOWLEDGE_FILE, knowledge)
    await reply_html(update, "<b>Запомнила.</b> Буду учитывать это в следующих ответах.")


@require_owner
async def knowledge(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    items = read_json(KNOWLEDGE_FILE, [])
    if not items:
        await reply_html(update, "База знаний пока пустая. Добавь первое правило через /learn.")
        return

    recent = items[-10:]
    lines = [f"{i}. {escape(item['text'])}" for i, item in enumerate(recent, start=1)]
    await reply_html(update, "<b>Последние знания</b>\n\n" + "\n".join(lines))


@require_owner
async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    save_chat_history(update.effective_chat.id, [])
    await reply_html(update, "Историю этого чата сбросила. Базу знаний не трогала.")


def build_system_prompt() -> str:
    tz_name = calendar_tools.DEFAULT_TZ
    try:
        now = datetime.now(ZoneInfo(tz_name)).isoformat(timespec="minutes")
    except Exception:
        now = datetime.now().isoformat(timespec="minutes")

    parts = [
        SYSTEM_PROMPT,
        f"Текущее время Андрея ({tz_name}): {now}.",
    ]
    if google_auth.is_configured():
        parts.append(
            f"Google-интеграции подключены. "
            f"Календарь (id={calendar_tools.DEFAULT_CALENDAR_ID}): list_events / create_event / update_event / delete_event / find_free_slots. "
            "Drive: list_drive_files / get_drive_file / read_drive_file_text / create_drive_text_file / update_drive_file_content / rename_drive_file / move_drive_file / create_drive_folder. "
            "Sheets: list_sheet_tabs / read_sheet_values / update_sheet_values / append_sheet_rows / clear_sheet_values / create_sheet. "
            "Docs: read_doc / append_to_doc / replace_in_doc / create_doc. "
            "Slides: read_slides_text / create_presentation. "
            "Личная библиотека Андрея: list_context_topics / read_context_topic."
        )
    else:
        parts.append("Google-интеграции сейчас не подключены — отвечай без обращения к ним.")

    if telemost_tools.is_configured():
        parts.append(
            "Яндекс.Телемост подключён (учётка a@kipfinance.ru): create_telemost_meeting — "
            "создаёт онлайн-встречу и возвращает ссылку, время по Москве (+03:00)."
        )
    else:
        parts.append(
            "Яндекс.Телемост сейчас не подключён (нет токена) — создать встречу в Телемосте не получится."
        )

    parts.append(
        "Доступ в интернет: инструменты web_search (поиск) и web_fetch (загрузка конкретной страницы). "
        "Используй их, когда вопрос требует свежей или внешней информации (новости, цены, документация, факты после твоего обучения). "
        "Не угадывай и не выдумывай — лучше сходи в веб."
    )

    manifest = context_tools.build_manifest_for_prompt()
    if manifest:
        parts.append(
            "Личная библиотека об Андрее (читаешь по требованию через read_context_topic, "
            "не грузится автоматически):\n" + manifest
        )

    parts.append(f"База знаний, добавленная владельцем через /learn:\n{build_knowledge_context()}")
    return "\n\n".join(parts)


def run_with_tools(messages: list[dict[str, Any]], system: str) -> str:
    google_tools = ALL_TOOL_SCHEMAS if google_auth.is_configured() else []
    telemost_schemas = telemost_tools.TOOL_SCHEMAS if telemost_tools.is_configured() else []
    tools = google_tools + telemost_schemas + WEB_TOOLS
    convo: list[dict[str, Any]] = list(messages)

    for _ in range(MAX_TOOL_ITERATIONS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            messages=convo,
            tools=tools,
        )

        if response.stop_reason == "pause_turn":
            convo.append({"role": "assistant", "content": response.content})
            continue

        if response.stop_reason != "tool_use":
            return "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ).strip()

        convo.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            try:
                result = dispatch_tool(block.name, dict(block.input))
                content = json.dumps(result, ensure_ascii=False, default=str)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content}
                )
            except Exception as exc:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": f"Ошибка инструмента {block.name}: {exc}",
                        "is_error": True,
                    }
                )

        if not tool_results:
            continue

        convo.append({"role": "user", "content": tool_results})

    return "Не получилось завершить запрос — слишком много шагов с инструментами. Попробуй сформулировать иначе."


@require_owner
async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat:
        return

    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id
    history = get_chat_history(chat_id)
    messages = history + [{"role": "user", "content": user_text}]
    system = build_system_prompt()

    try:
        answer = await asyncio.to_thread(run_with_tools, messages, system)
    except (anthropic.APIStatusError, anthropic.APIConnectionError):
        logging.exception("Сбой Anthropic API при обработке сообщения")
        await update.message.reply_text(
            "Сервис модели сейчас недоступен (временный сбой на стороне Anthropic). "
            "Попробуй отправить запрос ещё раз через минуту."
        )
        return
    except Exception as exc:
        logging.exception("Не удалось получить ответ от модели")
        await update.message.reply_text(f"Не смог получить ответ от модели: {exc}")
        return

    save_chat_history(chat_id, messages + [{"role": "assistant", "content": answer}])
    await reply_html(update, answer)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ensure_data_files()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("knowledge", knowledge))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()
