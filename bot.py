import os
import threading
import traceback
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

import psycopg
from telethon import TelegramClient
from telethon.sessions import StringSession
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
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

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION")

telegram_client = TelegramClient(
    StringSession(TELEGRAM_SESSION),
    API_ID,
    API_HASH
)

# کانال خصوصی محتوای MediaPlus
CONTENT_CHANNEL_ID = -1004485551897

# نام ربات
BOT_USERNAME = "Mediapluscenterbot"

# آیدی عددی ادمین برای دریافت درخواست فیلم
ADMIN_ID = 8093676883

# کاربرانی که در انتظار ارسال عنوان فیلم درخواستی هستند
pending_movie_requests = set()

# A private chat/channel used only to upload news photos through Bot API.
# Set this Render environment variable to a chat/channel where the bot has
# permission to send messages. If it is not set, news text still works.
NEWS_STORAGE_CHAT_ID = os.getenv("NEWS_STORAGE_CHAT_ID")

# Global Application reference used by the Telethon news worker.
bot_app = None


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
                    year TEXT,
                    genre TEXT,
                    rating TEXT,
                    duration TEXT,
                    description TEXT,
                    poster_file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS music (
                    id SERIAL PRIMARY KEY,
                    title TEXT,
                    artist TEXT,
                    poster_file_id TEXT,
                    download_url TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS news (
                    id SERIAL PRIMARY KEY,
                    source TEXT,
                    title TEXT,
                    text TEXT,
                    photo_file_id TEXT,
                    telegram_message_id BIGINT,
                    news_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cur.execute("""
                ALTER TABLE news
                ADD COLUMN IF NOT EXISTS telegram_message_id BIGINT
            """)

            cur.execute("""
                ALTER TABLE news
                DROP CONSTRAINT IF EXISTS news_source_title_key
            """)

            cur.execute("""
                DROP INDEX IF EXISTS news_source_message_unique
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS news_source_message_unique
                ON news(source, telegram_message_id)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS notification_settings (
                    user_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        
            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS year TEXT
            """)

            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS genre TEXT
            """)

            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS rating TEXT
            """)

            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS duration TEXT
            """)

            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS description TEXT
            """)

            cur.execute("""
                ALTER TABLE contents
                ADD COLUMN IF NOT EXISTS poster_file_id TEXT
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


def save_content(
    message_id,
    title,
    poster_file_id=None,
    year=None,
    genre=None,
    rating=None,
    duration=None,
    description=None
):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT 1
                FROM contents
                WHERE title = %s
            """, (title,))

            if cur.fetchone():
                return False

            cur.execute("""
                INSERT INTO contents (
                    message_id,
                    title,
                    poster_file_id,
                    year,
                    genre,
                    rating,
                    duration,
                    description
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)

                ON CONFLICT (message_id)
                DO UPDATE SET
                    title = EXCLUDED.title,
                    poster_file_id = EXCLUDED.poster_file_id,
                    year = EXCLUDED.year,
                    genre = EXCLUDED.genre,
                    rating = EXCLUDED.rating,
                    duration = EXCLUDED.duration,
                    description = EXCLUDED.description
            """, (
                message_id,
                title,
                poster_file_id,
                year,
                genre,
                rating,
                duration,
                description
            ))

        conn.commit()

    return True


def get_contents():

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    message_id,
                    title,
                    year,
                    genre,
                    rating,
                    duration,
                    description,
                    poster_file_id
                FROM contents
                ORDER BY id DESC
                LIMIT 50
            """)

            return cur.fetchall()

def save_music(title, artist, poster_file_id, download_url):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO music (
                    title,
                    artist,
                    poster_file_id,
                    download_url
                )
                VALUES (%s, %s, %s, %s)
            """, (
                title,
                artist,
                poster_file_id,
                download_url
            ))

        conn.commit()


def get_music():

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    title,
                    artist,
                    poster_file_id,
                    download_url
                FROM music
                ORDER BY id DESC
                LIMIT 50
            """)

            return cur.fetchall()


def delete_music(music_id):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM music
                WHERE id = %s
            """, (music_id,))

        conn.commit()


