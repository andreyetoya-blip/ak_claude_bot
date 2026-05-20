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
OWNER_ID = os.getenv("TAX_BOT_OWNER_ID")

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_HISTORY_MESSAGES = 12
MAX_KNOWLEDGE_ITEMS = 60

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge_base.json"
MEMORY_FILE = DATA_DIR / "chat_memory.json"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


SYSTEM_PROMPT = """
Ты персональный ИИ-ассистент по налогам в России.

Твоя задача:
- помогать пользователю разбираться в налогах РФ для физлиц, ИП, самозанятых и компаний;
- объяснять простым русским языком, но сохранять юридическую аккуратность;
- использовать знания, которым тебя обучил владелец через команду /learn;
- отделять уверенные факты от предположений;
- задавать уточняющие вопросы, если налоговый режим, год, регион, статус лица или сумма важны для ответа.

Правила безопасности:
- не выдавай себя за юриста, налогового консультанта или сотрудника ФНС;
- не обещай гарантированный правовой результат;
- если вопрос зависит от свежих изменений закона, прямо скажи, что норму нужно проверить по актуальной редакции НК РФ, письмам ФНС/Минфина или в личном кабинете ФНС;
- для рискованных решений предлагай свериться с профессиональным налоговым консультантом;
- не придумывай номера статей, писем, ставок и сроков, если их нет в контексте или ты не уверен.

Формат ответа:
- отвечай на русском;
- сначала дай короткий практический вывод;
- затем объясни логику;
- если нужны данные от пользователя, задай конкретные вопросы списком.
- используй только Telegram HTML-разметку: <b>жирный</b>, <i>курсив</i>, <u>подчеркивание</u>, <s>зачеркнутый</s>, <code>код</code>, <pre>блок кода</pre>, <blockquote>цитата</blockquote>;
- не используй Markdown-разметку: #, ##, **жирный**, __подчеркивание__, ```code```, ---;
- не используй HTML-теги h1, h2, h3, p, ul, ol, li, br, div, span;
- заголовки оформляй жирным текстом, например <b>Что важно</b>;
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
        "<b>Привет! Я - ИИ-ассистент по налогам в России</b>\n\n"
        "<b>Что я умею</b>\n\n"
        "Помогаю разобраться в налоговых вопросах для:\n\n"
        "- Физических лиц: НДФЛ, вычеты, декларации, продажа имущества\n"
        "- ИП: выбор режима, УСН, патент, страховые взносы\n"
        "- Самозанятых: НПД, лимиты, чеки, совмещение с другими статусами\n"
        "- Компаний: общие вопросы налогообложения, режимы\n\n"
        "<b>Как я отвечаю</b>\n\n"
        "1. <b>Сначала - практический вывод</b>: что делать\n"
        "2. <b>Потом - объяснение логики</b>: почему именно так\n"
        "3. <b>Задаю уточняющие вопросы</b>, если от деталей зависит ответ\n\n"
        "<blockquote>Важно: я не юрист и не налоговый консультант. Я помогаю разобраться и сориентироваться, "
        "но для ответственных решений рекомендую проверять актуальные нормы НК РФ или консультироваться со специалистом.</blockquote>\n\n"
        "<b>Задавайте вопрос - постараюсь помочь!</b>\n\n"
        "Команды: /help, /learn, /knowledge, /reset"
    )


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(
        update,
        "<b>Как со мной работать</b>\n\n"
        "1. Задавай налоговые вопросы обычным сообщением.\n"
        "2. Добавляй знания через /learn. Например:\n"
        "<code>/learn Для ИП на УСН важно отдельно проверять лимиты доходов за нужный год.</code>\n"
        "3. Смотри последние добавленные знания через /knowledge.\n"
        "4. Сбрасывай историю текущего чата через /reset.\n\n"
        "Для ограничения обучения только владельцем задай переменную <code>TAX_BOT_OWNER_ID</code>."
    )


async def learn(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        await update.message.reply_text("Обучать меня может только владелец бота.")
        return

    text = " ".join(ctx.args).strip()
    if not text:
        await reply_html(
            update,
            "Пришли знание после команды. Например:\n"
            "<code>/learn НДФЛ платят налоговые резиденты РФ...</code>",
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
    app.run_polling()


if __name__ == "__main__":
    main()
