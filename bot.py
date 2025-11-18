import os
import logging
import json
import asyncio
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# --- Configuration & Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State Definitions ---
(
    SELECTING_ACTION,
    SUBMITTING_CONTENT,
    CONFIRM_SUBMISSION,
    REPORTING_MESSAGE,
    FEEDBACK_TEXT,
    BROADCASTING_TEXT
) = range(6)

# --- Files ---
USERS_DB = "users.json"

# --- Helper Functions ---
def load_users() -> dict:
    if not os.path.exists(USERS_DB):
        return {}
    try:
        with open(USERS_DB, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def save_user(user_id, username, first_name):
    users = load_users()
    if str(user_id) not in users:
        users[str(user_id)] = {"username": username, "first_name": first_name}
        try:
            with open(USERS_DB, "w") as f:
                json.dump(users, f, indent=4)
        except IOError:
            logger.error("Failed to save user DB")

def get_admin_id():
    try:
        return int(os.getenv("TELEGRAM_ADMIN_CHAT_ID"))
    except (TypeError, ValueError):
        logger.error("TELEGRAM_ADMIN_CHAT_ID is not set or invalid.")
        return 0

# --- Entry Point ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Checks membership and shows the main menu."""
    user = update.effective_user
    save_user(user.id, user.username, user.first_name)

    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    
    # 1. Check Membership
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user.id)
        if member.status not in ["member", "administrator", "creator"]:
            raise Exception("Not a member")
    except Exception:
        channel_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
        keyboard = [[InlineKeyboardButton("👉 Join Channel Dulu", url=channel_link)]]
        await update.message.reply_text(
            f"👋 <b>Hai {user.first_name}!</b>\n\n"
            "Sebelum guna bot ni, korang kena join channel rasmi kitorang dulu tau.\n"
            "Dah join, baru tekan /start semula ya!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # 2. Show Main Menu
    await show_main_menu(update, context)
    return SELECTING_ACTION

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the menu with animated emoji logic."""
    user_id = update.effective_user.id
    admin_id = get_admin_id()

    # Define Standard Buttons
    keyboard = [
        [InlineKeyboardButton("📨 Hantar Confession", callback_data="menu_send")],
        [InlineKeyboardButton("🚨 Report Message", callback_data="menu_report")],
        [InlineKeyboardButton("💬 Bagi Feedback", callback_data="menu_feedback")]
    ]

    # Add Broadcast button ONLY if user is Admin
    if user_id == admin_id:
        keyboard.append([InlineKeyboardButton("📢 Broadcast (Admin)", callback_data="menu_broadcast")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🔥 <b>Menu Utama</b> 🔥\n\n"
        "Pilih action kat bawah ni:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

# --- Menu Handlers ---
async def handle_menu_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data

    if choice == "menu_send":
        await query.edit_message_text(
            "🤫 <b>Mode Confession</b>\n\n"
            "Hantar je apa yang korang nak luahkan (Teks, Gambar, Video atau Audio).\n"
            "Identiti korang **RAHSIA**. Min akan post kat channel tanpa nama.",
            parse_mode=ParseMode.HTML
        )
        return SUBMITTING_CONTENT

    elif choice == "menu_report":
        await query.edit_message_text(
            "🚨 <b>Lapor Salah Laku</b>\n\n"
            "Ada mesej yang tak sepatutnya? Sila <b>Forward</b> mesej tu kat sini untuk Min semak.",
            parse_mode=ParseMode.HTML
        )
        return REPORTING_MESSAGE

    elif choice == "menu_feedback":
        await query.edit_message_text(
            "💬 <b>Feedback / Cadangan</b>\n\n"
            "Ada idea best untuk bot ni? Atau ada masalah? Tulis je kat sini.",
            parse_mode=ParseMode.HTML
        )
        return FEEDBACK_TEXT

    elif choice == "menu_broadcast":
        if update.effective_user.id != get_admin_id():
            await query.edit_message_text("⛔ Korang bukan admin.")
            return ConversationHandler.END
        
        await query.edit_message_text(
            "📢 <b>Mode Hebahan (Admin)</b>\n\n"
            "Hantar mesej yang nak di-blast kepada semua user.\n"
            "Taip /cancel untuk batal.",
            parse_mode=ParseMode.HTML
        )
        return BROADCASTING_TEXT
    
    return ConversationHandler.END

# --- Feature 1: Anonymous Submission (Text/Media) ---
async def receive_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Captures Text, Photo, Video, Audio using copy_message logic."""
    context.user_data["msg_id"] = update.message.message_id
    context.user_data["chat_id"] = update.message.chat_id

    # Create confirmation buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Onz, Post Je!", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Batal", callback_data="confirm_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👀 <b>Preview:</b> Min dah dapat mesej korang.\n\n"
        "Confirm nak post confession ni?",
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    return CONFIRM_SUBMISSION

async def confirm_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_no":
        await show_main_menu(update, context) # Return to menu
        return SELECTING_ACTION

    # Proceed with Posting
    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    ADMIN_ID = get_admin_id()
    
    user_msg_id = context.user_data.get("msg_id")
    user_chat_id = context.user_data.get("chat_id")

    try:
        # 1. Copy to Channel
        posted_msg = await context.bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=user_chat_id,
            message_id=user_msg_id
        )
        
        # 2. Notify User
        await query.edit_message_text("✅ <b>Beres!</b> Confession korang dah masuk channel.", parse_mode=ParseMode.HTML)
        
        # 3. Log to Admin
        timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%d %b %Y, %I:%M %p")
        user = query.from_user
        
        admin_log = (
            f"🔔 <b>Confession Baru!</b>\n"
            f"👤 <b>Dari:</b> {user.first_name} (@{user.username}) [`{user.id}`]\n"
            f"⏰ <b>Masa:</b> {timestamp}\n"
            f"👇 <b>Isi kandungan:</b>"
        )
        
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_log, parse_mode=ParseMode.HTML)
        
        # Copy to admin
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=user_chat_id,
            message_id=user_msg_id
        )
        
        # Add delete button for admin
        del_kb = [[InlineKeyboardButton("🗑️ Delete Post", callback_data=f"delete:{posted_msg.message_id}")]]
        await context.bot.send_message(chat_id=ADMIN_ID, text="Tindakan Admin:", reply_markup=InlineKeyboardMarkup(del_kb))

    except Exception as e:
        logger.error(f"Error posting: {e}")
        await query.edit_message_text("❌ Alamak, error pulak. Mungkin fail/video besar sangat.")

    return ConversationHandler.END