def get_music_by_id(music_id):

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    title,
                    artist,
                    poster_file_id,
                    download_url
                FROM music
                WHERE id = %s
            """, (music_id,))

        return cur.fetchone()

def clear_contents():

    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM contents
            """)

        conn.commit()


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


def save_news(
    source,
    title,
    text,
    photo_file_id=None,
    telegram_message_id=None
):

    with db_connection() as conn:
        with conn.cursor() as cur:

            if telegram_message_id is not None:
                cur.execute("""
                    INSERT INTO news
                    (
                        source,
                        title,
                        text,
                        photo_file_id,
                        telegram_message_id
                    )
                    VALUES (%s,%s,%s,%s,%s)
                    ON CONFLICT (source, telegram_message_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        text = EXCLUDED.text,
                        photo_file_id = COALESCE(
                            EXCLUDED.photo_file_id,
                            news.photo_file_id
                        )
                """, (
                    source,
                    title,
                    text,
                    photo_file_id,
                    telegram_message_id
                ))
            else:
                cur.execute("""
                    INSERT INTO news
                    (
                        source,
                        title,
                        text,
                        photo_file_id
                    )
                    VALUES (%s,%s,%s,%s)
                """, (
                    source,
                    title,
                    text,
                    photo_file_id
                ))

        conn.commit()



def get_news_photo_file_id(source, telegram_message_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT photo_file_id
                FROM news
                WHERE source = %s
                  AND telegram_message_id = %s
                LIMIT 1
            """, (source, telegram_message_id))

            row = cur.fetchone()
            return row[0] if row and row[0] else None


def get_latest_news():
    
    with db_connection() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT
                    id,
                    source,
                    title,
                    text,
                    photo_file_id
                FROM news
                ORDER BY id DESC
                LIMIT 10
            """)

            return cur.fetchall()


# =========================
# Notifications
# =========================

def set_notification_status(user_id, enabled):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notification_settings (user_id, enabled)
                VALUES (%s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, enabled))
        conn.commit()


def get_notification_status(user_id):
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT enabled
                FROM notification_settings
                WHERE user_id = %s
            """, (user_id,))
            row = cur.fetchone()
            return row[0] if row else False


def get_notification_users():
    with db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id
                FROM notification_settings
                WHERE enabled = TRUE
            """)
            return [row[0] for row in cur.fetchall()]


async def notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass

    user_id = query.from_user.id
    enabled = get_notification_status(user_id)

    keyboard = [
        [InlineKeyboardButton("🔔 فعال کردن اعلان‌ها", callback_data="notifications_on")],
        [InlineKeyboardButton("🔕 غیرفعال کردن اعلان‌ها", callback_data="notifications_off")]
    ]

    status = "🟢 اعلان‌ها برای شما فعال است." if enabled else "🔴 اعلان‌ها برای شما غیرفعال است."

    await query.message.reply_text(
        "🔔 اطلاع‌رسانی مدیا پلاس\n\n"
        "با فعال کردن اعلان‌ها، هر زمان محتوای جدیدی در مدیا پلاس منتشر شود، فقط یک پیام کوتاه برای شما ارسال خواهد شد.\n\n"
        f"{status}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def notifications_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass
    set_notification_status(query.from_user.id, True)
    await query.message.reply_text(
        "🔔 اعلان‌های مدیا پلاس برای شما فعال شد.\n\n"
        "از این به بعد با انتشار محتوای جدید، یک پیام کوتاه برای شما ارسال می‌شود."
    )


async def notifications_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass
    set_notification_status(query.from_user.id, False)
    await query.message.reply_text(
        "🔕 اعلان‌های مدیا پلاس برای شما غیرفعال شد.\n\n"
        "هر زمان بخواهید می‌توانید دوباره آن را فعال کنید."
    )


async def notify_new_content(context: ContextTypes.DEFAULT_TYPE):
    notification_text = (
        "🔔 محتوای جدید در مدیا پلاس منتشر شد!\n\n\n"
        "برای مشاهده وارد شوید 👇"
    )
    keyboard = [[InlineKeyboardButton("🏠 ورود به منوی اصلی", callback_data="main_menu")]]

    for user_id in get_notification_users():
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=notification_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            print(f"Notification error for user {user_id}: {e}")


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass

    keyboard = get_main_keyboard()
    welcome_text = (
        "🌹 به مدیا پلاس خوش آمدید\n\n"
        "اینجا دنیایی از فیلم، سریال، اخبار و خدمات متنوع منتظر شماست.\n\n"
        "با ما همراه باشید و تجربه‌ای متفاوت از محتوا را داشته باشید 🎬✨"
    )
    welcome_photo_file_id = os.getenv("WELCOME_PHOTO_FILE_ID")
    if welcome_photo_file_id:
        try:
            await query.message.reply_photo(photo=welcome_photo_file_id, caption=welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return
        except BadRequest as e:
            print(f"Welcome photo error in main menu: {e}")
    await query.message.reply_text(welcome_text, reply_markup=InlineKeyboardMarkup(keyboard))




def get_back_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
    ])

