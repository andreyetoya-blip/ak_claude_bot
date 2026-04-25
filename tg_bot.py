import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

ANTHROPIC_KEY = "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"
TELEGRAM_TOKEN = "8496424429:AAGFd-V0NoVPrLg9pDzITQXt1zwvzMptgLg"

client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

async def handle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": user_text}]
    )
    await update.message.reply_text(response.content[0].text)

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
app.run_polling()
