```python
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# کانال خصوصی محتوای MediaPlus
CONTENT_CHANNEL_ID = -1004485551897

# نام ربات
BOT_USERNAME = "Mediapluscenterbot"


# =========================
# Render Health Server
# =========================

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


# =========================
# Database
# =========================

def db_connection():
    return psycopg.connect(DATABASE_URL)


def init_database():

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    invite_link TEXT,
                    verified BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id SERIAL PRIMARY KEY,
                    inviter_id BIGINT NOT NULL,
                    referred_id BIGINT NOT NULL UNIQUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    message_id BIGINT UNIQUE NOT NULL,
                    title TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        conn.commit()


def save_user(user_id, username, first_name):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO users (
                    user_id,
                    username,
                    first_name
                )
                VALUES (%s, %s, %s)

                ON CONFLICT (user_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name
            """, (
                user_id,
                username,
                first_name
            ))

        conn.commit()


def get_user(user_id):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    user_id,
                    invite_link,
                    verified
                FROM users
                WHERE user_id = %s
            """, (user_id,))

            return cur.fetchone()


def set_invite_link(user_id, invite_link):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET invite_link = %s
                WHERE user_id = %s
            """, (
                invite_link,
                user_id
            ))

        conn.commit()


def add_referral(inviter_id, referred_id):

    # جلوگیری از دعوت خود شخص
    if inviter_id == referred_id:
        return False

    with db_connection() as conn:
        with conn.cursor() as cur:

            # آیا این شخص قبلاً توسط شخص دیگری دعوت شده؟
            cur.execute("""
                SELECT 1
                FROM referrals
                WHERE referred_id = %s
            """, (referred_id,))

            if cur.fetchone():
                return False

            # آیا دعوت‌کننده وجود دارد؟
            cur.execute("""
                SELECT 1
                FROM users
                WHERE user_id = %s
            """, (inviter_id,))

            if not cur.fetchone():
                return False

            # ثبت دعوت
            cur.execute("""
                INSERT INTO referrals (
                    inviter_id,
                    referred_id
                )
                VALUES (%s, %s)
            """, (
                inviter_id,
                referred_id
            ))

            # تعداد دعوت‌ها
            cur.execute("""
                SELECT COUNT(*)
                FROM referrals
                WHERE inviter_id = %s
            """, (inviter_id,))

            count = cur.fetchone()[0]

            # فعال کردن دسترسی بعد از 5 دعوت
            if count >= 5:

                cur.execute("""
                    UPDATE users
                    SET verified = TRUE
                    WHERE user_id = %s
                """, (inviter_id,))

        conn.commit()

    return True


def get_referral_count(user_id):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT COUNT(*)
                FROM referrals
                WHERE inviter_id = %s
            """, (user_id,))

            return cur.fetchone()[0]


def save_content(message_id, title):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO contents (
                    message_id,
                    title
                )
                VALUES (%s, %s)

                ON CONFLICT (message_id)
                DO NOTHING
            """, (
                message_id,
                title
            ))

        conn.commit()


def get_contents():

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    message_id,
                    title
                FROM contents
                ORDER BY id DESC
                LIMIT 50
            """)

            return cur.fetchall()


def get_content_message_id(content_id):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT message_id
                FROM contents
                WHERE id = %s
            """, (content_id,))

            row = cur.fetchone()

            return row[0] if row else None


# =========================
# Start + Referral
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if not user:
        return

    # ذخیره کاربر
    save_user(
        user.id,
        user.username,
        user.first_name
    )

    # بررسی لینک دعوت
    if context.args:

        referral_code = context.args[0]

        if referral_code.startswith("ref_"):

            try:

                inviter_id = int(
                    referral_code.replace("ref_", "", 1)
                )

                # ثبت دعوت
                added = add_referral(
                    inviter_id,
                    user.id
                )

                if added:

                    count = get_referral_count(
                        inviter_id
                    )

                    try:

                        if count >= 5:

                            await context.bot.send_message(
                                chat_id=inviter_id,
                                text=(
                                    "🎉 تبریک!\n\n"
                                    "۵ دعوت موفق شما تکمیل شد.\n"
                                    "✅ دسترسی شما به بخش فیلم و سریال فعال شد."
                                )
                            )

                        else:

                            await context.bot.send_message(
                                chat_id=inviter_id,
                                text=(
                                    "✅ یک دعوت موفق ثبت شد.\n\n"
                                    f"👥 دعوت‌های موفق شما: {count} از 5"
                                )
                            )

                    except Exception as e:

                        print(
                            "Referral notification error:",
                            e
                        )

            except ValueError:

                print(
                    "Invalid referral code:",
                    referral_code
                )

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 فیلم و سریال رایگان",
                callback_data="movies"
            )
        ]
    ]

    await update.message.reply_text(

        "🎬 به MediaPlus خوش آمدید\n\n"
        "برای ورود به بخش فیلم و سریال روی دکمه زیر بزنید:",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# Movie Section
# =========================

async def movies(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        return

    verified = user[2]

    # =========================
    # کاربر تایید شده
    # =========================

    if verified:

        contents = get_contents()

        if not contents:

            await query.message.reply_text(
                "🎬 بخش فیلم و سریال\n\n"
                "هنوز محتوایی اضافه نشده است."
            )

            return

        keyboard = []

        for content_id, message_id, title in contents:

            keyboard.append([
                InlineKeyboardButton(
                    f"🎬 {title}",
                    callback_data=f"content_{content_id}"
                )
            ])

        await query.message.reply_text(

            "🎬 فیلم و سریال‌های موجود:",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

        return

    # =========================
    # ساخت لینک جدید دعوت به ربات
    # =========================

    invite_link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )

    # ذخیره لینک جدید
    set_invite_link(
        user_id,
        invite_link
    )

    count = get_referral_count(
        user_id
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "👥 دعوت دوستان",
                callback_data="invite"
            )
        ],
        [
            InlineKeyboardButton(
                "🔍 بررسی دعوت‌ها",
                callback_data="check_referrals"
            )
        ]
    ]

    await query.message.reply_text(

        "🔐 دسترسی فیلم و سریال فعال نیست.\n\n"

        "برای فعال شدن دسترسی، باید "
        "۵ نفر را از طریق لینک اختصاصی خودتان "
        "وارد ربات کنید.\n\n"

        f"👥 دعوت‌های موفق: {count} از 5\n\n"

        "بعد از تکمیل ۵ دعوت، بخش فیلم و سریال "
        "برای شما فعال می‌شود.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================
# Invite Button
# =========================

async def invite_friends(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        return

    # همیشه لینک جدید ربات ساخته می‌شود
    invite_link = (
        f"https://t.me/{BOT_USERNAME}"
        f"?start=ref_{user_id}"
    )

    set_invite_link(
        user_id,
        invite_link
    )

    count = get_referral_count(
        user_id
    )

    await query.message.reply_text(

        "👥 دعوت دوستان\n\n"

        f"تعداد دعوت‌های موفق شما: {count} از 5\n\n"

        "لینک اختصاصی شما:\n"
        f"{invite_link}\n\n"

        "📌 این لینک را برای دوستانتان ارسال کنید.\n"
        "دوست شما باید از طریق همین لینک وارد ربات شود "
        "و Start را بزند تا دعوت ثبت شود.",

    )


# =========================
# Check Referrals
# =========================

async def check_referrals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        return

    verified = user[2]

    count = get_referral_count(
        user_id
    )

    if verified:

        await query.message.reply_text(

            "🎉 دسترسی شما فعال است!\n\n"
            f"👥 دعوت‌های موفق: {count} از 5\n\n"
            "✅ اکنون می‌توانید فیلم و سریال‌ها را دریافت کنید."

        )

        return

    remaining = 5 - count

    await query.message.reply_text(

        "📊 وضعیت دعوت‌ها\n\n"

        f"👥 دعوت‌های موفق: {count} از 5\n"
        f"⏳ تعداد باقی‌مانده: {remaining}\n\n"

        "بعد از تکمیل ۵ دعوت، "
        "دسترسی فیلم و سریال فعال می‌شود."

    )


# =========================
# Content Button
# =========================

async def send_content(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user or not user[2]:

        await query.message.reply_text(

            "🔒 دسترسی شما هنوز فعال نشده است.\n\n"
            "ابتدا باید ۵ نفر را دعوت کنید."

        )

        return

    try:

        content_id = int(
            query.data.replace(
                "content_",
                ""
            )
        )

        message_id = get_content_message_id(
            content_id
        )

        if not message_id:

            await query.message.reply_text(
                "❌ این محتوا پیدا نشد."
            )

            return

        await context.bot.copy_message(

            chat_id=user_id,

            from_chat_id=CONTENT_CHANNEL_ID,

            message_id=message_id

        )

    except Exception as e:

        print(
            "Content error:",
            e
        )

        await query.message.reply_text(
            "❌ ارسال محتوا با مشکل مواجه شد."
        )


# =========================
# Channel New Content
# =========================

async def channel_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.channel_post

    if not message:
        return

    if message.chat.id != CONTENT_CHANNEL_ID:
        return

    title = "محتوای جدید"

    if message.caption:

        title = message.caption.split(
            "\n"
        )[0][:80]

    elif message.text:

        title = message.text.split(
            "\n"
        )[0][:80]

    save_content(
        message.message_id,
        title
    )

    print(
        f"New content saved: "
        f"{message.message_id} - {title}"
    )


# =========================
# Main
# =========================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set"
        )

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set"
        )

    # ساخت جدول‌ها
    init_database()

    # وب‌سرور Render
    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    # ربات
    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # /start
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # فیلم و سریال
    app.add_handler(
        CallbackQueryHandler(
            movies,
            pattern="^movies$"
        )
    )

    # دعوت دوستان
    app.add_handler(
        CallbackQueryHandler(
            invite_friends,
            pattern="^invite$"
        )
    )

    # بررسی دعوت‌ها
    app.add_handler(
        CallbackQueryHandler(
            check_referrals,
            pattern="^check_referrals$"
        )
    )

    # دریافت محتوا
    app.add_handler(
        CallbackQueryHandler(
            send_content,
            pattern="^content_[0-9]+$"
        )
    )

    # ثبت پست‌های جدید کانال
    app.add_handler(
        MessageHandler(
            filters.Chat(CONTENT_CHANNEL_ID)
            & filters.UpdateType.CHANNEL_POST,
            channel_post
        )
    )

    print(
        "MediaPlus Bot started..."
    )

    app.run_polling()


if __name__ == "__main__":
    main()
```
