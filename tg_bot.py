import os
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

ANTHROPIC_KEY = os.environ["ANTHROPIC_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

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
