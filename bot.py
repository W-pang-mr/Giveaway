import asyncio
import html
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


BOT_TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "wallet_watch.sqlite3")
TONAPI_BASE = os.environ.get("TONAPI_BASE", "https://tonapi.io")
TONAPI_TOKEN = os.environ.get("TONAPI_TOKEN", "")
POLL_SECONDS = max(10, int(os.environ.get("POLL_SECONDS", "20")))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN secret is required")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("ton-wallet-watch")


db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA busy_timeout=30000")
db.execute(
    """CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        watch_address TEXT,
        notifications_enabled INTEGER NOT NULL DEFAULT 1,
        baseline_pending INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL
    )"""
)
db.execute(
    """CREATE TABLE IF NOT EXISTS seen_events (
        user_id INTEGER NOT NULL,
        event_id TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        PRIMARY KEY (user_id, event_id)
    )"""
)
db.commit()
db_lock = asyncio.Lock()

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddressState(StatesGroup):
    waiting_for_address = State()


def now_ts():
    return int(time.time())


def normalize_address(value: str):
    return re.sub(r"\\s+", "", (value or "").strip())


def looks_like_ton_address(value: str):
    if not value or len(value) < 40 or len(value) > 70:
        return False
    if value.startswith(("EQ", "UQ", "Ef", "Uf")):
        return bool(re.fullmatch(r"[A-Za-z0-9_-]+", value))
    if value.startswith("0:"):
        return bool(re.fullmatch(r"0:[0-9a-fA-F]{64}", value))
    return False


async def api_get(path: str):
    endpoint = f"{TONAPI_BASE.rstrip('/')}{path}"

    def request_json():
        headers = {"Accept": "application/json", "User-Agent": "TON-Wallet-Watch/1.0"}
        if TONAPI_TOKEN:
            headers["Authorization"] = f"Bearer {TONAPI_TOKEN}"
        request = Request(endpoint, headers=headers)
        with urlopen(request, timeout=12) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        return await asyncio.to_thread(request_json)
    except Exception as exc:
        logger.warning("TON API request failed for %s: %s", path, exc)
        return None


async def resolve_address(raw_address: str):
    if not looks_like_ton_address(raw_address):
        return None
    payload = await api_get(f"/v2/accounts/{quote(raw_address, safe='')}")
    if not payload:
        return None
    return payload.get("address") or raw_address


async def get_user(user_id: int):
    async with db_lock:
        return db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


async def ensure_user(user_id: int):
    async with db_lock:
        db.execute(
            "INSERT OR IGNORE INTO users (user_id, updated_at) VALUES (?, ?)",
            (user_id, now_ts()),
        )
        db.commit()


async def save_address(user_id: int, address: str):
    async with db_lock:
        db.execute("DELETE FROM seen_events WHERE user_id = ?", (user_id,))
        db.execute(
            """INSERT INTO users (user_id, watch_address, notifications_enabled, baseline_pending, updated_at)
               VALUES (?, ?, 1, 1, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 watch_address = excluded.watch_address,
                 notifications_enabled = 1,
                 baseline_pending = 1,
                 updated_at = excluded.updated_at""",
            (user_id, address, now_ts()),
        )
        db.commit()


async def set_notifications(user_id: int, enabled: bool):
    async with db_lock:
        db.execute(
            "UPDATE users SET notifications_enabled = ?, updated_at = ? WHERE user_id = ?",
            (1 if enabled else 0, now_ts(), user_id),
        )
        db.commit()


async def mark_baseline_ready(user_id: int):
    async with db_lock:
        db.execute(
            "UPDATE users SET baseline_pending = 0, updated_at = ? WHERE user_id = ?",
            (now_ts(), user_id),
        )
        db.commit()


async def add_seen_event(user_id: int, event_id: str):
    async with db_lock:
        cursor = db.execute(
            "INSERT OR IGNORE INTO seen_events (user_id, event_id, created_at) VALUES (?, ?, ?)",
            (user_id, event_id, now_ts()),
        )
        db.commit()
        return cursor.rowcount == 1


async def seed_events(user_id: int, events):
    async with db_lock:
        for event in events:
            event_id = event_identifier(event)
            if event_id:
                db.execute(
                    "INSERT OR IGNORE INTO seen_events (user_id, event_id, created_at) VALUES (?, ?, ?)",
                    (user_id, event_id, now_ts()),
                )
        db.execute(
            "UPDATE users SET baseline_pending = 0, updated_at = ? WHERE user_id = ?",
            (now_ts(), user_id),
        )
        db.commit()


