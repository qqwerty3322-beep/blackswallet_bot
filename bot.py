import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN  = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_СЮДА")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://ВАШ_ДОМЕН.vercel.app")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, *args): pass

# Запускаем health‑сервер в отдельном потоке (только один)
def run_health():
    server = HTTPServer(('0.0.0.0', 10000), Health)
    server.serve_forever()

threading.Thread(target=run_health, daemon=True).start()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    name = user.first_name or "there"
    keyboard = [[InlineKeyboardButton(text="🚀 Open BlackS Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        f"👋 Hey, {name}!\n\n<b>BlackS Wallet</b> — your crypto wallet in Telegram.\n\n• Staking, Swap, Portfolio\n• Referral rewards\n• Instant deposits & withdrawals\n\nTap the button below to open your wallet 👇",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ <b>BlackS Wallet Help</b>\n\n/start — Open your wallet\n/wallet — Open your wallet\n/support — Contact support\n\nNeed help? → @BlackSectorHelp",
        parse_mode="HTML"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💬 <b>Support</b>\n\nIf you have any issues with deposits or your account, contact us:\n\n→ @BlackSectorHelp\n\nPlease include your email and a description of the issue.",
        parse_mode="HTML"
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("Open Wallet 🚀", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "Use /start to open your wallet.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("wallet",  wallet))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    logger.info("Bot started. Polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
