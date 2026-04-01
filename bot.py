"""
BlackS Wallet — Telegram Bot
Деплой: Render (Web Service)

Зависимости:
  pip install python-telegram-bot==20.7
"""

import os
import json
import logging
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
PORT       = int(os.getenv("PORT", 10000))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

_app = None
_loop = None


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        if self.path == '/send_otp':
            self._send_otp()
        elif self.path == '/send_message':
            self._send_message()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith('/get_chat'):
            self._get_chat()
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'BlackS Wallet Bot OK')

    def _get_chat(self):
        import urllib.parse, urllib.request
        try:
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            tg_id = params.get('tg_id', [None])[0]
            if not tg_id:
                self._json(400, {'ok': False, 'error': 'tg_id required'})
                return
            token = BOT_TOKEN
            url = f'https://api.telegram.org/bot{token}/getChat?chat_id={tg_id}'
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json(500, {'ok': False, 'error': str(e)})

    def _send_otp(self):
        self._send_to_user('otp')

    def _send_message(self):
        self._send_to_user('message')

    def _send_to_user(self, mode):
        try:
            n    = int(self.headers.get('Content-Length', 0))
            data = json.loads(self.rfile.read(n))
            tg_id = data.get('tg_id')

            if not tg_id:
                self._json(400, {'ok': False, 'error': 'tg_id required'})
                return

            if mode == 'otp':
                code  = data.get('code')
                email = data.get('email', '')
                if not code:
                    self._json(400, {'ok': False, 'error': 'code required'})
                    return
                text = (
                    f"🔐 <b>BlackS Wallet — Verification Code</b>\n\n"
                    f"Your code: <code>{code}</code>\n\n"
                    f"Valid for <b>10 minutes</b>.\n"
                    f"Never share this code with anyone."
                )
                log_msg = f"OTP {code} sent to {tg_id} ({email})"
            else:
                text = data.get('text', '').strip()
                if not text:
                    self._json(400, {'ok': False, 'error': 'text required'})
                    return
                log_msg = f"Message sent to {tg_id}"

            if _app and _loop:
                future = asyncio.run_coroutine_threadsafe(
                    _app.bot.send_message(
                        chat_id=int(tg_id),
                        text=text,
                        parse_mode='HTML'
                    ),
                    _loop
                )
                future.result(timeout=10)
                logger.info(log_msg)
                self._json(200, {'ok': True})
            else:
                self._json(503, {'ok': False, 'error': 'Bot not ready'})

        except Exception as e:
            logger.error(f"send error: {e}")
            self._json(500, {'ok': False, 'error': str(e)})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args): pass


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    name = update.effective_user.first_name or "there"
    kb = [[InlineKeyboardButton("🚀 Open BlackS Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        f"👋 Hey, {name}!\n\n<b>BlackS Wallet</b> — your crypto wallet in Telegram.\n\n"
        "• Staking, Swap, Portfolio\n• Referral rewards\n• Instant deposits & withdrawals\n\n"
        "Tap the button below to open your wallet 👇",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def wallet(update, context): await start(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text(
        "ℹ️ <b>BlackS Wallet Help</b>\n\n/start — Open wallet\n/support — Contact support\n\n→ @BlackSWalletHelp",
        parse_mode="HTML"
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text(
        "💬 <b>Support</b>\n\nContact us: @BlackSWalletHelp\n\nInclude your email and issue description.",
        parse_mode="HTML"
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    kb = [[InlineKeyboardButton("Open Wallet 🚀", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text("Use /start to open your wallet.", reply_markup=InlineKeyboardMarkup(kb))


def main():
    global _app, _loop

    if not BOT_TOKEN: logger.error("BOT_TOKEN not set!"); return
    if not WEBAPP_URL: logger.error("WEBAPP_URL not set!"); return

    # Start HTTP server (health + OTP endpoint)
    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', PORT), Handler).serve_forever(),
        daemon=True
    ).start()
    logger.info(f"HTTP server on port {PORT}")

    _app = Application.builder().token(BOT_TOKEN).build()

    _app.add_handler(CommandHandler("start",   start))
    _app.add_handler(CommandHandler("wallet",  wallet))
    _app.add_handler(CommandHandler("help",    help_cmd))
    _app.add_handler(CommandHandler("support", support))
    _app.add_handler(MessageHandler(filters.COMMAND, unknown))

    _loop = asyncio.get_event_loop()

    logger.info(f"Bot started. WEBAPP_URL={WEBAPP_URL}")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
