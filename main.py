import logging
import psycopg
import re
import datetime
import os
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from flask import Flask
from threading import Thread

# Flask setup
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Bot credentials
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
SITE_LINK = os.getenv("SITE_LINK")
AI_BOOST_LINK = os.getenv("AI_BOOST_LINK")  # For X package special content

# Predefined payment accounts
PAYMENT_ACCOUNTS = {
    "Nigeria (Kuda)": "🇳🇬 Account: 2036035854\nBank: Kuda Bank\nName: Eluem, Chike Olanrewaju",
    "Nigeria (Zenith)": "🇳🇬 Account: 2267515466\nBank: Zenith Bank\nName: Chike Eluem Olanrewaju",
    "Nigeria (OPay)": "🇳🇬 Account: 8051454564\nBank: OPay\nName: Chike Eluem Olanrewaju",
}

# Help topics
HELP_TOPICS = {
    "how_to_pay": {"label": "How to Pay", "type": "text", "text": "Payments can be made via bank transfer. Select an account from the options provided after choosing your package."},
    "register": {"label": "Registration Process", "type": "text", "text": (
        "1. /start → choose package\n"
        "2. Pay via your selected account → upload screenshot\n"
        "3. Wait for approval, then send details\n"
        "4. Receive credentials and access!"
    )},
}

# Database setup with PostgreSQL
try:
    import urllib.parse as urlparse

    url = os.getenv("DATABASE_URL")
    result = urlparse.urlparse(url)
    conn = psycopg.connect(
        dbname=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode='require'
    )
    cursor = conn.cursor()

    # Users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id BIGINT PRIMARY KEY,
        name TEXT,
        username TEXT,
        email TEXT,
        phone TEXT,
        package TEXT,
        payment_status TEXT DEFAULT 'new',
        approved_at TIMESTAMP,
        registration_date TIMESTAMP
    )
    """)

    # Payments table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT,
        package TEXT,
        payment_account TEXT,
        status TEXT DEFAULT 'pending_payment',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        approved_at TIMESTAMP
    )
    """)

    conn.commit()
except psycopg.Error as e:
    logging.error(f"Database error: {e}")
    raise

# In-memory storage
user_state = {}

# Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Helper functions
def get_status(chat_id):
    try:
        cursor.execute("SELECT payment_status FROM users WHERE chat_id=%s", (chat_id,))
        row = cursor.fetchone()
        return row[0] if row else None
    except psycopg.Error as e:
        logger.error(f"Database error in get_status: {e}")
        return None

