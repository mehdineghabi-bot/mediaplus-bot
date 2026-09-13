import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


TOKEN = os.getenv("BOT_TOKEN")


# پورت وب برای Render
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"MediaPlus Bot is running")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


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
    # اجرای وب‌سرور در پس‌زمینه
    threading.Thread(target=start_web_server, daemon=True).start()

    # اجرای ربات تلگرام
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(movies, pattern="^movies$"))

    app.run_polling()


if __name__ == "__main__":
    main()
