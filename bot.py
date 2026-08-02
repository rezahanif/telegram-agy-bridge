import logging
import os
import subprocess
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID_ENV = os.getenv("TELEGRAM_ALLOWED_USER_ID")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL: TELEGRAM_BOT_TOKEN environment variable is missing.")

if not ALLOWED_USER_ID_ENV:
    raise ValueError("CRITICAL: TELEGRAM_ALLOWED_USER_ID environment variable is missing.")

ALLOWED_USER_ID = int(ALLOWED_USER_ID_ENV)
AGY_PATH = os.getenv("AGY_PATH", "/root/.local/bin/agy")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/project")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Session configuration per chat
user_sessions = {}

AVAILABLE_MODELS = [
    ("Gemini 3.6 Flash", "gemini-3.6-flash-medium"),
    ("Gemini 3.5 Flash", "gemini-3.5-flash-medium"),
    ("Gemini 3.1 Pro", "gemini-3.1-pro-high"),
    ("Claude Sonnet 4.6", "claude-sonnet-4-6"),
    ("Claude Opus 4.6", "claude-opus-4-6-thinking"),
    ("GPT-OSS 120B", "gpt-oss-120b-medium"),
]

EFFORT_LEVELS = [
    ("Low Effort", "low"),
    ("Medium Effort", "medium"),
    ("High Effort", "high"),
]

def get_session(chat_id):
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {
            "has_active_session": False,
            "model": "gemini-3.6-flash-medium",
            "effort": "medium"
        }
    return user_sessions[chat_id]

async def send_model_menu(update: Update, chat_id: int):
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"set_model:{model_id}")]
        for name, model_id in AVAILABLE_MODELS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    session = get_session(chat_id)
    await update.message.reply_text(
        f"🧠 Select AI Model (Current: {session['model']}):",
        reply_markup=reply_markup
    )

async def send_effort_menu(update: Update, chat_id: int):
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"set_effort:{effort_id}")]
        for name, effort_id in EFFORT_LEVELS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    session = get_session(chat_id)
    await update.message.reply_text(
        f"⚡ Select Reasoning Effort (Current: {session['effort']}):",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    logging.info(f"Callback query received: {query.data} from user {query.from_user.id}")
    if query.from_user.id != ALLOWED_USER_ID:
        await query.answer("Unauthorized access.")
        return

    await query.answer()
    data = query.data
    chat_id = query.message.chat.id
    session = get_session(chat_id)

    if data.startswith("set_model:"):
        model_id = data.split("set_model:")[1]
        session["model"] = model_id
        await query.edit_message_text(f"✅ Model set to {model_id}")
    elif data.startswith("set_effort:"):
        effort_id = data.split("set_effort:")[1]
        session["effort"] = effort_id
        await query.edit_message_text(f"✅ Reasoning Effort set to {effort_id}")

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()
    logging.info(f"Received text from user {user_id}: {user_text}")

    if user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    session = get_session(chat_id)

    # Dispatch slash commands manually to ensure reliability
    cmd_lower = user_text.lower()
    if cmd_lower in ("/start", "/help"):
        await update.message.reply_text(
            "🚀 Connected to Antigravity CLI Bridge!\n\n"
            f"• Active Model: {session['model']}\n"
            f"• Effort Level: {session['effort']}\n\n"
            "Commands:\n"
            "• /model - Select AI model\n"
            "• /thinking or /effort - Select reasoning effort\n"
            "• /new or /reset - Start a fresh conversation session"
        )
        return

    if cmd_lower in ("/new", "/reset"):
        session["has_active_session"] = False
        await update.message.reply_text("🔄 Started a new session! Your next message will begin a fresh conversation.")
        return

    if cmd_lower.startswith("/model"):
        parts = user_text.split(maxsplit=1)
        if len(parts) > 1:
            session["model"] = parts[1].strip()
            await update.message.reply_text(f"✅ Model set to {session['model']}")
        else:
            await send_model_menu(update, chat_id)
        return

    if cmd_lower.startswith("/thinking") or cmd_lower.startswith("/effort"):
        parts = user_text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip() in ("low", "medium", "high"):
            session["effort"] = parts[1].strip()
            await update.message.reply_text(f"✅ Reasoning Effort set to {session['effort']}")
        else:
            await send_effort_menu(update, chat_id)
        return

    # Regular message -> Execute agy
    status_msg = await update.message.reply_text("⏳ Thinking...")

    cmd = [AGY_PATH]
    if session["has_active_session"]:
        cmd.append("--continue")
    
    cmd.extend([
        "--model", session["model"],
        "--effort", session["effort"],
        "-p", user_text
    ])

    try:
        logging.info(f"Executing agy command: {cmd}")
        process = subprocess.run(
            cmd,
            cwd=WORKSPACE_DIR,
            capture_output=True,
            text=True,
            timeout=180
        )
        
        output = process.stdout.strip() or process.stderr.strip() or "Task finished with no output."
        logging.info(f"agy returncode: {process.returncode}, output length: {len(output)}")
        
        session["has_active_session"] = True

        try:
            await status_msg.delete()
        except Exception:
            pass

        if len(output) > 4000:
            for chunk in [output[i:i+4000] for i in range(0, len(output), 4000)]:
                await update.message.reply_text(f"```\n{chunk}\n```", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")

    except subprocess.TimeoutExpired:
        await status_msg.edit_text("⚠️ Command execution timed out (180s limit).")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")

def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.ALL, handle_any_message))
    logging.info("Starting Persistent Telegram Antigravity Bridge Bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
