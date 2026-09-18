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

# A private chat/channel used only to upload news photos through Bot API.
# Set this Render environment variable to a chat/channel where the bot has
# permission to send messages. If it is not set, news text still works.
NEWS_STORAGE_CHAT_ID = os.getenv("NEWS_STORAGE_CHAT_ID")


async def print_storage_channel_id():
    try:
        print("=== TELEGRAM CHATS START ===")

        dialogs = await telegram_client.get_dialogs()

        for dialog in dialogs:
            print(f"NAME={dialog.name} | ID={dialog.id}")

        print("=== TELEGRAM CHATS END ===")

    except Exception as e:
        print(f"Storage channel ID error: {e}")


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
                return

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
# Telethon - دریافت اخبار
# =========================

NEWS_CHANNELS = [
    "akharinkhabar"
]


async def fetch_news_from_telegram():
    try:
        if not telegram_client.is_connected():
            await telegram_client.connect()

        if not await telegram_client.is_user_authorized():
            print("Telethon: User is not authorized.")
            return

        if not NEWS_STORAGE_CHAT_ID:
            print(
                "Telethon: NEWS_STORAGE_CHAT_ID is not set; "
                "news photos will not be stored."
            )

        for channel in NEWS_CHANNELS:

            messages = await telegram_client.get_messages(
                channel,
                limit=20
            )

            for message in messages:

                if not message or not message.message:
                    continue

                news_text = message.message.strip()

                lines = news_text.split("\n", 1)

                title = lines[0][:250].strip()

                body = (
                    lines[1].strip()
                    if len(lines) > 1
                    else news_text
                )

                photo_file_id = get_news_photo_file_id(
                    channel,
                    message.id
                )

                # Upload each news photo only once and reuse the Bot API
                # file_id on later 5-minute polling cycles.
                if (
                    not photo_file_id
                    and message.photo
                    and NEWS_STORAGE_CHAT_ID
                    and bot_app
                ):
                    try:
                        photo_bytes = await telegram_client.download_media(
                            message,
                            file=bytes
                        )

                        if photo_bytes:
                            sent = await bot_app.bot.send_photo(
                                chat_id=NEWS_STORAGE_CHAT_ID,
                                photo=photo_bytes
                            )
                            if sent.photo:
                                photo_file_id = sent.photo[-1].file_id

                    except Exception as photo_error:
                        print(
                            f"News photo error for message "
                            f"{message.id}: {photo_error}"
                        )

                save_news(
                    source=channel,
                    title=title,
                    text=body,
                    photo_file_id=photo_file_id,
                    telegram_message_id=message.id
                )

                print(
                    f"News checked: {channel} / message_id={message.id} / "
                    f"title={title[:80]}"
                )

        print("Telethon: News fetched successfully.")

    except Exception as e:
        print(f"Telethon news error: {e}")


async def news_fetch_loop():
    while True:
        try:
            await fetch_news_from_telegram()
        except Exception as e:
            print(f"News loop error: {e}")

        # Check for new posts every 5 minutes.
        await asyncio.sleep(300)


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

    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 فیلم و سریال",
                callback_data="movies"
            )
        ],
        [
            InlineKeyboardButton(
                "📰 آخرین اخبار",
                callback_data="latest_news"
            ),
            InlineKeyboardButton(
                "🚨 اخبار جنگ",
                url="https://t.me/M0_HM"
            )
        ], 
        [
            InlineKeyboardButton(
                "💱 نرخ ارز و طلا",
                url="https://t.me/NerkhTv1"
            )
        ],
        [
            InlineKeyboardButton(
                "₿ دنیای ارز دیجیتال",
                callback_data="coming_soon"
            )
        ],
        [
            InlineKeyboardButton(
                "🎵 موسیقی",
                callback_data="coming_soon"
            )
        ],
        [
            InlineKeyboardButton(
                "❤️ عاشقانه‌ها",
                callback_data="coming_soon"
            )
        ]
    ]

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
        "⏳ این بخش به‌زودی فعال می‌گردد..."
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
    except BadRequest as e:
        print("Old/invalid callback query in movies:", e)

    user_id = query.from_user.id

    print(
        f"MOVIES BUTTON CLICKED BY USER: {user_id}"
    )

    user = get_user(user_id)

    if not user:
        return

    contents = get_contents()

    if not contents:

        await query.message.reply_text(
            "🎬 بخش فیلم و سریال\n\n"
            "هنوز محتوایی اضافه نشده است."
        )

        return


    for (
        content_id,
        message_id,
        title,
        year,
        genre,
        rating,
        duration,
        description,
        poster_file_id
    ) in contents:


        caption = (
            f"🎬 {title}\n\n"
            f"📅 سال: {year or 'نامشخص'}\n"
            f"🎭 ژانر: {genre or 'نامشخص'}\n"
            f"⭐ امتیاز: {rating or 'نامشخص'}\n"
            f"⏱ مدت: {duration or 'نامشخص'}\n\n"
            f"📝 خلاصه:\n"
            f"{description or 'بدون توضیحات'}"
        )


        keyboard = [
            [
                InlineKeyboardButton(
                    "⬇️ دانلود",
                    callback_data=f"content_{content_id}"
                )
            ]
        ]


        if poster_file_id:

            await query.message.reply_photo(
                photo=poster_file_id,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        else:

            await query.message.reply_text(
                caption,
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


    save_content(
        message.message_id,
        title,
        poster_file_id,
        year,
        genre,
        rating,
        duration,
        description
    )


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


# =========================
# Telethon Runner
# =========================

def run_telethon():
    async def runner():
        try:
            await telegram_client.start()

            await print_storage_channel_id()
            
            print("Telethon connected successfully.")

            await fetch_news_from_telegram()

            await news_fetch_loop()

        except Exception as e:
            print(f"Telethon runner error: {e}")

    asyncio.run(runner())


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

    threading.Thread(
        target=run_telethon,
        daemon=True
    ).start()

    print("Telethon runner started.")

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
            latest_news,
            pattern="^latest_news$"
        )
    )
    
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