async def watchers():
    async with db_lock:
        return db.execute(
            """SELECT user_id, watch_address, notifications_enabled, baseline_pending
               FROM users WHERE watch_address IS NOT NULL AND watch_address != ''"""
        ).fetchall()


def event_identifier(event):
    return str(event.get("event_id") or event.get("lt") or event.get("hash") or "")


def address_from(value):
    if isinstance(value, dict):
        return str(value.get("address") or "")
    return str(value or "")


def amount_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def parse_action(action, watched_address):
    action_type = action.get("type") or ""
    if action_type == "TonTransfer" or action.get("TonTransfer"):
        transfer = action.get("TonTransfer") or action.get("ton_transfer") or {}
        decimals = 9
        symbol = "TON"
    elif action_type == "JettonTransfer" or action.get("JettonTransfer"):
        transfer = action.get("JettonTransfer") or action.get("jetton_transfer") or {}
        jetton = transfer.get("jetton") or {}
        decimals = int(jetton.get("decimals") or 9)
        symbol = str(jetton.get("symbol") or jetton.get("name") or "JETTON")
    else:
        return None

    sender = address_from(transfer.get("sender"))
    recipient = address_from(transfer.get("recipient"))
    watched = watched_address.lower()
    if recipient.lower() == watched:
        direction = "deposit"
    elif sender.lower() == watched:
        direction = "withdrawal"
    else:
        return None

    raw_amount = amount_number(transfer.get("amount"))
    amount = raw_amount / (10 ** decimals)
    return {
        "direction": direction,
        "amount": amount,
        "symbol": symbol,
        "sender": sender,
        "recipient": recipient,
        "comment": str(transfer.get("comment") or "").strip(),
    }


def event_transfer(event, watched_address):
    for action in event.get("actions") or []:
        transfer = parse_action(action, watched_address)
        if transfer:
            return transfer
    return None


def format_amount(value):
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    return f"{value:,.9f}".rstrip("0").rstrip(".")


def event_time(event):
    timestamp = event.get("timestamp")
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OSError):
        return "زمان نامشخص"


def explorer_url(event):
    identifier = event_identifier(event)
    return f"https://tonviewer.com/transaction/{quote(identifier, safe='')}" if identifier else "https://tonviewer.com"


def notification_text(event, transfer):
    if transfer["direction"] == "deposit":
        title = "📥 واریز جدید"
        detail = f"از: <code>{html.escape(transfer['sender'])}</code>"
    else:
        title = "📤 برداشت جدید"
        detail = f"به: <code>{html.escape(transfer['recipient'])}</code>"
    comment = f"\\n📝 یادداشت: {html.escape(transfer['comment'])}" if transfer["comment"] else ""
    return (
        f"{title}\\n\\n"
        f"💰 مقدار: <b>{format_amount(transfer['amount'])} {html.escape(transfer['symbol'])}</b>\\n"
        f"{detail}\\n"
        f"🕒 {event_time(event)}{comment}"
    )


def notification_keyboard(event):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔎 مشاهده تراکنش", url=explorer_url(event))
    ]])