def log_interaction(chat_id, action):
    logger.info(f"Interaction: chat_id={chat_id}, action={action}")

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    log_interaction(chat_id, "start")
    try:
        cursor.execute("SELECT payment_status FROM users WHERE chat_id=%s", (chat_id,))
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO users (chat_id, username) VALUES (%s, %s)",
                (chat_id, update.effective_user.username or "Unknown")
            )
            conn.commit()
    except psycopg.Error as e:
        logger.error(f"Database error in start: {e}")
        await update.message.reply_text("An error occurred. Please try again.")
        return
    keyboard = [[InlineKeyboardButton("🚀 Proceed", callback_data="menu")]]
    await update.message.reply_text(
        "Welcome to the Bot!\n\nChoose your package and start today.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id != ADMIN_ID:
        await update.message.reply_text("This command is restricted to the admin.")
        return
    log_interaction(chat_id, "admin_stats")
    try:
        cursor.execute("SELECT COUNT(*) FROM users WHERE payment_status='registered' AND package='Standard'")
        standard_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM users WHERE payment_status='registered' AND package='X'")
        x_count = cursor.fetchone()[0]
        total = standard_count + x_count
        cursor.execute("""
        SELECT chat_id, package, registration_date 
        FROM users 
        WHERE payment_status='registered' 
        ORDER BY registration_date DESC 
        LIMIT 10
        """)
        last_users = cursor.fetchall()
        text = f"📊 Admin Stats:\n\n• Standard Users: {standard_count}\n• X Users: {x_count}\n• Total Registrations: {total}\n\nLast 10 Registrations:\n"
        for user in last_users:
            text += f"Chat ID: {user[0]}, Package: {user[1]}, Date: {user[2]}\n"
        await update.message.reply_text(text)
    except psycopg.Error as e:
        logger.error(f"Database error in admin_stats: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

# Callback handlers
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    chat_id = query.from_user.id
    logger.info(f"Received callback data: {data} from chat_id: {chat_id}")
    await query.answer()
    log_interaction(chat_id, f"button_{data}")

    try:
        if data == "menu":
            if chat_id in user_state:
                del user_state[chat_id]
            await show_main_menu(update, context)
        elif data == "package_selector":
            status = get_status(chat_id)
            if status == 'registered':
                await context.bot.send_message(chat_id, "You are already registered.")
                return
            keyboard = [
                [InlineKeyboardButton("🚀X (₦14,000)", callback_data="reg_x")],
                [InlineKeyboardButton("✈️Standard (₦9,000)", callback_data="reg_standard")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")],
            ]
            await query.edit_message_text("Choose your package:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data in ["reg_standard", "reg_x"]:
            package = "Standard" if data == "reg_standard" else "X"
            user_state[chat_id] = {'package': package}
            try:
                cursor.execute("UPDATE users SET package=%s, payment_status='pending_payment' WHERE chat_id=%s", (package, chat_id))
                if cursor.rowcount == 0:
                    cursor.execute("INSERT INTO users (chat_id, package, payment_status, username) VALUES (%s, %s, 'pending_payment', %s)", (chat_id, package, update.effective_user.username or "Unknown"))
                conn.commit()
                keyboard = [[InlineKeyboardButton(a, callback_data=f"reg_account_{a}")] for a in PAYMENT_ACCOUNTS.keys()]
                keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
                await query.edit_message_text("Select an account to pay to:", reply_markup=InlineKeyboardMarkup(keyboard))
            except psycopg.Error as e:
                logger.error(f"Database error in package_selector: {e}")
                await query.edit_message_text("An error occurred. Please try again.")
                return
        elif data.startswith("reg_account_"):
            account = data[len("reg_account_"):]
            payment_details = PAYMENT_ACCOUNTS.get(account)
            if not payment_details:
                await context.bot.send_message(chat_id, "Error: Invalid account. Contact admin.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]))
                return
            user_state[chat_id]['selected_account'] = account
            user_state[chat_id]['expecting'] = 'reg_screenshot'
            keyboard = [
                [InlineKeyboardButton("Change Account", callback_data="show_account_selection")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]
            ]
            await context.bot.send_message(
                chat_id,
                f"Payment details:\n\n{payment_details}\n\nPlease make the payment and send the screenshot.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif data == "show_account_selection":
            package = user_state[chat_id].get('package', '')
            if not package:
                await query.edit_message_text("Please select a package first.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]))
                return
            keyboard = [[InlineKeyboardButton(a, callback_data=f"reg_account_{a}")] for a in PAYMENT_ACCOUNTS.keys()]
            keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
            await query.edit_message_text("Select an account to pay to:", reply_markup=InlineKeyboardMarkup(keyboard))
        elif data.startswith("approve_reg_"):
            user_chat_id = int(data.split("_")[2])
            try:
                cursor.execute("UPDATE users SET payment_status='pending_details', approved_at=%s WHERE chat_id=%s", (datetime.datetime.now(), user_chat_id))
                conn.commit()
                await context.bot.send_message(
                    user_chat_id,
                    "✅ Your payment is approved!\n\nPlease send your details:\n➡️ Email address\n➡️ Full name\n➡️ Username (e.g. @you)\n➡️ Phone number (with country code)\n\nAll in one message, each on its own line."
                )
                await query.edit_message_text("Payment approved. Waiting for user details.")
            except psycopg.Error as e:
                logger.error(f"Database error in approve_reg: {e}")
                await query.edit_message_text("An error occurred. Please try again.")
        elif data.startswith("finalize_reg_"):
            user_chat_id = int(data.split("_")[2])
            user_state[ADMIN_ID] = {'expecting': 'user_credentials', 'for_user': user_chat_id}
            await context.bot.send_message(
                ADMIN_ID,
                f"Please send the username and password for user {user_chat_id} in the format:\nusername\npassword"
            )
            await query.edit_message_text("Waiting for user credentials.")
        elif data.startswith("pending_reg_"):
            user_chat_id = int(data.split("_")[2])
            await context.bot.send_message(user_chat_id, "Your payment is still being reviewed. Please check back later.")
        elif data == "access_content":
            cursor.execute("SELECT package FROM users WHERE chat_id=%s", (chat_id,))
            package = cursor.fetchone()[0]
            if package == "X":
                text = f"Access your special X content here: {AI_BOOST_LINK}"
            else:
                text = f"Access your content here: {SITE_LINK}"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]]))
        elif data in HELP_TOPICS:
            topic = HELP_TOPICS[data]
            keyboard = [[InlineKeyboardButton("🔙 Help Menu", callback_data="help")]]
            content = topic["text"]
            await query.edit_message_text(content, reply_markup=InlineKeyboardMarkup(keyboard))
        elif data == "help":
            await help_menu(update, context)
        else:
            logger.warning(f"Unknown callback data: {data}")
            await query.edit_message_text("Unknown action. Please try again or contact admin.")
    except Exception as e:
        logger.error(f"Error in button_handler: {e}")
        await query.edit_message_text("An error occurred. Please try again or contact admin.")

# Message handlers
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if 'expecting' not in user_state.get(chat_id, {}):
        return
    expecting = user_state[chat_id]['expecting']
    photo_file = update.message.photo[-1].file_id
    try:
        if expecting == 'reg_screenshot':
            cursor.execute("INSERT INTO payments (chat_id, package, payment_account) VALUES (%s, %s, %s) RETURNING id",
                           (chat_id, user_state[chat_id]['package'], user_state[chat_id]['selected_account']))
            payment_id = cursor.fetchone()[0]
            conn.commit()
            keyboard = [
                [InlineKeyboardButton("Approve", callback_data=f"approve_reg_{chat_id}")],
                [InlineKeyboardButton("Pending", callback_data=f"pending_reg_{chat_id}")],
            ]
            await context.bot.send_photo(
                ADMIN_ID,
                photo_file,
                caption=f"📸 Registration Payment from @{update.effective_user.username or 'Unknown'} (chat_id: {chat_id})",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await update.message.reply_text("✅ Screenshot received! Awaiting admin approval.")
            del user_state[chat_id]['expecting']
        log_interaction(chat_id, "photo_upload")
    except Exception as e:
        logger.error(f"Error in handle_photo: {e}")
        await update.message.reply_text("An error occurred. Please try again or contact admin.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    text = update.message.text
    log_interaction(chat_id, "text_message")
    if 'expecting' in user_state.get(chat_id, {}):
        expecting = user_state[chat_id]['expecting']
        try:
            if expecting == 'user_credentials' and chat_id == ADMIN_ID:
                lines = text.splitlines()
                if len(lines) != 2:
                    await update.message.reply_text("Please send username and password in two lines.")
                    return
                username, password = lines
                for_user = user_state[chat_id]['for_user']
                cursor.execute(
                    "UPDATE users SET username=%s, payment_status='registered', registration_date=%s WHERE chat_id=%s",
                    (username, datetime.datetime.now(), for_user)
                )
                conn.commit()
                await context.bot.send_message(
                    for_user,
                    f"🎉 Registration successful! Your username is {username} and password is {password}.\n\nAccess the site: {SITE_LINK}"
                )
                await update.message.reply_text("Credentials set and sent to the user.")
                del user_state[chat_id]
        except Exception as e:
            logger.error(f"Error in handle_text: {e}")
            await update.message.reply_text("An error occurred. Please try again or contact admin.")
    else:
        status = get_status(chat_id)
        if status == 'pending_details':
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if len(lines) < 4:
                await update.message.reply_text("❗️ Please send all four lines.")
                return
            email, full_name, username, phone = lines[:4]
            if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
                await update.message.reply_text("❗️ Invalid email.")
                return
            if not username.startswith('@'):
                await update.message.reply_text("❗️ Username must start with @.")
                return
            try:
                cursor.execute(
                    "UPDATE users SET email=%s, name=%s, username=%s, phone=%s WHERE chat_id=%s",
                    (email, full_name, username, phone, chat_id)
                )
                conn.commit()
                cursor.execute("SELECT package FROM users WHERE chat_id=%s", (chat_id,))
                pkg = cursor.fetchone()[0]
                keyboard = [[InlineKeyboardButton("Finalize Registration", callback_data=f"finalize_reg_{chat_id}")]]
                await context.bot.send_message(
                    ADMIN_ID,
                    f"🆕 User Details Received:\nUser ID: {chat_id}\nUsername: {username}\nPackage: {pkg}\nEmail: {email}\nName: {full_name}\nPhone: {phone}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                await update.message.reply_text(
                    "✅ Details received! Awaiting admin finalization.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Main Menu", callback_data="menu")]])
                )
            except psycopg.Error as e:
                logger.error(f"Database error in pending_details: {e}")
                await update.message.reply_text("An error occurred. Please try again.")

# Menus
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        cursor.execute("SELECT payment_status, package FROM users WHERE chat_id=%s", (chat_id,))
        user = cursor.fetchone()
        keyboard = [
            [InlineKeyboardButton("💸 Register & Make Payment", callback_data="package_selector")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
        ]
        if user and user[0] == 'registered':
            keyboard = [
                [InlineKeyboardButton("📂 Access Content", callback_data="access_content")],
                [InlineKeyboardButton("❓ Help", callback_data="help")],
            ]
        text = "Select an option below:"
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        log_interaction(chat_id, "show_main_menu")
    except psycopg.Error as e:
        logger.error(f"Database error in show_main_menu: {e}")
        await update.message.reply_text("An error occurred. Please try again.")

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.callback_query.from_user.id
    keyboard = [[InlineKeyboardButton(topic["label"], callback_data=key)] for key, topic in HELP_TOPICS.items()]
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu")])
    query = update.callback_query
    await query.edit_message_text("What would you like help with?", reply_markup=InlineKeyboardMarkup(keyboard))
    log_interaction(chat_id, "help_menu")

# Main
def main():
    keep_alive()
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("menu", show_main_menu))
        application.add_handler(CommandHandler("adminstats", admin_stats))
        application.add_handler(CallbackQueryHandler(button_handler))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        # Log that the bot is running
        logger.info("Bot is up and running...")
        # Start polling
        application.run_polling()
    except Exception as e:
        logger.error(f"Error in main: {e}")
        print("Failed to start bot. Check logs for details.")

if __name__ == "__main__":
    main()