# --- Feature 2: Report Message ---
async def receive_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    ADMIN_ID = get_admin_id()

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🚨 <b>REPORT DITERIMA</b>\nDari: {user.first_name} (@{user.username})\n\nIsi Report:",
        parse_mode=ParseMode.HTML
    )
    
    await update.message.forward(chat_id=ADMIN_ID)

    await update.message.reply_text("✅ Report diterima. Terima kasih sebab bagitahu Min.")
    return ConversationHandler.END

# --- Feature 3: Feedback ---
async def receive_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    user = update.effective_user
    ADMIN_ID = get_admin_id()

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"💬 <b>FEEDBACK USER</b>\nDari: {user.first_name} (@{user.username})\n\nMesej: {text}",
        parse_mode=ParseMode.HTML
    )
    
    await update.message.reply_text("✅ Feedback dah dihantar! Mekasih support.")
    return ConversationHandler.END

# --- Feature 4: Broadcast (Admin Only) ---
async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    users = load_users()
    
    status_msg = await update.message.reply_text(f"⏳ Tengah blast mesej ke {len(users)} students...")
    
    count = 0
    blocked = 0
    
    for user_id in users:
        try:
            await context.bot.copy_message(chat_id=user_id, from_chat_id=message.chat_id, message_id=message.message_id)
            count += 1
            await asyncio.sleep(0.05)
        except Forbidden:
            blocked += 1
        except Exception:
            pass
            
    await status_msg.edit_text(
        f"📢 <b>Hebahan Selesai</b>\n\n"
        f"✅ Berjaya hantar: {count}\n"
        f"🚫 Kena block: {blocked}",
        parse_mode=ParseMode.HTML
    )
    return ConversationHandler.END

# --- Admin Actions (Delete) ---
async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("delete:"):
        msg_id = int(data.split(":")[1])
        CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=msg_id)
            await query.edit_message_text("🗑️ Post berjaya dipadam.")
        except Exception as e:
            await query.edit_message_text(f"❌ Gagal padam: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operation dibatalkan.")
    return ConversationHandler.END

# --- Main Execution ---
def main() -> None:
    env_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID"]
    if not all(os.getenv(var) for var in env_vars):
        print(f"Missing specific env vars: {env_vars}")
        return

    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(handle_menu_selection, pattern="^menu_.*")
            ],
            SUBMITTING_CONTENT: [
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_submission)
            ],
            CONFIRM_SUBMISSION: [
                CallbackQueryHandler(confirm_submission, pattern="^confirm_.*")
            ],
            REPORTING_MESSAGE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_report)
            ],
            FEEDBACK_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_feedback)
            ],
            BROADCASTING_TEXT: [
                MessageHandler(filters.ALL & ~filters.COMMAND, execute_broadcast)
            ]
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(handle_admin_callback, pattern="^delete:.*"))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()