def get_main_keyboard():
    return [
        [InlineKeyboardButton("🎬 فیلم و سریال", callback_data="movies")],
        [InlineKeyboardButton("📰 آخرین اخبار", callback_data="news_menu")],
        [InlineKeyboardButton("₿ دنیای ارز دیجیتال", callback_data="coming_soon")],
        [InlineKeyboardButton("🎵 موسیقی", callback_data="coming_soon")],
        [InlineKeyboardButton("❤️ عاشقانه‌ها", callback_data="coming_soon")],
        [InlineKeyboardButton("🔔 منو باخبر کن", callback_data="notifications")]
    ]


async def news_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except BadRequest:
        pass
    keyboard = [
        [InlineKeyboardButton("📰 آخرین خبر", url="https://t.me/akharinkhabar"), InlineKeyboardButton("🇮🇷 ایران نیوز", url="https://t.me/IranNews")],
        [InlineKeyboardButton("🚨 اخبار فوری جنگ", url="https://t.me/M0_HM")],
        [InlineKeyboardButton("📺 BBC فارسی", url="https://t.me/bbcpersian"), InlineKeyboardButton("📡 ایران اینترنشنال", url="https://t.me/IranintlTV")],
        [InlineKeyboardButton("💰 نرخ طلا و ارز 1", url="https://t.me/NerkhTv1"), InlineKeyboardButton("💵 نرخ طلا و ارز 2", url="https://t.me/DO_L4")]
    ]
    keyboard.append([InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")])
    await query.message.reply_text("📰 منابع خبری مدیا پلاس\n\nمنبع مورد نظر خود را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))


# =========================
# Telethon news removed - only news links menu is used
# =========================


# =========================
# Start + Referral
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    print("START COMMAND RECEIVED")

    user = update.effective_user

    if not user:
        print("START ERROR: No effective user")
        return

    print(
        f"START FROM USER: {user.id} "
        f"@{user.username or 'no_username'}"
    )

    # ذخیره کاربر
    save_user(
        user.id,
        user.username,
        user.first_name
    )

    # بررسی لینک دعوت
    if context.args:

        referral_code = context.args[0]

        print(
            f"REFERRAL CODE RECEIVED: {referral_code}"
        )

        if referral_code.startswith("ref_"):

            try:

                inviter_id = int(
                    referral_code.replace("ref_", "", 1)
                )

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

    keyboard = get_main_keyboard()

    welcome_text = (
        "🌹 به مدیا پلاس خوش آمدید\n\n"
        "اینجا دنیایی از فیلم، سریال، اخبار و خدمات متنوع منتظر شماست.\n\n"
        "با ما همراه باشید و تجربه‌ای متفاوت از محتوا را داشته باشید 🎬✨"
    )

    welcome_photo_file_id = os.getenv("WELCOME_PHOTO_FILE_ID")

    if welcome_photo_file_id:
        try:
            await update.message.reply_photo(
                photo=welcome_photo_file_id,
                caption=welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except BadRequest as photo_error:
            print(f"Welcome photo error: {photo_error}")
            await update.message.reply_text(
                text=welcome_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await update.message.reply_text(
            text=welcome_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    print("START RESPONSE SENT")


async def coming_soon(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass

    await query.message.reply_text(
        "⏳ این بخش به‌زودی فعال می‌گردد...",
        reply_markup=get_back_menu_keyboard()
    )


async def latest_news(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass


    news_list = get_latest_news()


    if not news_list:

        await query.message.reply_text(
            "📰 هنوز خبری دریافت نشده است."
        )

        return


    for (
        news_id,
        source,
        title,
        text,
        photo_file_id
    ) in news_list:


        caption = (
            f"📰 {title}\n\n"
            f"{text}\n\n"
            f"📌 منبع: {source}"
        )


        if photo_file_id:
            try:
                await query.message.reply_photo(
                    photo=photo_file_id,
                    caption=caption
                )
                continue
            except BadRequest as photo_error:
                print(
                    f"Invalid news photo file_id for news {news_id}: "
                    f"{photo_error}"
                )

        await query.message.reply_text(
            caption
        )


# =========================
# Movie Section
# =========================

async def movies(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass

    user_id = query.from_user.id

    print(f"MOVIES BUTTON CLICKED BY USER: {user_id}")

    user = get_user(user_id)

    if not user:
        return

    contents = get_contents()

    if not contents:
        keyboard = [
            [InlineKeyboardButton("🎥 فیلم درخواستی", callback_data="movie_request")],
            [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
        ]

        await query.message.reply_text(
            "🎬 بخش فیلم و سریال\n\n"
            "هنوز محتوایی اضافه نشده است.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # نمایش اولین فیلم
    await show_movie_page(
        query.message,
        contents,
        0
    )


async def show_movie_page(message, contents, index):

    if index < 0:
        index = len(contents) - 1

    if index >= len(contents):
        index = 0

    (
        content_id,
        message_id,
        title,
        year,
        genre,
        rating,
        duration,
        description,
        poster_file_id
    ) = contents[index]

    caption = (
        f"🎬 {title}\n\n"
        f"📅 سال: {year or 'نامشخص'}\n"
        f"🎭 ژانر: {genre or 'نامشخص'}\n"
        f"⭐ امتیاز: {rating or 'نامشخص'}\n"
        f"⏱ مدت: {duration or 'نامشخص'}\n\n"
        f"📝 خلاصه:\n"
        f"{description or 'بدون توضیحات'}\n\n"
        f"📄 صفحه {index + 1} از {len(contents)}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "⬇️ دانلود",
                callback_data=f"content_{content_id}"
            )
        ],
        [
           InlineKeyboardButton(
               "بعدی ◀️",
               callback_data=f"movie_page_{index + 1}"
           ),
           InlineKeyboardButton(
               "▶️ قبلی",
               callback_data=f"movie_page_{index - 1}"
           )
        ],
        [
            InlineKeyboardButton(
                "🎥 فیلم درخواستی",
                callback_data="movie_request"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 منوی اصلی",
                callback_data="main_menu"
            )
        ]
    ]

    markup = InlineKeyboardMarkup(keyboard)

    if poster_file_id:

        try:
            await message.reply_photo(
                photo=poster_file_id,
                caption=caption,
                reply_markup=markup
            )
        except BadRequest:
            await message.reply_text(
                caption,
                reply_markup=markup
            )

    else:

        await message.reply_text(
            caption,
            reply_markup=markup
        )


async def movie_page(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass

    try:
        index = int(
            query.data.replace(
                "movie_page_",
                ""
            )
        )
    except ValueError:
        return

    contents = get_contents()

    if not contents:
        await query.message.reply_text(
            "🎬 محتوایی برای نمایش وجود ندارد.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
            ])
        )
        return

    if index < 0:
        index = len(contents) - 1

    if index >= len(contents):
        index = 0

    (
        content_id,
        message_id,
        title,
        year,
        genre,
        rating,
        duration,
        description,
        poster_file_id
    ) = contents[index]

    caption = (
        f"🎬 {title}\n\n"
        f"📅 سال: {year or 'نامشخص'}\n"
        f"🎭 ژانر: {genre or 'نامشخص'}\n"
        f"⭐ امتیاز: {rating or 'نامشخص'}\n"
        f"⏱ مدت: {duration or 'نامشخص'}\n\n"
        f"📝 خلاصه:\n"
        f"{description or 'بدون توضیحات'}\n\n"
        f"📄 صفحه {index + 1} از {len(contents)}"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "⬇️ دانلود",
                callback_data=f"content_{content_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "بعدی ◀️",
                callback_data=f"movie_page_{index + 1}"
            ),
            InlineKeyboardButton(
                "▶️ قبلی",
                callback_data=f"movie_page_{index - 1}"
            )
        ],
        [
            InlineKeyboardButton(
                "🎥 فیلم درخواستی",
                callback_data="movie_request"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 منوی اصلی",
                callback_data="main_menu"
            )
        ]
    ]

    markup = InlineKeyboardMarkup(keyboard)

    try:
        await query.message.delete()
    except Exception:
        pass

    if poster_file_id:

        try:
            await query.message.reply_photo(
                photo=poster_file_id,
                caption=caption,
                reply_markup=markup
            )
        except BadRequest:
            await query.message.reply_text(
                caption,
                reply_markup=markup
            )

    else:

        await query.message.reply_text(
            caption,
            reply_markup=markup
        )


# =========================
# Movie Request
# =========================

async def movie_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    try:
        await query.answer()
    except BadRequest:
        pass

    user_id = query.from_user.id
    pending_movie_requests.add(user_id)

    await query.message.reply_text(
        "🎥 فیلم درخواستی\n\n"
        "چنانچه فیلم مورد نظر شما در کانال موجود نیست،\n"
        "عنوان فیلم را همینجا ارسال کنید تا در کوتاه‌ترین زمان ممکن بررسی و برای شما ارسال شود.\n\n"
        "✍️ لطفاً فقط نام فیلم یا سریال مورد نظر را ارسال کنید."
    )


async def receive_movie_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or user.id not in pending_movie_requests:
        return

    movie_title = (update.message.text or "").strip()

    if not movie_title:
        await update.message.reply_text("❌ لطفاً عنوان فیلم یا سریال را به صورت متنی ارسال کنید.")
        return

    pending_movie_requests.discard(user.id)

    username = f"@{user.username}" if user.username else "ندارد"
    first_name = user.first_name or "بدون نام"

    admin_text = (
        "📥 درخواست جدید فیلم\n\n"
        f"👤 نام کاربر: {first_name}\n"
        f"🔹 username: {username}\n"
        f"🆔 شناسه کاربر: {user.id}\n\n"
        f"🎬 عنوان درخواست: {movie_title}"
    )

    try:
        user_link = f"tg://user?id={user.id}"
        keyboard = [[InlineKeyboardButton("👤 باز کردن پروفایل کاربر", url=user_link)]]

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        await update.message.reply_text(
            "✅ درخواست شما دریافت شد.\n\n"
            "عنوان مورد نظر برای بررسی ارسال شد و در کوتاه‌ترین زمان ممکن پیگیری می‌شود. 🎬"
        )
    except Exception as e:
        print(f"Movie request error: {e}")
        pending_movie_requests.add(user.id)
        await update.message.reply_text(
            "❌ در ارسال درخواست مشکلی پیش آمد. لطفاً چند لحظه بعد دوباره تلاش کنید."
        )


# =========================
# Invite Button
# =========================

async def invite_friends(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest as e:
        print("Old/invalid callback query in invite:", e)

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user:
        return

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
        "و Start را بزند تا دعوت ثبت شود."

    )


# =========================
# Check Referrals
# =========================

async def check_referrals(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:
        await query.answer()
    except BadRequest as e:
        print("Old/invalid callback query in check:", e)

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

    try:
        await query.answer()
    except BadRequest as e:
        print("Old/invalid callback query in content:", e)

    user_id = query.from_user.id

    user = get_user(user_id)

    if not user or not user[2]:

        invite_link = (
            f"https://t.me/{BOT_USERNAME}"
            f"?start=ref_{user_id}"
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

            "🔒 دانلود این فیلم هنوز فعال نیست.\n\n"

            f"👥 دعوت‌های موفق شما: {count} از 5\n\n"

            "برای فعال شدن دانلود همه فیلم‌ها، "
            "۵ نفر را با لینک اختصاصی خود دعوت کنید:\n\n"

            f"{invite_link}",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
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
    year = None
    genre = None
    rating = None
    duration = None
    description = None
    poster_file_id = None


    # گرفتن پوستر
    if message.photo:

        poster_file_id = message.photo[-1].file_id


    # خواندن کپشن
    text = ""

    if message.caption:
        text = message.caption

    elif message.text:
        text = message.text


    if text:

        lines = text.split("\n")

        # عنوان = خط اول
        title = lines[0].strip()[:80]


        # استخراج اطلاعات هوشمند
        for line in lines:

            line = line.strip()

            clean_line = (
                line
                .replace("📅", "")
                .replace("🎭", "")
                .replace("⭐", "")
                .replace("⏱", "")
                .strip()
            )


            if clean_line.startswith("سال"):

                year = (
                    clean_line
                    .replace("سال ساخت:", "")
                    .replace("سال:", "")
                    .replace("سال :", "")
                    .strip()
                )


            elif clean_line.startswith("ژانر"):

                genre = (
                    clean_line
                    .replace("ژانر:", "")
                    .replace("ژانر :", "")
                    .strip()
                )


            elif clean_line.startswith("امتیاز"):

                rating = (
                    clean_line
                    .replace("امتیاز:", "")
                    .replace("امتیاز :", "")
                    .strip()
                )


            elif clean_line.startswith("مدت"):

                duration = (
                    clean_line
                    .replace("مدت:", "")
                    .replace("مدت :", "")
                    .strip()
                )


        # استخراج فقط خلاصه داستان
        summary_lines = []

        in_summary = False

        for line in lines:

            line = line.strip()

            if line.startswith("خلاصه داستان"):

                in_summary = True
    
                text_after = (
                    line
                    .replace("خلاصه داستان:", "")
                    .replace("خلاصه داستان :", "")
                    .strip()
                )

                if text_after:
                    summary_lines.append(text_after)

                continue


            if in_summary and line:

                summary_lines.append(line)


        description = "\n".join(
            summary_lines
        ).strip() or None


    is_new_content = save_content(
        message.message_id,
        title,
        poster_file_id,
        year,
        genre,
        rating,
        duration,
        description
    )

    if is_new_content:
        await notify_new_content(context)

    print(
        f"New content saved: "
        f"{message.message_id} - {title}"
    )

# =========================
# Error Handler
# =========================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    print("========== BOT ERROR ==========")
    print("Update:", update)
    print("Error:", context.error)

    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__
    )

    print("================================")


# Telethon Runner removed


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

    print("Initializing database...")

    init_database()

    print("Database initialized.")
    
    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    print("Render health server started.")

    global bot_app

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    bot_app = app


    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            movies,
            pattern="^movies$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            movie_page,
            pattern="^movie_page_-?[0-9]+$"
        )
    )
    
    app.add_handler(
        CallbackQueryHandler(
            movie_request,
            pattern="^movie_request$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            latest_news,
            pattern="^latest_news$"
        )
    )

    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(news_menu, pattern="^news_menu$"))
    app.add_handler(CallbackQueryHandler(notifications, pattern="^notifications$"))
    app.add_handler(CallbackQueryHandler(notifications_on, pattern="^notifications_on$"))
    app.add_handler(CallbackQueryHandler(notifications_off, pattern="^notifications_off$"))
    
    app.add_handler(
        CallbackQueryHandler(
            coming_soon,
            pattern="^coming_soon$"
        )
    )
    
    app.add_handler(
        CallbackQueryHandler(
            invite_friends,
            pattern="^invite$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            check_referrals,
            pattern="^check_referrals$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            send_content,
            pattern="^content_[0-9]+$"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            receive_movie_request
        )
    )

    app.add_handler(
        MessageHandler(
            filters.Chat(CONTENT_CHANNEL_ID)
            & filters.UpdateType.CHANNEL_POST,
            channel_post
        )
    )

    app.add_error_handler(
        error_handler
    )

    print("MediaPlus Bot started...")
    print("Starting Telegram polling...")

    
    app.run_polling(
        drop_pending_updates=False
    )


if __name__ == "__main__":
    main()
