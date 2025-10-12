import os
import logging
import json
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

# --- Configuration: Setting up logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State definitions for conversation ---
SUBMITTING, AWAITING_CONFIRMATION = range(2)

# --- File for persisting pending messages ---
PENDING_DB = "pending_messages.json"

# --- Helper functions for persistence ---
def load_pending_messages():
    """Loads pending messages from the JSON file."""
    if not os.path.exists(PENDING_DB):
        return {}
    try:
        with open(PENDING_DB, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        logger.error("Could not read or parse pending_messages.json", exc_info=True)
        return {}

def save_pending_messages(data):
    """Saves pending messages to the JSON file."""
    try:
        with open(PENDING_DB, "w") as f:
            json.dump(data, f, indent=4)
    except IOError:
        logger.error("Could not write to pending_messages.json", exc_info=True)


# --- User-facing conversation handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the conversation and asks the user for their message."""
    await update.message.reply_text(
        "IN THE DEVELOPMENT\n"
        "Welcome! I can help you post a message anonymously to the channel.\n\n"
        "Please send me the message you want to post. To cancel, type /cancel."
    )
    return SUBMITTING

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Stores the message and asks for confirmation."""
    message_text = update.message.text
    context.user_data["message_to_send"] = message_text

    keyboard = [
        [
            InlineKeyboardButton("Yes, send it for review", callback_data="confirm_yes"),
            InlineKeyboardButton("No, cancel", callback_data="confirm_no"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Your message:\n\n---\n"
        f"{message_text}\n---\n\n"
        "Do you want to submit this for approval?",
        reply_markup=reply_markup,
    )
    return AWAITING_CONFIRMATION

async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles the user's confirmation (Yes/No)."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_yes":
        message_text = context.user_data.get("message_to_send")
        if not message_text:
            await query.edit_message_text(text="Sorry, something went wrong. Please start over with /start.")
            return ConversationHandler.END

        # --- Forward to Admin for Approval ---
        ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
        user_id = query.from_user.id
        
        # Unique identifier for the callback
        callback_id = f"{user_id}_{update.effective_message.message_id}"

        keyboard = [
            [
                InlineKeyboardButton("Approve", callback_data=f"approve:{callback_id}"),
                InlineKeyboardButton("Decline", callback_data=f"decline:{callback_id}"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        admin_message = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"New message for approval from user `{user_id}`:\n\n---\n{message_text}\n---",
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN,
        )

        # Persist the message
        pending = load_pending_messages()
        pending[callback_id] = {
            "text": message_text,
            "user_id": user_id,
            "admin_message_id": admin_message.message_id
        }
        save_pending_messages(pending)

        await query.edit_message_text(text="Thank you! Your message has been sent for review.")
        
    elif query.data == "confirm_no":
        await query.edit_message_text(text="Submission cancelled. You can start over with /start.")

    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Operation cancelled. Have a great day!")
    return ConversationHandler.END


# --- Admin-facing action handler ---

async def handle_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Approve' or 'Decline' from the admin chat."""
    query = update.callback_query
    await query.answer()

    action, callback_id = query.data.split(":", 1)
    
    pending = load_pending_messages()
    submission = pending.get(callback_id)

    if not submission:
        await query.edit_message_text(text="This action has already been processed or the submission expired.", reply_markup=None)
        return

    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    user_id = submission["user_id"]
    message_text = submission["text"]
    admin_message_id = submission["admin_message_id"]

    if action == "approve":
        try:
            await context.bot.send_message(chat_id=CHANNEL_ID, text=message_text)
            await context.bot.send_message(chat_id=user_id, text="Your message has been approved and posted!")
            await context.bot.edit_message_text(
                chat_id=ADMIN_CHAT_ID,
                message_id=admin_message_id,
                text=f"✅ Approved\n\n---\n{message_text}\n---",
                reply_markup=None
            )
        except Exception as e:
            logger.error(f"Failed to post approved message: {e}")
            await context.bot.send_message(chat_id=user_id, text="Sorry, an error occurred while trying to post your approved message.")
            
    elif action == "decline":
        await context.bot.send_message(chat_id=user_id, text="Sorry, your message was not approved.")
        await context.bot.edit_message_text(
            chat_id=ADMIN_CHAT_ID,
            message_id=admin_message_id,
            text=f"❌ Declined\n\n---\n{message_text}\n---",
            reply_markup=None
        )

    # Clean up the processed submission
    if callback_id in pending:
        del pending[callback_id]
        save_pending_messages(pending)

# --- Main Bot Setup ---

def main() -> None:
    """Run the bot."""
    # --- Load Environment Variables ---
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
    ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID")

    if not all([BOT_TOKEN, CHANNEL_ID, ADMIN_CHAT_ID]):
        logger.fatal("FATAL: One or more environment variables are not set. (TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_ADMIN_CHAT_ID)")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # --- Conversation handler for user submissions ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SUBMITTING: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)],
            AWAITING_CONFIRMATION: [CallbackQueryHandler(handle_confirmation, pattern="^confirm_.*")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # --- Handler for admin actions ---
    admin_handler = CallbackQueryHandler(handle_admin_action, pattern="^(approve|decline):.*")

    application.add_handler(conv_handler)
    application.add_handler(admin_handler)

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
