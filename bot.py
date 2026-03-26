"""
BlackS Wallet — Telegram Bot
Запуск: python bot.py
Деплой: Railway / Render

Установка зависимостей:
  pip install python-telegram-bot==20.7
"""

import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Health check сервер (для Railway/Render) ──────────────
class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args): pass

threading.Thread(
    target=lambda: HTTPServer(('0.0.0.0', int(os.getenv('PORT', 10000))), Health).serve_forever(),
    daemon=True
).start()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ── Конфиг ────────────────────────────────────────────────
BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ── /start ────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    user = update.effective_user
    name = user.first_name if user and user.first_name else "there"

    keyboard = [[
        InlineKeyboardButton(
            text="🚀 Open BlackS Wallet",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"👋 Hey, {name}!\n\n"
        f"<b>BlackS Wallet</b> — your crypto wallet in Telegram.\n\n"
        f"• Staking, Swap, Portfolio\n"
        f"• Referral rewards\n"
        f"• Instant deposits & withdrawals\n\n"
        f"Tap the button below to open your wallet 👇",
        parse_mode="HTML",
        reply_markup=reply_markup
    )


# ── /wallet — same as start ───────────────────────────────
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


# ── /help ─────────────────────────────────────────────────
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "ℹ️ <b>BlackS Wallet Help</b>\n\n"
        "/start — Open your wallet\n"
        "/wallet — Open your wallet\n"
        "/support — Contact support\n\n"
        "Need help? → @BlackSectorHelp",
        parse_mode="HTML"
    )


# ── /support ──────────────────────────────────────────────
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        "💬 <b>Support</b>\n\n"
        "If you have any issues with deposits or your account, contact us:\n\n"
        "→ @BlackSectorHelp\n\n"
        "Please include your email and a description of the issue.",
        parse_mode="HTML"
    )


# ── Unknown command ───────────────────────────────────────
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    keyboard = [[
        InlineKeyboardButton("Open Wallet 🚀", web_app=WebAppInfo(url=WEBAPP_URL))
    ]]
    await update.message.reply_text(
        "Use /start to open your wallet.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Main ──────────────────────────────────────────────────
def main() -> None:
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is not set! Set it as an environment variable.")
        return
    if not WEBAPP_URL:
        logger.error("WEBAPP_URL is not set! Set it as an environment variable.")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("wallet",  wallet))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info(f"Bot started. WEBAPP_URL: {WEBAPP_URL}")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True   # игнорировать накопившиеся апдейты при рестарте
    )


if __name__ == "__main__":
    main()
