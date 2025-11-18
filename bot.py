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
    # Helper to safely get Admin ID as integer
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
        keyboard = [[InlineKeyboardButton("👉 Join Channel First", url=channel_link)]]
        await update.message.reply_text(
            f"👋 <b>Welcome {user.first_name}!</b>\n\n"
            "To use this bot, you must first join our channel.\n"
            "Please join and then type /start again.",
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
        [InlineKeyboardButton("📨 Send Anonymous Secret", callback_data="menu_send")],
        [InlineKeyboardButton("🚨 Report a Message", callback_data="menu_report")],
        [InlineKeyboardButton("💬 Send Feedback", callback_data="menu_feedback")]
    ]

    # Add Broadcast button ONLY if user is Admin
    if user_id == admin_id:
        keyboard.append([InlineKeyboardButton("📢 Broadcast (Admin)", callback_data="menu_broadcast")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🔥 <b>Main Menu</b> 🔥\n\n"
        "Select an action below:"
    )

    # Handle both Message and CallbackQuery (if returning to menu)
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
            "🤫 <b>Anonymous Mode</b>\n\n"
            "Send me your text, photo, video, or audio.\n"
            "I will post it anonymously to the channel.",
            parse_mode=ParseMode.HTML
        )
        return SUBMITTING_CONTENT

    elif choice == "menu_report":
        await query.edit_message_text(
            "🚨 <b>Report Message</b>\n\n"
            "Please <b>forward</b> the message you want to report to me, or send the Message ID.",
            parse_mode=ParseMode.HTML
        )
        return REPORTING_MESSAGE

    elif choice == "menu_feedback":
        await query.edit_message_text(
            "💬 <b>Feedback</b>\n\n"
            "Please type your feedback or suggestion for the admin.",
            parse_mode=ParseMode.HTML
        )
        return FEEDBACK_TEXT

    elif choice == "menu_broadcast":
        # Double check security
        if update.effective_user.id != get_admin_id():
            await query.edit_message_text("⛔ Access Denied.")
            return ConversationHandler.END
        
        await query.edit_message_text(
            "📢 <b>Broadcast Mode</b>\n\n"
            "Send the message you want to broadcast to all users.\n"
            "Type /cancel to stop.",
            parse_mode=ParseMode.HTML
        )
        return BROADCASTING_TEXT
    
    return ConversationHandler.END

# --- Feature 1: Anonymous Submission (Text/Media) ---
async def receive_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Captures Text, Photo, Video, Audio using copy_message logic."""
    # Store the message object to copy later
    context.user_data["msg_id"] = update.message.message_id
    context.user_data["chat_id"] = update.message.chat_id

    # Create confirmation buttons
    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, Post It", callback_data="confirm_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="confirm_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # We reply to the message asking for confirmation
    await update.message.reply_text(
        "👀 <b>Preview:</b> I have received your message/media.\n\n"
        "Are you sure you want to post this anonymously?",
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
        # 1. Copy to Channel (Preserves Media, Captions, Format)
        posted_msg = await context.bot.copy_message(
            chat_id=CHANNEL_ID,
            from_chat_id=user_chat_id,
            message_id=user_msg_id
        )
        
        # 2. Notify User
        await query.edit_message_text("✅ <b>Sent!</b> Your secret is live.", parse_mode=ParseMode.HTML)
        
        # 3. Log to Admin
        timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%d %b %Y, %I:%M %p")
        user = query.from_user
        
        admin_log = (
            f"🔔 <b>New Confession</b>\n"
            f"👤 <b>From:</b> {user.first_name} (@{user.username}) [`{user.id}`]\n"
            f"⏰ <b>Time:</b> {timestamp}\n"
            f"👇 <b>Content below:</b>"
        )
        
        await context.bot.send_message(chat_id=ADMIN_ID, text=admin_log, parse_mode=ParseMode.HTML)
        
        # Copy the content to admin so they see exactly what was posted
        await context.bot.copy_message(
            chat_id=ADMIN_ID,
            from_chat_id=user_chat_id,
            message_id=user_msg_id
        )
        
        # Add delete button for admin
        del_kb = [[InlineKeyboardButton("🗑️ Delete Post", callback_data=f"delete:{posted_msg.message_id}")]]
        await context.bot.send_message(chat_id=ADMIN_ID, text="Action:", reply_markup=InlineKeyboardMarkup(del_kb))

    except Exception as e:
        logger.error(f"Error posting: {e}")
        await query.edit_message_text("❌ Error posting message. It might be too large or unsupported.")

    return ConversationHandler.END

# --- Feature 2: Report Message ---
async def receive_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Forwards the report to admin."""
    user = update.effective_user
    ADMIN_ID = get_admin_id()

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🚨 <b>REPORT RECEIVED</b>\nFrom: {user.first_name} (@{user.username})\n\nContent:",
        parse_mode=ParseMode.HTML
    )
    
    # Forward the actual reported message
    await update.message.forward(chat_id=ADMIN_ID)

    await update.message.reply_text("✅ Report sent to admins. Thank you.")
    return ConversationHandler.END

# --- Feature 3: Feedback ---
async def receive_feedback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    user = update.effective_user
    ADMIN_ID = get_admin_id()

    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"💬 <b>FEEDBACK</b>\nFrom: {user.first_name} (@{user.username})\n\n{text}",
        parse_mode=ParseMode.HTML
    )
    
    await update.message.reply_text("✅ Feedback sent! We appreciate it.")
    return ConversationHandler.END

# --- Feature 4: Broadcast (Admin Only) ---
async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    message = update.message
    users = load_users()
    
    status_msg = await update.message.reply_text(f"⏳ Broadcasting to {len(users)} users...")
    
    count = 0
    blocked = 0
    
    for user_id in users:
        try:
            # Using copy_message allows admin to broadcast images/videos too
            await context.bot.copy_message(chat_id=user_id, from_chat_id=message.chat_id, message_id=message.message_id)
            count += 1
            await asyncio.sleep(0.05) # Safety delay
        except Forbidden:
            blocked += 1
        except Exception:
            pass
            
    await status_msg.edit_text(
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: {count}\n"
        f"🚫 Blocked: {blocked}",
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
            await query.edit_message_text("🗑️ Post deleted successfully.")
        except Exception as e:
            await query.edit_message_text(f"❌ Failed to delete: {e}")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# --- Main Execution ---
def main() -> None:
    env_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID"]
    if not all(os.getenv(var) for var in env_vars):
        print(f"Missing specific env vars: {env_vars}")
        return

    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    # The Main Conversation Handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(handle_menu_selection, pattern="^menu_.*")
            ],
            SUBMITTING_CONTENT: [
                # This filter captures Text, Photos, Video, Audio, Documents (Everything)
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