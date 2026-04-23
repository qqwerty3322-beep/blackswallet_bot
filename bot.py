"""
BlackS Wallet — Telegram Bot + Supabase Proxy
Деплой: Render (Web Service)

Зависимости:
  pip install python-telegram-bot==20.7
"""

import os
import json
import logging
import asyncio
import threading
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
PORT       = int(os.getenv("PORT", 10000))

# Supabase credentials (proxied — clients don't need direct access)
SB_URL = "https://rvduytgfwtyytodyxmfo.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ2ZHV5dGdmd3R5eXRvZHl4bWZvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM4MzMwNjEsImV4cCI6MjA4OTQwOTA2MX0.OHS7vQwJlicKmTiONLzjG7-N18tz_-_rZmSADuocLfA"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

_app  = None
_loop = None


# ══════════════════════════════════════════════════════════
# HTTP HANDLER
# ══════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):

    # ── CORS preflight ────────────────────────────────────
    def do_OPTIONS(self):
        self._cors(200)

    # ── GET ───────────────────────────────────────────────
    def do_GET(self):
        p = self.path
        if p.startswith('/proxy'):
            self._sb_proxy_get()
        elif p.startswith('/get_chat'):
            self._get_chat()
        else:
            self.send_response(200)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b'BlackS Wallet Bot OK')

    # ── POST ──────────────────────────────────────────────
    def do_POST(self):
        p = self.path
        if p.startswith('/proxy'):
            self._sb_proxy_post()
        elif p == '/send_otp':
            self._send_to_user('otp')
        elif p == '/send_message':
            self._send_to_user('message')
        else:
            self.send_response(404)
            self.end_headers()

    # ── PATCH ─────────────────────────────────────────────
    def do_PATCH(self):
        if self.path.startswith('/proxy'):
            self._sb_proxy_patch()
        else:
            self.send_response(404)
            self.end_headers()

    # ── DELETE ────────────────────────────────────────────
    def do_DELETE(self):
        if self.path.startswith('/proxy'):
            self._sb_proxy_delete()
        else:
            self.send_response(404)
            self.end_headers()

    # ══════════════════════════════════════════════════════
    # SUPABASE PROXY
    # Принимает запросы вида:
    #   GET    /proxy/tablename?filter=...
    #   POST   /proxy/tablename          body: JSON
    #   PATCH  /proxy/tablename?filter=  body: JSON
    #   DELETE /proxy/tablename?filter=
    # ══════════════════════════════════════════════════════
    def _sb_path(self):
        """Extract /proxy/TABLE?QUERY → TABLE, QUERY"""
        parsed = urllib.parse.urlparse(self.path)
        table  = parsed.path.replace('/proxy/', '').split('/')[0]
        query  = parsed.query
        return table, query

    def _sb_headers(self, extra=None):
        h = {
            'apikey':        SB_KEY,
            'Authorization': 'Bearer ' + SB_KEY,
            'Content-Type':  'application/json',
            'Prefer':        'return=representation',
        }
        if extra:
            h.update(extra)
        return h

    def _sb_proxy_get(self):
        table, query = self._sb_path()
        url = f"{SB_URL}/rest/v1/{table}?{query}"
        try:
            req  = urllib.request.Request(url, headers=self._sb_headers())
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
            self._raw(200, body)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read())
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _sb_proxy_post(self):
        table, query = self._sb_path()
        url  = f"{SB_URL}/rest/v1/{table}"
        if query:
            url += '?' + query
        body = self._read_body()
        try:
            req  = urllib.request.Request(url, data=body, headers=self._sb_headers(
                {'Prefer': 'resolution=merge-duplicates,return=representation'}
            ), method='POST')
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = r.read()
            self._raw(200, resp)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read())
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _sb_proxy_patch(self):
        table, query = self._sb_path()
        url  = f"{SB_URL}/rest/v1/{table}?{query}"
        body = self._read_body()
        try:
            req  = urllib.request.Request(url, data=body, headers=self._sb_headers(), method='PATCH')
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = r.read()
            self._raw(200, resp)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read())
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _sb_proxy_delete(self):
        table, query = self._sb_path()
        url = f"{SB_URL}/rest/v1/{table}?{query}"
        try:
            req = urllib.request.Request(url, headers=self._sb_headers(), method='DELETE')
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = r.read()
            self._raw(200, resp)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read())
        except Exception as e:
            self._json(500, {'error': str(e)})

    # ── Telegram: get_chat ────────────────────────────────
    def _get_chat(self):
        try:
            qs     = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            tg_id  = params.get('tg_id', [None])[0]
            if not tg_id:
                self._json(400, {'ok': False, 'error': 'tg_id required'})
                return
            url = f'https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id={tg_id}'
            with urllib.request.urlopen(url, timeout=8) as r:
                data = r.read()
            self._raw(200, data)
        except Exception as e:
            self._json(500, {'ok': False, 'error': str(e)})

    # ── Telegram: send OTP / message ─────────────────────
    def _send_to_user(self, mode):
        try:
            data  = json.loads(self._read_body())
            tg_id = data.get('tg_id')
            if not tg_id:
                self._json(400, {'ok': False, 'error': 'tg_id required'})
                return

            if mode == 'otp':
                code = data.get('code')
                if not code:
                    self._json(400, {'ok': False, 'error': 'code required'})
                    return
                text = (
                    f"🔐 <b>BlackS Wallet — Verification Code</b>\n\n"
                    f"Your code: <code>{code}</code>\n\n"
                    f"Valid for <b>10 minutes</b>.\n"
                    f"Never share this code with anyone."
                )
            else:
                text = data.get('text', '').strip()
                if not text:
                    self._json(400, {'ok': False, 'error': 'text required'})
                    return

            if _app and _loop:
                future = asyncio.run_coroutine_threadsafe(
                    _app.bot.send_message(chat_id=int(tg_id), text=text, parse_mode='HTML'),
                    _loop
                )
                future.result(timeout=10)
                self._json(200, {'ok': True})
            else:
                self._json(503, {'ok': False, 'error': 'Bot not ready'})

        except Exception as e:
            self._json(500, {'ok': False, 'error': str(e)})

    # ── Helpers ───────────────────────────────────────────
    def _read_body(self):
        n = int(self.headers.get('Content-Length', 0))
        return self.rfile.read(n)

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PATCH, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, apikey, Authorization, Prefer')

    def _cors(self, status):
        self.send_response(status)
        self._cors_headers()
        self.end_headers()

    def _raw(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', len(body))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self._raw(status, body)

    def log_message(self, *args): pass


# ══════════════════════════════════════════════════════════
# BOT HANDLERS
# ══════════════════════════════════════════════════════════
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


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
def main():
    global _app, _loop

    if not BOT_TOKEN: logger.error("BOT_TOKEN not set!"); return
    if not WEBAPP_URL: logger.error("WEBAPP_URL not set!"); return

    threading.Thread(
        target=lambda: HTTPServer(('0.0.0.0', PORT), Handler).serve_forever(),
        daemon=True
    ).start()
    logger.info(f"HTTP server on port {PORT} (with Supabase proxy at /proxy/)")

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
