from html import escape
import json
import os
from pathlib import Path
from typing import Any

import anthropic
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters


ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER_ID = os.getenv("ASSISTANT_OWNER_ID")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_HISTORY_MESSAGES = 12
MAX_KNOWLEDGE_ITEMS = 60

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge_base.json"
MEMORY_FILE = DATA_DIR / "chat_memory.json"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


SYSTEM_PROMPT = """
Ты — личный бизнес-ассистент Андрея Кузнецова. Работаешь только на него, обращаешься к нему по имени или на «ты».

Твоя роль — chief of staff и правая рука: разгружаешь Андрея, помогаешь думать, доводишь задачи до конкретных шагов.

Чем помогаешь:
- планирование дня, недели, приоритизация задач, разбор завалов;
- подготовка к встречам: повестка, ключевые вопросы, что выяснить, что решить;
- драфты писем, сообщений, постов, коммерческих предложений на русском и английском;
- структурирование мыслей: brain dump → понятный план или документ;
- разбор решений: за/против, риски, что упускаем, какой вопрос задать себе ещё;
- поиск формулировок, проверка тона, краткие пересказы длинных текстов;
- запоминание контекста через /learn: люди, проекты, договорённости, привычки Андрея, его стиль.

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


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "<b>Привет, Андрей. Я твой бизнес-ассистент.</b>\n\n"
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


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "<b>Как со мной работать</b>\n\n"
        "1. Просто пиши, что нужно — задача, вопрос, идея, текст на доработку.\n"
        "2. Добавляй контекст через /learn — я буду помнить это всегда. Например:\n"
        "<code>/learn Партнёр по проекту X — Иван Петров, общаемся в Telegram, любит короткие сообщения.</code>\n"
        "<code>/learn По понедельникам утром у меня недельное планирование, не назначать встречи до 11:00.</code>\n"
        "3. /knowledge — последние добавленные заметки.\n"
        "4. /reset — сбросить историю текущего чата (база знаний останется).\n"
        "5. /forget — полностью очистить базу знаний.\n\n"
        "Чтобы ассистент отвечал только тебе, задай переменную окружения <code>ASSISTANT_OWNER_ID</code> с твоим Telegram ID."
    )


async def learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("Обучать меня может только владелец бота.")
        return

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
    await reply_html(update, "<b>Запомнил.</b> Буду учитывать это в следующих ответах.")


async def knowledge(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    items = read_json(KNOWLEDGE_FILE, [])
    if not items:
        await reply_html(update, "База знаний пока пустая. Добавь первое правило через /learn.")
        return

    recent = items[-10:]
    lines = [f"{i}. {escape(item['text'])}" for i, item in enumerate(recent, start=1)]
    await reply_html(update, "<b>Последние знания</b>\n\n" + "\n".join(lines))


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    save_chat_history(update.effective_chat.id, [])
    await reply_html(update, "Историю этого чата сбросил. Базу знаний не трогал.")


async def forget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("Чистить базу знаний может только владелец.")
        return
    write_json(KNOWLEDGE_FILE, [])
    await reply_html(update, "Базу знаний полностью очистил. Историю чатов не трогал — для этого /reset.")


async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat:
        return

    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id
    history = get_chat_history(chat_id)

    messages = history + [{"role": "user", "content": user_text}]
    system = (
        f"{SYSTEM_PROMPT}\n\n"
        f"База знаний, добавленная владельцем:\n{build_knowledge_context()}"
    )

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1600,
            system=system,
            messages=messages,
        )
        answer = response.content[0].text
    except Exception as exc:
        await update.message.reply_text(f"Не смог получить ответ от модели: {exc}")
        return

    save_chat_history(chat_id, messages + [{"role": "assistant", "content": answer}])
    await reply_html(update, answer)


def main() -> None:
    ensure_data_files()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("learn", learn))
    app.add_handler(CommandHandler("knowledge", knowledge))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()