def menu_keyboard(row):
    address = row["watch_address"] if row else None
    enabled = bool(row["notifications_enabled"]) if row else False
    status = "روشن ✅" if enabled else "خاموش ❌"
    rows = [
        [InlineKeyboardButton(text="📍 تنظیم آدرس کیف‌پول", callback_data="set_address")],
        [InlineKeyboardButton(text=f"🔔 اعلان‌ها: {status}", callback_data="toggle_notifications")],
    ]
    if address:
        rows.append([InlineKeyboardButton(text="👁 نمایش آدرس فعلی", callback_data="show_address")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


WELCOME_TEXT = (
    "👋 <b>ربات مانیتور کیف‌پول TON</b>\\n\\n"
    "این ربات آدرس عمومی TON را بررسی می‌کند و وقتی تراکنش جدیدی ثبت شود، "
    "واریز یا برداشت را برایت اعلان می‌کند.\\n\\n"
    "🔐 کلید خصوصی یا عبارت بازیابی لازم نیست و نباید آن‌ها را برای ربات بفرستی.\\n"
    "📡 ربات فقط اطلاعات عمومی بلاک‌چین را می‌خواند."
)


@dp.message(CommandStart())
async def start_handler(message: types.Message):
    await ensure_user(message.from_user.id)
    row = await get_user(message.from_user.id)
    await message.answer(WELCOME_TEXT, parse_mode="HTML", reply_markup=menu_keyboard(row))


@dp.callback_query(F.data == "set_address")
async def set_address_button(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AddressState.waiting_for_address)
    await callback.message.answer(
        "📍 آدرس عمومی TON را بفرست.\\n\\n"
        "نمونه: <code>EQ...</code> یا <code>UQ...</code>\\n"
        "عبارت بازیابی و کلید خصوصی را هرگز ارسال نکن.",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(AddressState.waiting_for_address)
async def receive_address(message: types.Message, state: FSMContext):
    raw_address = normalize_address(message.text or "")
    if not looks_like_ton_address(raw_address):
        await message.answer("⚠️ این آدرس شبیه آدرس معتبر TON نیست. آدرس EQ، UQ یا raw را کامل بفرست.")
        return

    await message.answer("⏳ در حال بررسی آدرس روی شبکه TON...")
    canonical = await resolve_address(raw_address)
    if not canonical:
        await message.answer("❌ آدرس تأیید نشد یا TON API موقتاً در دسترس نیست. آدرس را دوباره بررسی کن.")
        return

    await save_address(message.from_user.id, canonical)
    events = await api_get(f"/v2/accounts/{quote(canonical, safe='')}/events?limit=50")
    if events and isinstance(events.get("events"), list):
        await seed_events(message.from_user.id, events["events"])
    await state.clear()
    row = await get_user(message.from_user.id)
    await message.answer(
        "✅ آدرس با موفقیت ثبت شد.\\n"
        "تراکنش‌های قبلی اعلان نمی‌شوند؛ فقط تراکنش‌های جدید را می‌فرستم.",
        reply_markup=menu_keyboard(row),
    )


@dp.callback_query(F.data == "toggle_notifications")
async def toggle_notifications(callback: types.CallbackQuery):
    await ensure_user(callback.from_user.id)
    row = await get_user(callback.from_user.id)
    if not row or not row["watch_address"]:
        await callback.answer("اول یک آدرس کیف‌پول تنظیم کن.", show_alert=True)
        return
    enabled = not bool(row["notifications_enabled"])
    await set_notifications(callback.from_user.id, enabled)
    row = await get_user(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=menu_keyboard(row))
    await callback.answer("اعلان‌ها روشن شد ✅" if enabled else "اعلان‌ها خاموش شد ❌")


@dp.callback_query(F.data == "show_address")
async def show_address(callback: types.CallbackQuery):
    row = await get_user(callback.from_user.id)
    if not row or not row["watch_address"]:
        await callback.answer("هنوز آدرسی تنظیم نشده است.", show_alert=True)
        return
    await callback.message.answer(
        f"📍 آدرس تحت نظر:\\n<code>{html.escape(row['watch_address'])}</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(Command("status"))
async def status_handler(message: types.Message):
    row = await get_user(message.from_user.id)
    if not row or not row["watch_address"]:
        await message.answer("هنوز آدرسی تنظیم نشده است.", reply_markup=menu_keyboard(row))
        return
    status = "روشن ✅" if row["notifications_enabled"] else "خاموش ❌"
    await message.answer(
        f"📍 آدرس: <code>{html.escape(row['watch_address'])}</code>\\n🔔 اعلان‌ها: {status}",
        parse_mode="HTML",
        reply_markup=menu_keyboard(row),
    )


async def poll_wallets():
    while True:
        try:
            rows = await watchers()
            for row in rows:
                if not row["notifications_enabled"]:
                    continue
                payload = await api_get(
                    f"/v2/accounts/{quote(row['watch_address'], safe='')}/events?limit=50"
                )
                if not payload or not isinstance(payload.get("events"), list):
                    continue
                events = payload["events"]
                if row["baseline_pending"]:
                    await seed_events(row["user_id"], events)
                    continue
                for event in reversed(events):
                    event_id = event_identifier(event)
                    if not event_id or not await add_seen_event(row["user_id"], event_id):
                        continue
                    transfer = event_transfer(event, row["watch_address"])
                    if not transfer:
                        continue
                    try:
                        await bot.send_message(
                            row["user_id"],
                            notification_text(event, transfer),
                            parse_mode="HTML",
                            reply_markup=notification_keyboard(event),
                        )
                    except Exception as exc:
                        logger.warning("Could not notify user %s: %s", row["user_id"], exc)
        except Exception:
            logger.exception("Wallet polling cycle failed")
        await asyncio.sleep(POLL_SECONDS)


async def main():
    logger.info("Starting TON wallet watcher; polling every %ss", POLL_SECONDS)
    poller = asyncio.create_task(poll_wallets())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        poller.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
