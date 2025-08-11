#!/usr/bin/env python3
"""
Minimal Registration & Admin Approval Telegram Bot
- Uses python-telegram-bot (v20+) style Application
- Uses psycopg (psycopg3) for PostgreSQL
- Flask keep-alive endpoint for hosting (optional)
- Stripped of referrals, coupons, balance, tasks, reminders, broadcasts, coach apply, etc.
- Admin approves payment screenshots and finalizes user credentials.
"""

import os
import logging
import datetime
import secrets
from threading import Thread

from flask import Flask
import psycopg  # psycopg (psycopg3)
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ----------------------------
# Configuration / Environment
# ----------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DATABASE_URL = os.getenv("DATABASE_URL")

if not BOT_TOKEN or not ADMIN_ID or not DATABASE_URL:
    raise RuntimeError("Please set BOT_TOKEN, ADMIN_ID and DATABASE_URL environment variables.")

# ----------------------------
# Flask keep-alive (optional)
# ----------------------------
app = Flask("keepalive")


@app.route("/")
def home():
    return "Bot is alive!"


def run_flask():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080)))


def keep_alive():
    t = Thread(target=run_flask, daemon=True)
    t.start()


# ----------------------------
# Logging
# ----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ----------------------------
# Database Setup
# ----------------------------
try:
    # Connect to Postgres using psycopg3
    # DATABASE_URL expected in the form: postgres://user:pass@host:port/dbname
    conn = psycopg.connect(DATABASE_URL, autocommit=False)
    cursor = conn.cursor()

    # Users table (minimal)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            chat_id BIGINT PRIMARY KEY,
            username TEXT,
            name TEXT,
            email TEXT,
            phone TEXT,
            package TEXT,
            payment_status TEXT DEFAULT 'new', -- new, pending_payment, pending_approval, approved, registered
            approved_at TIMESTAMP,
            registration_date TIMESTAMP
        )
        """
    )

    # Payments table (stores screenshot/payment attempts)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT,
            package TEXT,
            payment_account TEXT,
            status TEXT DEFAULT 'pending', -- pending, approved, rejected
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_at TIMESTAMP
        )
        """
    )
    conn.commit()
except Exception as e:
    logger.exception("Database connection/setup failed.")
    raise

# ----------------------------
# In-memory state
# ----------------------------
# used for simple state transitions (admin pending credential input, user expecting screenshot)
user_state = {}

# ----------------------------
# Predefined payment accounts (edit as needed)
# ----------------------------
PAYMENT_ACCOUNTS = {
    "Bank A": "Account: 1234567890\nBank: Example Bank A\nName: Your Name",
    "Bank B": "Account: 0987654321\nBank: Example Bank B\nName: Your Name",
}

PACKAGE_PRICES_NGN = {
    "Standard": 9000,
    "X": 14000,
}

