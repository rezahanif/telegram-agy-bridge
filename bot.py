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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return
    
    session = get_session(update.effective_chat.id)
    await update.message.reply_text(
        "🚀 Connected to Antigravity CLI Bridge!\n\n"
        f"• Active Model: `{session['model']}`\n"
        f"• Effort Level: `{session['effort']}`\n\n"
        "Commands:\n"
        "• /model - Select AI model\n"
        "• /effort or /thinking - Select reasoning effort (low/medium/high)\n"
        "• /new or /reset - Start a fresh conversation session",
        parse_mode="Markdown"
    )

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return
    
    chat_id = update.effective_chat.id
    session = get_session(chat_id)
    session["has_active_session"] = False
    await update.message.reply_text("🔄 Started a new session! Your next message will begin a fresh conversation.")

async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"set_model:{model_id}")]
        for name, model_id in AVAILABLE_MODELS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    session = get_session(update.effective_chat.id)
    await update.message.reply_text(
        f"🧠 Select AI Model (Current: `{session['model']}`):",
        reply_markup=reply_markup
    )

async def effort_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"set_effort:{effort_id}")]
        for name, effort_id in EFFORT_LEVELS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    session = get_session(update.effective_chat.id)
    await update.message.reply_text(
        f"⚡ Select Reasoning Effort (Current: `{session['effort']}`):",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    user_text = update.message.text
    if not user_text:
        return

    chat_id = update.effective_chat.id
    session = get_session(chat_id)

    status_msg = await update.message.reply_text("⏳ Thinking...")

    # Build command list
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
        
        # Mark session as active so subsequent messages continue context
        session["has_active_session"] = True

        # Delete status message
        try:
            await status_msg.delete()
        except Exception:
            pass

        # Split output if exceeding Telegram message size limit (4096 chars)
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
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("new", new_session))
    app.add_handler(CommandHandler("reset", new_session))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("effort", effort_command))
    app.add_handler(CommandHandler("thinking", effort_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    logging.info("Starting Persistent Telegram Antigravity Bridge Bot with Model & Effort controls...")
    app.run_polling()

if __name__ == "__main__":
    main()
