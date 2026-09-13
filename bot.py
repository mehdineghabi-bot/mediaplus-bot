import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = os.getenv("BOT_TOKEN")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🎬 فیلم و سریال رایگان", callback_data="movies")]
    ]

    await update.message.reply_text(
        "🎬 به MediaPlus خوش آمدید\n\n"
        "برای ورود به بخش فیلم و سریال روی دکمه زیر بزنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def movies(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.message.reply_text(
        "🎬 بخش فیلم و سریال\n\n"
        "به‌زودی محتوای این بخش اضافه می‌شود."
    )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(movies, pattern="^movies$"))

    app.run_polling()


if __name__ == "__main__":
    main()