# ----------------------------
# Helper functions
# ----------------------------
def get_user_status(chat_id: int):
    try:
        cursor.execute("SELECT payment_status FROM users WHERE chat_id=%s", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        logger.exception("Error fetching user status")
        return None


def log_interaction(chat_id: int, action: str):
    logger.info("Interaction - %s: %s", chat_id, action)


# ----------------------------
# Command handlers
# ----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    log_interaction(chat_id, "start")
    # Insert minimal user record if not exists
    try:
        cursor.execute("SELECT 1 FROM users WHERE chat_id=%s", (chat_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (chat_id, username, name, payment_status) VALUES (%s, %s, %s, %s)",
                (chat_id, update.effective_user.username or "", update.effective_user.full_name or "", "new"),
            )
            conn.commit()
    except Exception:
        logger.exception("Error creating user row on start")

    keyboard = [
        [InlineKeyboardButton("🚀 Choose Package", callback_data="package_selector")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text(
        "Welcome! Choose a package to register.\n\nWe have:\n• Standard (₦9,000)\n• X (₦14,000) — gives access to special content.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def help_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton("How to Pay", callback_data="how_to_pay")],
        [InlineKeyboardButton("Registration Process", callback_data="registration_process")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
    ]
    await update.message.reply_text("Help topics:", reply_markup=InlineKeyboardMarkup(keyboard))


# Admin stats: counts + recent 10 users
async def adminstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is admin-only.")
        return
    try:
        cursor.execute("SELECT COUNT(*) FROM users")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE package=%s", ("Standard",))
        c_standard = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE package=%s", ("X",))
        c_x = cursor.fetchone()[0]
        cursor.execute(
            "SELECT chat_id, username, package, registration_date FROM users WHERE registration_date IS NOT NULL ORDER BY registration_date DESC LIMIT 10"
        )
        rows = cursor.fetchall()
        text_lines = [
            f"📊 Admin Stats:",
            f"• Total users: {total}",
            f"• Standard: {c_standard}",
            f"• X: {c_x}",
            "",
            "Last 10 registered users:",
        ]
        if rows:
            for r in rows:
                chat, uname, pkg, regdate = r
                regstr = regdate.strftime("%Y-%m-%d %H:%M") if regdate else "N/A"
                text_lines.append(f"• {uname or chat} — {pkg or 'N/A'} — {regstr}")
        else:
            text_lines.append("No registered users yet.")
        await update.message.reply_text("\n".join(text_lines))
    except Exception:
        logger.exception("Error generating admin stats")
        await update.message.reply_text("Error generating stats. Check logs.")


# ----------------------------
# Callback button handler
# ----------------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.from_user.id
    log_interaction(chat_id, f"button_{data}")

    try:
        if data == "menu":
            await show_main_menu(update, context)
        elif data == "help":
            # reuse help menu flow
            await help_menu_cmd(update, context)
        elif data == "how_to_pay":
            text = "Make payment to any of the accounts shown when you pick a package. Upload a screenshot after payment."
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help", callback_data="help")]]))
        elif data == "registration_process":
            text = "1. /start → choose package\n2. Select a payment account\n3. Upload your payment screenshot\n4. Wait for admin approval and credential assignment."
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Help", callback_data="help")]]))
        elif data == "package_selector":
            # show packages
            keyboard = [
                [InlineKeyboardButton("Standard (₦9,000)", callback_data="reg_standard")],
                [InlineKeyboardButton("X (₦14,000)", callback_data="reg_x")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
            ]
            await query.edit_message_text("Choose your package:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data in ("reg_standard", "reg_x"):
            package = "Standard" if data == "reg_standard" else "X"
            uid = query.from_user.id
            # set user package and payment_status pending_payment
            try:
                cursor.execute("UPDATE users SET package=%s, payment_status=%s WHERE chat_id=%s", (package, "pending_payment", uid))
                if cursor.rowcount == 0:
                    cursor.execute("INSERT INTO users (chat_id, username, name, package, payment_status) VALUES (%s, %s, %s, %s, %s)",
                                   (uid, query.from_user.username or "", query.from_user.full_name or "", package, "pending_payment"))
                conn.commit()
            except Exception:
                logger.exception("Error updating user package")
            # show payment accounts
            keyboard = [[InlineKeyboardButton(name, callback_data=f"select_account:{name}")]] for name in PAYMENT_ACCOUNTS.keys()
            keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
            await query.edit_message_text(f"You selected {package}. Choose a payment account and then upload your screenshot.", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("select_account:"):
            account_name = data.split("select_account:")[1]
            payment_details = PAYMENT_ACCOUNTS.get(account_name, "Contact admin for payment details.")
            # create a payment record in DB with pending status
            uid = query.from_user.id
            try:
                # Use user's current package
                cursor.execute("SELECT package FROM users WHERE chat_id=%s", (uid,))
                row = cursor.fetchone()
                package = row[0] if row else None
                cursor.execute("INSERT INTO payments (chat_id, package, payment_account, status) VALUES (%s, %s, %s, %s) RETURNING id",
                               (uid, package, account_name, "pending"))
                payment_id = cursor.fetchone()[0]
                conn.commit()
                # store in user_state for tracking screenshot expectation
                user_state[uid] = {"expecting": "reg_screenshot", "payment_id": payment_id}
            except Exception:
                logger.exception("Error creating payment record")
                await query.edit_message_text("Error creating payment record. Try again later.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]))
                return

            kb = [
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]
            ]
            await query.edit_message_text(f"Payment details:\n\n{payment_details}\n\nPlease make the payment and upload the screenshot (as a photo) in this chat.", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("approve_reg_"):
            # admin pressing approve on a payment screenshot message
            # callback format: approve_reg_<chat_id>_<payment_id>
            parts = data.split("_")
            if len(parts) >= 3:
                # expect approve_reg_<chatid>_<paymentid> or approve_reg_<chatid>
                try:
                    target_chat = int(parts[2])
                    payment_id = int(parts[3]) if len(parts) > 3 else None
                except Exception:
                    await query.edit_message_text("Invalid approval data.")
                    return
                try:
                    # mark payment approved
                    if payment_id:
                        cursor.execute("UPDATE payments SET status='approved', approved_at=%s WHERE id=%s", (datetime.datetime.now(), payment_id))
                    cursor.execute("UPDATE users SET payment_status=%s, approved_at=%s WHERE chat_id=%s", ("approved", datetime.datetime.now(), target_chat))
                    conn.commit()
                    # notify user about approval and prompt admin to finalize credentials
                    await context.bot.send_message(target_chat, "✅ Your payment has been approved by the admin. Await credentials to complete registration.")
                    await query.edit_message_text("Payment approved. Please finalize the user credentials using the button below.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Finalize Registration (Admin)", callback_data=f"finalize_reg_{target_chat}")]]))
                except Exception:
                    logger.exception("Error approving registration")
                    await query.edit_message_text("Error approving registration. See logs.")
            else:
                await query.edit_message_text("Malformed approval callback.")
        elif data.startswith("reject_reg_"):
            # reject_reg_<chat_id>_<payment_id?>
            parts = data.split("_")
            if len(parts) >= 3:
                try:
                    target_chat = int(parts[2])
                    payment_id = int(parts[3]) if len(parts) > 3 else None
                except Exception:
                    await query.edit_message_text("Invalid rejection data.")
                    return
                try:
                    if payment_id:
                        cursor.execute("UPDATE payments SET status='rejected' WHERE id=%s", (payment_id,))
                    cursor.execute("UPDATE users SET payment_status=%s WHERE chat_id=%s", ("new", target_chat))
                    conn.commit()
                    await context.bot.send_message(target_chat, "❌ Your payment was not approved. Please try again or contact the admin.")
                    await query.edit_message_text("Payment rejected and user notified.")
                except Exception:
                    logger.exception("Error rejecting reg")
                    await query.edit_message_text("Error rejecting registration. See logs.")
            else:
                await query.edit_message_text("Malformed rejection callback.")
        elif data.startswith("finalize_reg_"):
            # admin will provide username/password for user
            parts = data.split("_")
            if len(parts) >= 3:
                target_chat = int(parts[2])
                # set admin state to expect credentials text
                user_state[ADMIN_ID] = {"expecting": "user_credentials", "for_user": target_chat}
                await context.bot.send_message(ADMIN_ID, f"Send username and password for user {target_chat} in two lines:\nusername\npassword")
                await query.edit_message_text("Waiting for admin to send credentials.")
            else:
                await query.edit_message_text("Malformed finalize command.")
        else:
            await query.edit_message_text("Unknown action.")
    except Exception:
        logger.exception("Error in button_handler")
        try:
            await query.edit_message_text("An error occurred. Contact admin.")
        except Exception:
            pass


# ----------------------------
# Photo handler (user uploads payment screenshot)
# ----------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Photo uploaded by user
    msg = update.message
    uid = msg.chat.id
    log_interaction(uid, "photo_upload")
    state = user_state.get(uid, {})
    if state.get("expecting") != "reg_screenshot":
        # ignore or notify
        await msg.reply_text("No payment process detected. Start with /start to register.")
        return
    try:
        photo_file_id = msg.photo[-1].file_id
        payment_id = state.get("payment_id")
        # send photo to admin with approve/reject/finalize buttons
        kb = [
            [
                InlineKeyboardButton("Approve", callback_data=f"approve_reg_{uid}_{payment_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject_reg_{uid}_{payment_id}"),
            ],
            [InlineKeyboardButton("Finalize (set credentials)", callback_data=f"finalize_reg_{uid}")],
        ]
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=photo_file_id,
            caption=f"Payment screenshot from @{update.effective_user.username or uid} (chat_id: {uid})",
            reply_markup=InlineKeyboardMarkup(kb),
        )
        # update payment record's timestamp (already created earlier)
        await msg.reply_text("✅ Screenshot received! Admin will review and get back to you.")
        # mark user's payment_status as pending_approval
        cursor.execute("UPDATE users SET payment_status=%s WHERE chat_id=%s", ("pending_approval", uid))
        conn.commit()
        # clear expecting flag for user
        user_state.pop(uid, None)
    except Exception:
        logger.exception("Error handling photo")
        await msg.reply_text("Error processing screenshot. Try again later.")


# ----------------------------
# Text handler (admin credentials, normal messages)
# ----------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.chat.id
    text = msg.text.strip() if msg.text else ""
    log_interaction(uid, "text_message")

    # Admin credential flow
    admin_state = user_state.get(ADMIN_ID, {})
    if uid == ADMIN_ID and admin_state.get("expecting") == "user_credentials":
        # Expecting two lines: username\npassword
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if len(lines) < 2:
            await msg.reply_text("Please send credentials in two lines:\nusername\npassword")
            return
        username, password = lines[0], lines[1]
        target = admin_state.get("for_user")
        if not target:
            await msg.reply_text("No target user set. Use the finalize button on a payment message.")
            user_state.pop(ADMIN_ID, None)
            return
        try:
            cursor.execute(
                "UPDATE users SET username=%s, payment_status=%s, registration_date=%s WHERE chat_id=%s",
                (username, "registered", datetime.datetime.now(), target),
            )
            # Update payments table for the user's last approved payment (if any)
            cursor.execute(
                "UPDATE payments SET status='approved', approved_at=%s WHERE chat_id=%s AND status='pending' RETURNING id",
                (datetime.datetime.now(), target),
            )
            conn.commit()
            # send credentials to the user
            await context.bot.send_message(target, f"🎉 Registration complete!\nUsername: {username}\nPassword: {password}\n\nWelcome!")
            await msg.reply_text(f"Credentials set and sent to user {target}.")
        except Exception:
            logger.exception("Error finalizing credentials")
            await msg.reply_text("Failed to set credentials. See logs.")
        finally:
            user_state.pop(ADMIN_ID, None)
        return

    # Non-admin or other admin messages
    # Basic commands or unknown text: reply with help
    await msg.reply_text("I didn't understand that. Use /start to begin registration or /help for assistance.")


# ----------------------------
# Menu display
# ----------------------------
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # can be triggered by either callback_query or /menu command
    if update.callback_query:
        chat = update.callback_query.from_user
        chat_id = chat.id
        query = update.callback_query
        keyboard = [
            [InlineKeyboardButton("🚀 Choose Package", callback_data="package_selector")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        await query.edit_message_text("Main menu:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # from /menu command
    chat_id = update.effective_chat.id
    keyboard = [
        [InlineKeyboardButton("🚀 Choose Package", callback_data="package_selector")],
        [InlineKeyboardButton("❓ Help", callback_data="help")],
    ]
    await update.message.reply_text("Main menu:", reply_markup=InlineKeyboardMarkup(keyboard))


# ----------------------------
# Startup / main
# ----------------------------
def main():
    keep_alive()
    application = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_menu_cmd))
    application.add_handler(CommandHandler("menu", show_main_menu))
    application.add_handler(CommandHandler("adminstats", adminstats))

    # CallbackQuery handler
    application.add_handler(CallbackQueryHandler(button_handler))

    # Message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
