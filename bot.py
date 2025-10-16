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

# --- Configuration: Setting up logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State definitions for conversations ---
SUBMITTING, AWAITING_CONFIRMATION, BROADCASTING = range(3)

# --- Files for persistence ---
USERS_DB = "users.json"

# --- Helper functions for data persistence ---
def load_json_data(filepath: str) -> dict:
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.error(f"Could not read or parse {filepath}", exc_info=True)
        return {}

def save_json_data(filepath: str, data: dict):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
    except IOError:
        logger.error(f"Could not write to {filepath}", exc_info=True)

# --- User-facing conversation handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the bot, saves user ID, and checks for channel membership."""
    user = update.effective_user
    user_id = user.id
    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    
    # Save user ID for broadcasting
    users = load_json_data(USERS_DB)
    if str(user_id) not in users:
        users[str(user_id)] = {"username": user.username, "first_name": user.first_name}
        save_json_data(USERS_DB, users)
        logger.info(f"New user saved: {user_id}")

    await update.message.reply_text("Welcome! Let me check if you're a member of our channel first...")

    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        if member.status not in ["member", "administrator", "creator"]:
            raise Exception("User is not a member.")
        
        logger.info(f"User {user_id} is a member.")
        await update.message.reply_text(
            "Great, you're a member! Please send me the message you want to post.\n\nTo cancel at any time, type /cancel."
        )
        return SUBMITTING
        
    except Exception:
        logger.info(f"User {user_id} is not a member of {CHANNEL_ID}.")
        channel_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
        await update.message.reply_text(
            f"It looks like you haven't joined our channel yet. Please join us at {channel_link} and then type /start to try again."
        )
        return ConversationHandler.END

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives user message and asks for confirmation before posting."""
    message_text = update.message.text
    context.user_data["message_to_send"] = message_text

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, post it", callback_data="confirm_post_yes"),
            InlineKeyboardButton("❌ No, cancel", callback_data="confirm_post_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"Your message:\n---\n{message_text}\n---\n\nAre you sure you want to post this anonymously?",
        reply_markup=reply_markup,
    )
    return AWAITING_CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles user confirmation to post or cancel the message."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_post_yes":
        message_text = context.user_data.get("message_to_send")
        if not message_text:
            await query.edit_message_text(text="Sorry, an error occurred. Please /start again.")
            return ConversationHandler.END

        user = query.from_user
        CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
        ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

        try:
            # Post the message to the public channel
            posted_message = await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=message_text
            )
            logger.info(f"Posted message {posted_message.message_id} to channel {CHANNEL_ID}")

            # Send a confirmation to the user
            await query.edit_message_text(text="✅ Your message has been posted anonymously to the channel!")

            # Prepare user info and timestamp for admin
            user_info = f"ID: `{user.id}`"
            if user.username:
                user_info += f", Username: @{user.username}"
            
            # Timezone for Malaysia (GMT+8)
            malaysia_tz = timezone(timedelta(hours=8))
            timestamp = datetime.now(malaysia_tz).strftime("%d %b %Y, %I:%M %p")

            # Send a notification to the admin with a delete button
            keyboard = [
                [
                    InlineKeyboardButton("🗑️ Delete Post", callback_data=f"delete:{posted_message.message_id}"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=(
                    f"*New Post*\n\n"
                    f"👤 *User:* {user_info}\n"
                    f"⏰ *Time:* {timestamp} (GMT+8)\n\n"
                    f"*Message Content:*\n---\n{message_text}\n---"
                ),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Failed to post message for user {user.id}: {e}", exc_info=True)
            await query.edit_message_text(text="Sorry, an error occurred and I couldn't post your message. Please try again later.")

    elif query.data == "confirm_post_no":
        await query.edit_message_text(text="Submission cancelled. Use /start to send a different message.")
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


# --- Admin-facing handlers ---
async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Delete' from the admin chat."""
    query = update.callback_query
    await query.answer()

    action, message_id = query.data.split(":", 1)
    
    if action == "delete":
        CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
        try:
            await context.bot.delete_message(chat_id=CHANNEL_ID, message_id=int(message_id))
            await query.edit_message_text(text="🗑️ Message has been deleted from the channel.", reply_markup=None)
            logger.info(f"Admin {query.from_user.id} deleted message {message_id} from channel {CHANNEL_ID}.")
        except BadRequest:
            # This happens if the message was already deleted
            await query.edit_message_text(text="Could not delete message. It may have been deleted already.", reply_markup=None)
            logger.warning(f"Admin {query.from_user.id} failed to delete message {message_id} (already deleted?).")
        except Exception as e:
            logger.error(f"Error deleting message {message_id}: {e}")
            await query.edit_message_text(text="An error occurred while trying to delete the message.", reply_markup=None)


async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the broadcast conversation. Admin only."""
    ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    # A simple check to see if the user is the admin. For multi-admin setups, a list would be better.
    if str(update.effective_user.id) not in ADMIN_CHAT_ID:
        await update.message.reply_text("This command is for admins only.")
        return ConversationHandler.END

    await update.message.reply_text("Please send the broadcast message. Use /cancel to stop.")
    return BROADCASTING

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends the broadcast message to all users."""
    broadcast_text = update.message.text
    users = load_json_data(USERS_DB)
    
    await update.message.reply_text(f"Starting broadcast to {len(users)} users. This may take a while...")
    
    success_count = 0
    fail_count = 0
    for user_id in users:
        try:
            await context.bot.send_message(chat_id=user_id, text=broadcast_text)
            success_count += 1
            await asyncio.sleep(0.1) # Avoid hitting rate limits
        except Forbidden:
            logger.warning(f"User {user_id} has blocked the bot. Skipping.")
            fail_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id}: {e}")
            fail_count += 1
            
    await update.message.reply_text(
        f"Broadcast complete.\n\nSuccessfully sent: {success_count}\nFailed or blocked: {fail_count}"
    )
    return ConversationHandler.END

# --- Main Bot Setup ---
def main() -> None:
    """Run the bot."""
    env_vars = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID"]
    if not all(os.getenv(var) for var in env_vars):
        logger.fatal(f"FATAL: One or more environment variables are not set. Required: {', '.join(env_vars)}")
        return

    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()

    submission_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBMITTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            AWAITING_CONFIRMATION: [CallbackQueryHandler(handle_confirmation, pattern="^confirm_post_.*")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={BROADCASTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message)]},
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(submission_conv)
    application.add_handler(broadcast_conv)
    application.add_handler(CallbackQueryHandler(handle_admin_action, pattern="^delete:.*"))

    logger.info("Bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()

