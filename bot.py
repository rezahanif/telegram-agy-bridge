import logging
import os
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8840249393:AAG05JSY4igVc7-7Sdo3qfuhZumeJMRZ7RU")
ALLOWED_USER_ID = int(os.getenv("TELEGRAM_ALLOWED_USER_ID", "1179211752"))
AGY_PATH = os.getenv("AGY_PATH", "/home/rezaserver/.local/bin/agy")
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/home/rezaserver")

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Track active session states per chat
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return
    await update.message.reply_text(
        "🚀 Connected to Antigravity CLI Bridge!\n\n"
        "• Send any prompt to chat (persists conversation context).\n"
        "• Use /new or /reset to start a fresh conversation session."
    )

async def new_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return
    
    chat_id = update.effective_chat.id
    user_sessions[chat_id] = False
    await update.message.reply_text("🔄 Started a new session! Your next message will begin a fresh conversation.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        await update.message.reply_text("Unauthorized access.")
        return

    user_text = update.message.text
    if not user_text:
        return

    chat_id = update.effective_chat.id
    has_active_session = user_sessions.get(chat_id, False)

    status_msg = await update.message.reply_text("⏳ Thinking...")

    # Build command list
    cmd = [AGY_PATH]
    if has_active_session:
        cmd.extend(["--continue", "-p", user_text])
    else:
        cmd.extend(["-p", user_text])

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
        user_sessions[chat_id] = True

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
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    logging.info("Starting Persistent Telegram Antigravity Bridge Bot...")
    app.run_polling()

if __name__ == "__main__":
    main()
