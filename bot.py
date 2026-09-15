async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    print("START COMMAND RECEIVED")

    user = update.effective_user

    if not user:
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

                    count = get_referral_count(inviter_id)

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
                        print("Referral notification error:", e)

            except ValueError:
                print("Invalid referral code")


    keyboard = [
        [
            InlineKeyboardButton(
                "🎬 فیلم و سریال",
                callback_data="movies"
            )
        ]
    ]


    await update.message.reply_photo(

        photo=(
            "AgACAgQAAxkBAAEimsRqqaTt4IukvsXJwnan4QZER5L0_QACdhBrGwSdUFGeibrtBkrKuQEAAwIAA3kAAz0E"
        ),

        caption=(
            "🌹 به مدیا پلاس خوش آمدید\n\n"
            "اینجا دنیایی از فیلم، سریال، اخبار و خدمات متنوع منتظر شماست.\n\n"
            "با ما همراه باشید و تجربه‌ای متفاوت از محتوا را داشته باشید 🎬✨\n\n"
            "برای ورود به بخش فیلم و سریال روی دکمه زیر بزنید:"
        ),

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


    print("START RESPONSE SENT")
