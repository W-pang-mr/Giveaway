# ==========================================
# Void Giveaway Bot - Version 6.1.0 (Fully Automatic TON Withdrawals)
# (Multi-Channel Forced Join, Live Wallet Tracker, Direct Admin DM, Ban System, MongoDB Integrated)
# ==========================================

import asyncio
import base64
import os
import logging
import html
import math
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

import motor.motor_asyncio
from pytoniq import LiteClient, WalletV5R1
from pymongo import ReturnDocument

logging.basicConfig(level=logging.INFO)
logging.getLogger("pytoniq").setLevel(logging.WARNING)
logging.getLogger("LiteClient").setLevel(logging.WARNING)

app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ Void Giveaway Bot (v6.1.0) is running smoothly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6879499219]
BOT_VERSION = "6.1.0"
WITHDRAW_CHANNEL = "@voidwithraw"
WALLET_TRACKER_CHANNEL = "@Voidchanneloffical"  # کانال ارسال و بروزرسانی خودکار موجودی ولت سیستم
TON_MNEMONIC = os.environ.get("TON_MNEMONIC")

# تنظیمات اتصال به MongoDB
MONGO_URI = os.environ.get("MONGO_URI", "")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client['void_giveaway_db']

users_col = db['users']
settings_col = db['settings']
withdrawals_col = db['withdrawals']
deposits_col = db['deposits']
transfers_col = db['transfers']

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_data = {}
all_time_users = set()
banned_users = set()
required_channels = ["@Voidchanneloffical"]  # پشتیبانی از چند کانال جوین اجباری

bot_active = True
withdrawals_enabled = True
min_withdraw_amount = 0.1
max_withdraw_amount = 10.0
min_deposit_amount = 0.01
ton_gas_fee = 0.005
tracker_message_id = None
system_wallet_address = None
# ارسال‌های TON باید پشت‌سرهم انجام شوند تا چند برداشت هم‌زمان از یک موجودی عبور نکند.
payout_lock = asyncio.Lock()
# کل مسیر رزرو/بازگشت موجودی و برداشت در یک پردازش سریالی انجام می‌شود.
withdrawal_flow_lock = asyncio.Lock()

# ==========================================
# استعلام موجودی ولت سیستم
# ==========================================
async def get_system_wallet_balance():
    if not TON_MNEMONIC:
        return None, "کلید امنیتی ولت (TON_MNEMONIC) تنظیم نشده است!"
    
    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()

        mnemonics = TON_MNEMONIC.strip().split()
        wallet = await WalletV5R1.from_mnemonic(client, mnemonics, network_global_id=-239)

        account_state = await client.get_account_state(wallet.address)
        balance_nano = account_state.balance
        balance_ton = balance_nano / 10**9

        await close_lite_client(client)
        client = None
        return balance_ton, wallet.address.to_str(is_user_friendly=True, is_bounceable=False)
    except Exception as e:
        logging.error(f"Error fetching wallet balance: {e}")
        await close_lite_client(client)
        return None, str(e)

# ==========================================
# تابع تراکر خودکار موجودی ولت هر ۳ دقیقه
# ==========================================
async def wallet_balance_tracker_loop():
    global tracker_message_id
    await asyncio.sleep(5)  # تاخیر اولیه جهت اجرای کامل لود دیتابیس
    
    while True:
        try:
            balance_ton, wallet_addr = await get_system_wallet_balance()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            if balance_ton is not None:
                text = (
                    f"💎 <b>گزارش لحظه‌ای موجودی ولت اصلی سیستم</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>موجودی موجود:</b> <code>{balance_ton:.4f} TON</code> 💎\n"
                    f"💳 <b>آدرس ولت:</b>\n<code>{wallet_addr}</code>\n\n"
                    f"⏰ <b>آخرین بروزرسانی:</b> {now_str}\n"
                    f"🔄 <i>بروزرسانی خودکار هر ۳ دقیقه انجام می‌شود.</i>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                text = (
                    f"⚠️ <b>خطا در دریافت موجودی ولت سیستم!</b>\n"
                    f"علت: {wallet_addr}\n\n"
                    f"⏰ <b>زمان:</b> {now_str}"
                )

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🚀 استارت ربات و دریافت هدیه", url=f"https://t.me/{(await bot.get_me()).username}")]
                ]
            )

            if tracker_message_id is None:
                try:
                    sent_msg = await bot.send_message(chat_id=WALLET_TRACKER_CHANNEL, text=text, parse_mode="HTML", reply_markup=kb)
                    tracker_message_id = sent_msg.message_id
                    await save_data()
                except Exception as e:
                    logging.error(f"Error sending tracker msg to channel: {e}")
            else:
                try:
                    await bot.edit_message_text(chat_id=WALLET_TRACKER_CHANNEL, message_id=tracker_message_id, text=text, parse_mode="HTML", reply_markup=kb)
                except TelegramBadRequest:
                    pass
                except Exception as e:
                    logging.error(f"Error editing tracker msg: {e}")
                    try:
                        sent_msg = await bot.send_message(chat_id=WALLET_TRACKER_CHANNEL, text=text, parse_mode="HTML", reply_markup=kb)
                        tracker_message_id = sent_msg.message_id
                        await save_data()
                    except Exception as ex:
                        logging.error(f"Error resending tracker msg: {ex}")

        except Exception as e:
            logging.error(f"Wallet tracker loop exception: {e}")

        await asyncio.sleep(180)  # بروزرسانی هر ۳ دقیقه (۱۸۰ ثانیه)

# ==========================================
# تابع بررسی اکانت واقعی
# ==========================================
# ==========================================
# تابع بررسی عضویت اجباری (چندکاناله)
# ==========================================
async def check_user_subscription(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    for ch in required_channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status not in ["creator", "administrator", "member"]:
                return False
        except Exception as e:
            logging.error(f"Subscription Check Error for {ch}: {e}")
            return False
    return True

def get_join_channel_keyboard():
    buttons = []
    for idx, ch in enumerate(required_channels, 1):
        clean_ch = ch.replace("@", "")
        buttons.append([InlineKeyboardButton(text=f"📢 عضویت در کانال {idx} ({ch})", url=f"https://t.me/{clean_ch}")])
    buttons.append([InlineKeyboardButton(text="✅ بررسی عضویت / ورود", callback_data="check_join_btn")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ==========================================
# واریز TON
# ==========================================
async def close_lite_client(client):
    """اتصال TON را در مسیرهای موفق و خطا بدون ایجاد خطای ثانویه می‌بندد."""
    if client is None:
        return
    try:
        if client.is_connected():
            await client.close()
    except Exception as close_error:
        logging.warning(f"LiteClient close warning: {close_error}")


async def send_ton_payout(destination_address: str, amount_ton: float):
    """ارسال امن؛ نتیجه می‌تواند sent، failed یا uncertain باشد."""
    if not TON_MNEMONIC:
        return "failed", "کلید امنیتی ولت (TON_MNEMONIC) تنظیم نشده است!"
    if not is_valid_ton_address(destination_address):
        return "failed", "آدرس کیف‌پول TON معتبر نیست یا checksum آن درست نیست."
    if not math.isfinite(amount_ton) or amount_ton <= 0:
        return "failed", "مبلغ واریز معتبر نیست."

    async with payout_lock:
        system_balance, balance_info = await get_system_wallet_balance()
        required_balance = amount_ton + max(ton_gas_fee, 0)
        if system_balance is None:
            return "failed", f"موجودی ولت ربات قابل بررسی نیست: {balance_info}"
        if system_balance < required_balance:
            return "failed", (
                f"موجودی ولت ربات کافی نمی‌باشد. موجودی فعلی: {system_balance:.4f} TON؛ "
                f"مبلغ موردنیاز با کارمزد: {required_balance:.4f} TON"
            )

        client = None
        transfer_started = False
        try:
            client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await client.connect()
            wallet = await WalletV5R1.from_mnemonic(
                client, TON_MNEMONIC.strip().split(), network_global_id=-239
            )
            seqno_before = await wallet.get_seqno()
            # From this point the network result can be ambiguous; never auto-refund blindly.
            transfer_started = True
            await wallet.transfer(
                destination=destination_address.strip(),
                amount=int(round(amount_ton * 10**9)),
                body="Payout from Void Giveaway Bot 🎉"
            )

            # تغییر seqno یعنی پیام از ولت سیستم ارسال شده؛ بعد از این مرحله refund ممنوع است.
            for _ in range(6):
                await asyncio.sleep(2)
                try:
                    if await wallet.get_seqno() > seqno_before:
                        await close_lite_client(client)
                        client = None
                        return "sent", (
                            f"ارسال {amount_ton:.4f} TON از ولت سیستم تأیید شد؛ "
                            "وضعیت شبکه ممکن است چند ثانیه دیرتر به‌روزرسانی شود."
                        )
                except Exception as confirm_error:
                    logging.warning(f"TON payout confirmation check failed: {confirm_error}")

            await close_lite_client(client)
            client = None
            return "uncertain", "ارسال به شبکه انجام شد اما تأیید نهایی هنوز دریافت نشده است؛ برای جلوگیری از پرداخت دوباره، مبلغ فعلاً رزرو می‌ماند."

        except Exception as e:
            logging.error(f"pytoniq W5 Payout Error: {e}")
            await close_lite_client(client)
            if transfer_started:
                return "uncertain", "نتیجه ارسال به شبکه قطعی نیست؛ مبلغ برای بررسی بیشتر رزرو می‌ماند."
            return "failed", str(e)


async def notify_wallet_issue(amount_ton: float, reason: str, withdrawal_id: str = None):
    """هشدار قابل پیگیری برای ادمین هنگام توقف یا شکست برداشت."""
    request_line = f"\n🆔 <b>شناسه درخواست:</b> <code>{html.escape(str(withdrawal_id))}</code>" if withdrawal_id else ""
    alert = (
        "🚨 <b>هشدار برداشت TON</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>مبلغ موردنیاز:</b> <code>{amount_ton:.4f} TON</code>\n"
        f"⚠️ <b>علت:</b> {html.escape(str(reason))}"
        f"{request_line}\n"
        "لطفاً موجودی ولت سیستم و وضعیت برداشت را بررسی کنید."
    )
    try:
        await bot.send_message(chat_id=WITHDRAW_CHANNEL, text=alert, parse_mode="HTML")
    except Exception as alert_error:
        logging.error(f"Wallet issue alert error: {alert_error}")

# ==========================================
# واریز TON به ولت مرکزی
# ==========================================
def get_object_field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def get_deposit_memo(user_id: int) -> str:
    return f"VG-{int(user_id)}"


async def get_system_wallet_address():
    global system_wallet_address
    if system_wallet_address:
        return system_wallet_address
    _, address_or_error = await get_system_wallet_balance()
    if address_or_error and is_valid_ton_address(address_or_error):
        system_wallet_address = address_or_error
        return system_wallet_address
    return None


def extract_ton_comment(in_msg) -> str:
    try:
        body = get_object_field(in_msg, "body")
        if not body:
            return ""
        parser = body.begin_parse()
        if parser.remaining_bits < 32 or parser.load_uint(32) != 0:
            return ""
        return parser.load_snake_string().strip()
    except Exception as e:
        logging.debug(f"TON comment parse skipped: {e}")
        return ""


async def credit_deposit_transaction(tx_id: str, user_id: int, amount_nano: int, memo: str, lt):
    amount_ton = round(amount_nano / 10**9, 4)
    if amount_ton < min_deposit_amount:
        return False

    now = datetime.utcnow().isoformat()
    session = None
    try:
        session = await mongo_client.start_session()
        async with session.start_transaction():
            existing = await deposits_col.find_one({"tx_id": tx_id}, session=session)
            if existing:
                return False
            await deposits_col.insert_one({
                "tx_id": tx_id,
                "user_id": user_id,
                "memo": memo,
                "amount_nano": amount_nano,
                "amount_ton": amount_ton,
                "lt": lt,
                "status": "credited",
                "created_at": now,
                "credited_at": now
            }, session=session)
            await users_col.update_one(
                {"user_id": user_id},
                {"$inc": {"balance": amount_ton}, "$setOnInsert": {
                    "user_id": user_id, "username": "", "first_name": "User"
                }},
                upsert=True, session=session
            )
    except Exception as e:
        logging.error(f"Deposit credit failed for {tx_id}: {e}")
        return False
    finally:
        if session:
            await session.end_session()

    prof = get_user_profile(user_id)
    prof["balance"] = round(float(prof.get("balance", 0.0)) + amount_ton, 4)
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>واریز شما تأیید شد.</b>\n💎 مبلغ افزوده‌شده: <code>{amount_ton:.4f} TON</code>\n"
            f"💰 موجودی جدید: <code>{prof['balance']:.4f} TON</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    return True


async def scan_incoming_deposits():
    wallet_address = await get_system_wallet_address()
    if not wallet_address:
        return

    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()
        transactions = await client.get_transactions(address=wallet_address, count=50)
        for tx in transactions:
            in_msg = get_object_field(tx, "in_msg")
            info = get_object_field(in_msg, "info")
            value = get_object_field(info, "value")
            amount_nano = get_object_field(value, "grams", get_object_field(info, "value_coins", 0))
            if not amount_nano or int(amount_nano) <= 0:
                continue
            if get_object_field(info, "bounced", False):
                continue
            description = get_object_field(tx, "description")
            if get_object_field(description, "aborted", False):
                continue
            credit_phase = get_object_field(description, "credit_ph")
            credit = get_object_field(credit_phase, "credit")
            credited_nano = get_object_field(credit, "grams")
            if credited_nano:
                amount_nano = credited_nano
            memo = extract_ton_comment(in_msg)
            match = re.fullmatch(r"VG-(\d+)", memo)
            if not match:
                continue
            tx_id = f"{get_object_field(tx, 'account_addr', wallet_address)}:{get_object_field(tx, 'lt', '')}"
            await credit_deposit_transaction(tx_id, int(match.group(1)), int(amount_nano), memo, get_object_field(tx, "lt"))
    except Exception as e:
        logging.error(f"Incoming TON deposit scan failed: {e}")
    finally:
        await close_lite_client(client)


async def deposit_tracker_loop():
    await asyncio.sleep(12)
    while True:
        try:
            await scan_incoming_deposits()
        except Exception as e:
            logging.error(f"Deposit tracker loop exception: {e}")
        await asyncio.sleep(30)


async def withdrawal_recovery_loop():
    """وضعیت‌های پردازش‌نشده را بعد از restart به بررسی شبکه منتقل می‌کند."""
    await asyncio.sleep(60)
    while True:
        try:
            cutoff = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
            async for withdrawal in withdrawals_col.find({
                "status": "processing", "updated_at": {"$lt": cutoff}
            }).limit(100):
                withdrawal_id = withdrawal.get("withdrawal_id")
                updated = await withdrawals_col.find_one_and_update(
                    {"withdrawal_id": withdrawal_id, "status": "processing"},
                    {"$set": {
                        "status": "pending_verification",
                        "last_error": "پردازش پس از restart نیاز به بررسی وضعیت شبکه دارد.",
                        "updated_at": datetime.utcnow().isoformat()
                    }},
                    return_document=ReturnDocument.AFTER
                )
                if updated:
                    await notify_wallet_issue(
                        float(updated.get("amount_to_send", 0)),
                        "پردازش برداشت بیش از حد طول کشید؛ مبلغ تا بررسی وضعیت شبکه رزرو می‌ماند.",
                        withdrawal_id
                    )
        except Exception as e:
            logging.error(f"Withdrawal recovery loop exception: {e}")
        await asyncio.sleep(60)

# ==========================================
# ذخیره و بازیابی دیتابیس MongoDB
# ==========================================
async def save_data():
    try:
        for u_id, info in user_data.items():
            user_doc = {
                "user_id": u_id,
                "balance": info.get("balance", 0.0),
                "username": info.get("username", ""),
                "first_name": info.get("first_name", "User")
            }
            if info.get("started_at"):
                user_doc["started_at"] = info["started_at"]
            await users_col.update_one({"user_id": u_id}, {"$set": user_doc}, upsert=True)

        settings_doc = {
            "setting_id": "global_config",
            "all_time_users": list(all_time_users),
            "banned_users": list(banned_users),
            "required_channels": required_channels,
            "bot_active": bot_active,
            "withdrawals_enabled": withdrawals_enabled,
            "min_withdraw_amount": min_withdraw_amount,
            "max_withdraw_amount": max_withdraw_amount,
            "ton_gas_fee": ton_gas_fee,
            "tracker_message_id": tracker_message_id
        }
        await settings_col.update_one({"setting_id": "global_config"}, {"$set": settings_doc}, upsert=True)

    except Exception as e:
        logging.error(f"Error saving data to MongoDB: {e}")

async def load_data():
    global user_data, all_time_users, banned_users, required_channels, bot_active, withdrawals_enabled, min_withdraw_amount, max_withdraw_amount, ton_gas_fee, tracker_message_id
    try:
        for collection, field in ((users_col, "user_id"), (withdrawals_col, "withdrawal_id"), (deposits_col, "tx_id"), (transfers_col, "transfer_id")):
            try:
                await collection.create_index(field, unique=True)
            except Exception as index_error:
                logging.warning(f"Index setup skipped for {field}: {index_error}")
        settings_doc = await settings_col.find_one({"setting_id": "global_config"})
        if settings_doc:
            all_time_users = set(settings_doc.get("all_time_users", []))
            banned_users = set(settings_doc.get("banned_users", []))
            required_channels = settings_doc.get("required_channels", ["@Voidchanneloffical"])
            bot_active = settings_doc.get("bot_active", True)
            withdrawals_enabled = settings_doc.get("withdrawals_enabled", True)
            min_withdraw_amount = settings_doc.get("min_withdraw_amount", 0.1)
            max_withdraw_amount = settings_doc.get("max_withdraw_amount", 10.0)
            ton_gas_fee = settings_doc.get("ton_gas_fee", 0.005)
            tracker_message_id = settings_doc.get("tracker_message_id", None)

        async for user_doc in users_col.find():
            u_id = int(user_doc["user_id"])
            user_data[u_id] = {
                "balance": round(user_doc.get("balance", 0.0), 4),
                "username": user_doc.get("username", ""),
                "first_name": user_doc.get("first_name", "User"),
                "started_at": user_doc.get("started_at")
            }

    except Exception as e:
        logging.error(f"Error loading data from MongoDB: {e}")

# ==========================================
# FSM States
# ==========================================
class WithdrawForm(StatesGroup):
    amount = State()
    wallet_address = State()

class DepositForm(StatesGroup):
    amount = State()

class AdminBroadcastForm(StatesGroup):
    message = State()

class AdminManageUserForm(StatesGroup):
    user_id = State()
    amount = State()

class AdminSearchUserForm(StatesGroup):
    user_id = State()

class AdminBanUserForm(StatesGroup):
    user_id = State()

class AdminUnbanUserForm(StatesGroup):
    user_id = State()

class AdminDirectMessageForm(StatesGroup):
    user_id = State()
    message = State()

class AdminSetMinWithdrawForm(StatesGroup):
    amount = State()

class AdminSetMaxWithdrawForm(StatesGroup):
    amount = State()

class AdminSetGasFeeForm(StatesGroup):
    amount = State()

class AdminAddChannelForm(StatesGroup):
    channel = State()

class AdminRemoveChannelForm(StatesGroup):
    channel = State()

# ==========================================
# توابع کمکی
# ==========================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_banned(user_id: int) -> bool:
    return user_id in banned_users

def get_user_profile(user_id: int, user_obj: types.User = None):
    if user_id not in user_data:
        user_data[user_id] = {
            "balance": 0.0,
            "username": "",
            "first_name": "User"
        }
    if user_obj:
        user_data[user_id]["username"] = user_obj.username or ""
        user_data[user_id]["first_name"] = user_obj.first_name or "User"
    return user_data[user_id]

def get_main_keyboard(user_id: int):
    kb = [
        [KeyboardButton(text="💎 کیف‌پول من (Wallet)")]
    ]
    if is_admin(user_id):
        kb.insert(0, [KeyboardButton(text="⚙️ پنل مدیریت ادمین 👑")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_inline_keyboard():
    status_btn = "🛑 خاموش کردن ربات" if bot_active else "✅ روشن کردن ربات"
    withdrawals_btn = "🛑 خاموش کردن برداشت‌ها" if withdrawals_enabled else "✅ روشن کردن برداشت‌ها"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن کانال اجباری", callback_data="admin_add_channel"), InlineKeyboardButton(text="➖ حذف کانال اجباری", callback_data="admin_remove_channel")],
            [InlineKeyboardButton(text="👥 جستجوی کاربر", callback_data="admin_search_user"), InlineKeyboardButton(text="➕/➖ تغییر موجودی", callback_data="admin_edit_balance")],
            [InlineKeyboardButton(text="💬 ارسال پیام مستقیم", callback_data="admin_direct_msg")],
            [InlineKeyboardButton(text="🚫 بن کردن کاربر", callback_data="admin_ban_user"), InlineKeyboardButton(text="🟢 آن‌بن کاربر", callback_data="admin_unban_user")],
            [InlineKeyboardButton(text="⚙️ حداقل برداشت", callback_data="admin_set_min_wd"), InlineKeyboardButton(text="🔝 حداکثر برداشت", callback_data="admin_set_max_wd")],
            [InlineKeyboardButton(text="⛽️ تنظیم گس‌فی شبکه", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text="🧹 صفر کردن موجودی کل کاربران", callback_data="admin_reset_balances")],
            [InlineKeyboardButton(text=withdrawals_btn, callback_data="admin_toggle_withdrawals")],
            [InlineKeyboardButton(text=status_btn, callback_data="admin_toggle_bot"), InlineKeyboardButton(text="📢 همه‌فرستی (Broadcast)", callback_data="admin_broadcast")],
        ]
    )

# ==========================================
# دکمه‌های مربوط به استارت و جوین اجباری
# ==========================================
@dp.callback_query(F.data == "check_join_btn")
async def check_join_btn_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id
    
    if is_banned(u_id):
        await call.answer("🚫 حساب شما از استفاده از ربات مسدود شده است.", show_alert=True)
        return

    if not bot_active and not is_admin(u_id):
        await call.answer("🛑 ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.", show_alert=True)
        return

    is_subscribed = await check_user_subscription(u_id)
    if is_subscribed:
        await call.answer("🎉 عضویتت با موفقیت تأیید شد! حالا آماده دریافت جایزه‌ای 🚀", show_alert=True)
        
        await call.message.delete()
        await call.message.answer(
            f"🔥 <b>به Void Giveaway خوش اومدی!</b> آماده‌ای جایزه جمع کنی؟\n"
            f"🧩 <b>نسخه فعال:</b> <code>{BOT_VERSION}</code> 💎\n\n"

            f"از منوی زیر استفاده کن و موجودی، برداشت و دعوت‌هات رو مدیریت کن 👇",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(u_id)
        )
    else:
        await call.answer("⏳ هنوز عضویتت در همه کانال‌ها تأیید نشده؛ یک بار دیگه بررسی کن!", show_alert=True)

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>دسترسی این حساب متوقف شده است.</b>\nاگر فکر می‌کنی اشتباهی رخ داده، با پشتیبانی تماس بگیر.", parse_mode="HTML")
        return

    profile = get_user_profile(u_id, message.from_user)
    profile["started_at"] = profile.get("started_at") or datetime.utcnow().isoformat()
    all_time_users.add(u_id)
    await save_data()

    if not bot_active and not is_admin(u_id):
        await message.answer("🛠️ <b>ربات موقتاً در حالت تعمیر و ارتقاست.</b>\nخیلی زود برمی‌گردیم؛ موجودی شما کاملاً محفوظ است.", parse_mode="HTML")
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer(
            f"🌟 <b>برای ورود به دنیای جایزه‌ها، ابتدا در کانال‌های رسمی ما عضو شو.</b>\n\n"
            f"✅ بعد از عضویت در همه کانال‌ها، روی «✅ بررسی عضویت / ورود» بزن تا جایزه‌ها برات فعال بشه!",
            parse_mode="HTML",
            reply_markup=get_join_channel_keyboard()
        )
        return

    await message.answer(
        f"🔥 <b>به Void Giveaway خوش اومدی!</b> آماده‌ای جایزه جمع کنی؟\n"
        f"🧩 <b>نسخه فعال:</b> <code>v6.1.0</code> 💎\n\n"

        f"از منوی زیر استفاده کن و موجودی، برداشت و دعوت‌هات رو مدیریت کن 👇",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(u_id)
    )

# ==========================================
# انتقال موجودی بین کاربران در گروه
# ==========================================
async def has_started_bot(user_id: int) -> bool:
    if user_id in all_time_users:
        return True
    profile = user_data.get(user_id)
    if profile and profile.get("started_at"):
        return True
    try:
        user_doc = await users_col.find_one({"user_id": user_id, "started_at": {"$exists": True}}, {"user_id": 1})
        return bool(user_doc)
    except Exception as e:
        logging.error(f"Started-user lookup failed for {user_id}: {e}")
        return False


async def transfer_user_balance(sender_id: int, recipient_id: int, amount: float, recipient: types.User):
    transfer_id = "TR-" + uuid.uuid4().hex[:16].upper()
    now = datetime.utcnow().isoformat()
    session = None
    try:
        session = await mongo_client.start_session()
        async with session.start_transaction():
            sender_after = await users_col.find_one_and_update(
                {"user_id": sender_id, "balance": {"$gte": amount}},
                {"$inc": {"balance": -amount}},
                return_document=ReturnDocument.AFTER, session=session
            )
            if not sender_after:
                return None, "insufficient_balance"

            recipient_after = await users_col.find_one_and_update(
                {"user_id": recipient_id},
                {"$inc": {"balance": amount}, "$setOnInsert": {
                    "user_id": recipient_id,
                    "username": recipient.username or "",
                    "first_name": recipient.first_name or "User",
                    "started_at": now
                }},
                upsert=True, return_document=ReturnDocument.AFTER, session=session
            )
            await transfers_col.insert_one({
                "transfer_id": transfer_id,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "amount": round(amount, 4),
                "status": "completed",
                "created_at": now
            }, session=session)

        sender_profile = get_user_profile(sender_id)
        sender_profile["balance"] = round(float(sender_after.get("balance", 0.0)), 4)
        recipient_profile = get_user_profile(recipient_id, recipient)
        recipient_profile["balance"] = round(float(recipient_after.get("balance", 0.0)), 4)
        return transfer_id, "ok"
    except Exception as e:
        logging.error(f"Balance transfer failed from {sender_id} to {recipient_id}: {e}")
        return None, "error"
    finally:
        if session:
            await session.end_session()


@dp.message(F.text.regexp(r"(?i)^/?wallet(?:@[A-Za-z0-9_]+)?(?:\s|$)"))
async def wallet_transfer_handler(message: types.Message):
    sender = message.from_user
    if not sender or is_banned(sender.id):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("ℹ️ انتقال موجودی فقط با reply به یک کاربر در گروه انجام می‌شود.")
        return
    if not bot_active and not is_admin(sender.id):
        await message.answer("🛠️ ربات موقتاً در حال ارتقاست؛ انتقالی انجام نشد.")
        return

    reply = message.reply_to_message
    recipient = reply.from_user if reply else None
    if not recipient or recipient.is_bot:
        await message.answer("⚠️ روی پیام کاربر مقصد reply کن و بعد بنویس: <code>wallet 0.01</code>", parse_mode="HTML")
        return
    if recipient.id == sender.id:
        await message.answer("⚠️ انتقال موجودی به خودت امکان‌پذیر نیست.")
        return

    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) != 2:
        await message.answer("⚠️ فرمت صحیح: <code>wallet 0.01</code> یا <code>/wallet 0.01</code>", parse_mode="HTML")
        return
    try:
        amount = round(float(parts[1]), 4)
    except (TypeError, ValueError):
        amount = 0
    if not math.isfinite(amount) or amount <= 0:
        await message.answer("⚠️ مقدار انتقال باید یک عدد مثبت باشد.")
        return

    if not await has_started_bot(recipient.id):
        target_name = html.escape(recipient.full_name or "کاربر")
        await message.answer(
            f"⚠️ <a href=\"tg://user?id={recipient.id}\">{target_name}</a> هنوز ربات را Start نکرده است.\n"
            "ابتدا در خصوصی ربات دستور /start را بفرستد؛ هیچ مبلغی از موجودی تو کم نشد.",
            parse_mode="HTML"
        )
        return

    transfer_id, result = await transfer_user_balance(sender.id, recipient.id, amount, recipient)
    if result == "insufficient_balance":
        await message.answer("💰 موجودی تو برای این انتقال کافی نیست؛ هیچ مبلغی کم نشد.")
        return
    if result != "ok":
        await message.answer("⚠️ انتقال انجام نشد و موجودی‌ها تغییر نکردند. دوباره تلاش کن.")
        return

    sender_name = html.escape(sender.full_name or "کاربر")
    recipient_name = html.escape(recipient.full_name or "کاربر")
    group_text = (
        "✅ <b>انتقال موجودی با موفقیت انجام شد.</b>\n"
        f"🆔 شناسه انتقال: <code>{transfer_id}</code>\n"
        f"👤 فرستنده: {sender_name}\n"
        f"🎁 گیرنده: {recipient_name}\n"
        f"💎 مبلغ: <code>{amount:.4f} TON</code>"
    )
    await message.answer(group_text, parse_mode="HTML")
    try:
        await bot.send_message(
            recipient.id,
            "✅ <b>یک انتقال موجودی برایت انجام شد.</b>\n"
            f"👤 از طرف: {sender_name}\n"
            f"💎 مبلغ دریافت‌شده: <code>{amount:.4f} TON</code>\n"
            f"🆔 شناسه انتقال: <code>{transfer_id}</code>\n"
            "موجودی جدیدت را از بخش کیف‌پول بررسی کن.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.warning(f"Transfer confirmation DM failed for {recipient.id}: {e}")

# ==========================================
# سیستم برداشت و مدیریت موجودی - بازنویسی پایدار
# ==========================================
def is_valid_ton_address(wallet_address: str) -> bool:
    """اعتبارسنجی آدرس TON friendly با checksum استاندارد."""
    address = wallet_address.strip() if isinstance(wallet_address, str) else ""
    if not re.fullmatch(r"(?:EQ|UQ)[a-zA-Z0-9_-]{46}", address):
        return False
    try:
        raw = base64.urlsafe_b64decode(address + "==")
        if len(raw) != 36:
            return False
        payload, checksum = raw[:-2], raw[-2:]
        crc = 0
        for byte in payload:
            crc ^= byte << 8
            for _ in range(8):
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        return payload[0] in (0x11, 0x51) and crc.to_bytes(2, "big") == checksum
    except (ValueError, TypeError, base64.binascii.Error):
        return False


async def create_withdrawal_record(user_id: int, wallet_address: str, requested_amount: float,
                                   amount_to_send: float, deducted_amount: float) -> str:
    withdrawal_id = uuid.uuid4().hex[:16]
    now = datetime.utcnow().isoformat()
    await withdrawals_col.insert_one({
        "withdrawal_id": withdrawal_id,
        "user_id": user_id,
        "wallet_address": wallet_address,
        "requested_amount": round(requested_amount, 4),
        "amount_to_send": round(amount_to_send, 4),
        "deducted_amount": round(deducted_amount, 4),
        "status": "created",
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now
    })
    return withdrawal_id


async def set_withdrawal_status(withdrawal_id: str, status: str, **fields):
    fields["status"] = status
    fields["updated_at"] = datetime.utcnow().isoformat()
    await withdrawals_col.update_one({"withdrawal_id": withdrawal_id}, {"$set": fields})


async def reserve_user_balance(user_id: int, amount: float) -> bool:
    """رزرو اتمیک موجودی؛ دو درخواست هم‌زمان نمی‌توانند یک موجودی را مصرف کنند."""
    amount = round(float(amount), 4)
    prof = get_user_profile(user_id)
    await users_col.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {
            "user_id": user_id, "balance": round(float(prof.get("balance", 0.0)), 4),
            "username": prof.get("username", ""), "first_name": prof.get("first_name", "User")
        }},
        upsert=True
    )
    result = await users_col.update_one(
        {"user_id": user_id, "balance": {"$gte": amount}},
        {"$inc": {"balance": -amount}}
    )
    if result.matched_count != 1:
        return False
    prof["balance"] = round(float(prof.get("balance", 0.0)) - amount, 4)
    return True


async def refund_withdrawal(withdrawal_id: str, reason: str,
                            allowed_statuses=("created", "reserved", "pending", "processing", "failed")) -> bool:
    status_filter = allowed_statuses[0] if len(allowed_statuses) == 1 else {"$in": list(allowed_statuses)}
    withdrawal = await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": status_filter},
        {"$set": {
            "status": "refunded", "refund_reason": reason,
            "updated_at": datetime.utcnow().isoformat()
        }},
        return_document=ReturnDocument.BEFORE
    )
    if not withdrawal:
        return False

    user_id = int(withdrawal["user_id"])
    amount = round(float(withdrawal["deducted_amount"]), 4)
    await users_col.update_one({"user_id": user_id}, {"$inc": {"balance": amount}}, upsert=True)
    prof = get_user_profile(user_id)
    prof["balance"] = round(float(prof.get("balance", 0.0)) + amount, 4)
    return True


async def claim_withdrawal(withdrawal_id: str):
    return await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": "pending"},
        {"$set": {"status": "processing", "updated_at": datetime.utcnow().isoformat()},
         "$inc": {"attempt_count": 1}},
        return_document=ReturnDocument.AFTER
    )


async def reject_withdrawal_without_refund(withdrawal_id: str, reason: str) -> bool:
    rejected = await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": "pending"},
        {"$set": {"status": "rejected", "reject_reason": reason,
                  "updated_at": datetime.utcnow().isoformat()}},
        return_document=ReturnDocument.BEFORE
    )
    return bool(rejected)


@dp.message(F.text == "💎 کیف‌پول من (Wallet)")
async def show_wallet(message: types.Message):
    u_id = message.from_user.id
    if is_banned(u_id):
        await message.answer("🚫 <b>دسترسی این حساب متوقف شده است.</b>\nاگر فکر می‌کنی اشتباهی رخ داده، با پشتیبانی تماس بگیر.", parse_mode="HTML")
        return
    if not bot_active and not is_admin(u_id):
        await message.answer("🛠️ <b>ربات موقتاً در حالت تعمیر و ارتقاست.</b>\nخیلی زود برمی‌گردیم؛ موجودی شما کاملاً محفوظ است.", parse_mode="HTML")
        return
    if not await check_user_subscription(u_id):
        await message.answer("🔐 <b>برای ورود به بخش جایزه‌ها، اول در کانال‌های رسمی عضو شو.</b>", parse_mode="HTML", reply_markup=get_join_channel_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    text = (
        f"💎 <b>داشبورد کیف‌پول تو</b> 🔥\n━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>موجودی آماده برداشت:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"⚡️ <b>کارمزد شبکه:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ واریز TON", callback_data="start_deposit")],
            [InlineKeyboardButton(text="🚀 ثبت درخواست برداشت", callback_data="start_withdraw")]
        ]
    ))


@dp.callback_query(F.data == "start_withdraw")
async def start_withdraw_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id
    if is_banned(u_id):
        await call.answer("🚫 این حساب دسترسی فعال ندارد.", show_alert=True)
        return
    if not bot_active and not is_admin(u_id):
        await call.answer("🛠️ ربات موقتاً در حال ارتقاست.", show_alert=True)
        return
    if not withdrawals_enabled and not is_admin(u_id):
        await call.answer("🛑 برداشت‌ها موقتاً خاموش هستند.", show_alert=True)
        return
    if not await check_user_subscription(u_id):
        await call.answer("🔐 برای برداشت، عضویت در همه کانال‌ها الزامی است!", show_alert=True)
        return
    prof = get_user_profile(u_id, call.from_user)
    if float(prof.get("balance", 0.0)) < min_withdraw_amount:
        await call.answer(f"💰 موجودی کافی نیست؛ برای برداشت حداقل {min_withdraw_amount} TON لازم داری.", show_alert=True)
        return
    await call.answer()
    await state.set_state(WithdrawForm.amount)
    await call.message.answer(
        f"💎 <b>موجودی آماده برداشت تو:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"🔻 حداقل: <code>{min_withdraw_amount} TON</code> | 🔝 حداکثر: <code>{max_withdraw_amount} TON</code>\n"
        f"⛽️ کارمزد: <code>{ton_gas_fee} TON</code>\n\n🎯 مقدار موردنظرت برای برداشت رو وارد کن:",
        parse_mode="HTML"
    )


@dp.message(WithdrawForm.amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        return
    try:
        req_amount = round(float(message.text.strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ لطفاً یک عدد معتبر وارد کن؛ عددی که وارد می‌کنی باید قابل برداشت باشد.")
        return
    if not math.isfinite(req_amount) or req_amount <= 0:
        await message.answer("⚠️ مبلغ برداشت باید بیشتر از صفر باشد!")
        return
    if req_amount < min_withdraw_amount:
        await message.answer(f"🔻 حداقل برداشت <code>{min_withdraw_amount} TON</code> است؛ مبلغ را کمی بیشتر وارد کن.", parse_mode="HTML")
        return
    if req_amount > max_withdraw_amount:
        await message.answer(f"🔝 سقف برداشت <code>{max_withdraw_amount} TON</code> است؛ مبلغ را در این محدوده وارد کن.", parse_mode="HTML")
        return
    prof = get_user_profile(message.from_user.id, message.from_user)
    if req_amount > float(prof.get("balance", 0.0)):
        await message.answer("💸 مبلغ درخواستی از موجودی فعلی‌ات بیشتر است؛ مقدار را اصلاح کن.")
        return
    amount_to_send = round(req_amount - max(ton_gas_fee, 0), 4)
    if amount_to_send <= 0:
        await message.answer(f"⚠️ مبلغ برداشت باید از کارمزد شبکه بیشتر باشد ({ton_gas_fee} TON) باشد!")
        return
    await state.update_data(requested_amount=req_amount, amount_to_send=amount_to_send, deducted_amount=req_amount)
    await state.set_state(WithdrawForm.wallet_address)
    await message.answer(
        f"🚀 <b>خلاصه برداشت تو</b>\n🔹 درخواست: <code>{req_amount:.4f} TON</code>\n"
        f"⛽️ کارمزد: <code>{ton_gas_fee:.4f} TON</code>\n🚀 دریافتی: <code>{amount_to_send:.4f} TON</code>\n\n"
        "📬 حالا آدرس کیف‌پول TON مقصد را بفرست:", parse_mode="HTML"
    )


@dp.message(WithdrawForm.wallet_address)
async def process_withdraw_address(message: types.Message, state: FSMContext):
    user = message.from_user
    if is_banned(user.id):
        await state.clear()
        return
    if not withdrawals_enabled and not is_admin(user.id):
        await state.clear()
        await message.answer("🛑 برداشت‌ها موقتاً خاموش هستند؛ موجودی شما محفوظ است.", reply_markup=get_main_keyboard(user.id))
        return

    wallet_addr = (message.text or "").strip()
    if not is_valid_ton_address(wallet_addr):
        await message.answer("⚠️ این آدرس TON معتبر نیست؛ یک آدرس کامل EQ یا UQ با checksum صحیح بفرست.")
        return

    data = await state.get_data()
    amount_to_send = data.get("amount_to_send")
    deducted_amount = data.get("deducted_amount")
    requested_amount = data.get("requested_amount")
    if None in (amount_to_send, deducted_amount, requested_amount):
        await state.clear()
        await message.answer("⏱️ نشست برداشت منقضی شد؛ دوباره از کیف‌پول شروع کن.")
        return

    withdrawal_id = None
    try:
        async with withdrawal_flow_lock:
            withdrawal_id = await create_withdrawal_record(
                user.id, wallet_addr, float(requested_amount), float(amount_to_send), float(deducted_amount)
            )
            reserved = await reserve_user_balance(user.id, float(deducted_amount))
            if not reserved:
                await set_withdrawal_status(withdrawal_id, "failed", last_error="موجودی کافی نبود")
                await state.clear()
                await message.answer("💰 موجودی برای ثبت این برداشت کافی نیست؛ مبلغی از کیف‌پولت کم نشد.", reply_markup=get_main_keyboard(user.id))
                return
            await set_withdrawal_status(
                withdrawal_id, "processing", reserved_at=datetime.utcnow().isoformat()
            )
    except Exception as e:
        logging.error(f"Automatic withdrawal reservation error: {e}")
        if withdrawal_id:
            try:
                await refund_withdrawal(withdrawal_id, "خطا هنگام رزرو برداشت")
            except Exception as refund_error:
                logging.error(f"Automatic withdrawal reservation refund error: {refund_error}")
        await state.clear()
        await message.answer("⚠️ ثبت برداشت کامل نشد؛ اگر مبلغی رزرو شده بود، خودکار بررسی می‌شود.", reply_markup=get_main_keyboard(user.id))
        return

    await state.clear()
    await message.answer(
        f"🚀 <b>برداشت خودکار ثبت شد.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
        f"مبلغ دریافتی: <code>{float(amount_to_send):.4f} TON</code>\n"
        "در حال ارسال امن به شبکه TON...", parse_mode="HTML"
    )

    payout_status, result_msg = await send_ton_payout(wallet_addr, float(amount_to_send))
    if payout_status == "sent":
        await set_withdrawal_status(
            withdrawal_id, "sent", sent_at=datetime.utcnow().isoformat(), result_message=result_msg
        )
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send), "✅ برداشت خودکار ارسال شد.", result_msg
        )
        await message.answer(
            f"🎉 <b>برداشت با موفقیت ارسال شد!</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            f"مبلغ: <code>{float(amount_to_send):.4f} TON</code>\n"
            "نمایش تراکنش در کیف‌پول مقصد ممکن است کمی زمان ببرد.",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )
    elif payout_status == "uncertain":
        await set_withdrawal_status(
            withdrawal_id, "pending_verification", last_error=result_msg,
            verification_required_at=datetime.utcnow().isoformat()
        )
        await notify_wallet_issue(float(amount_to_send), result_msg, withdrawal_id)
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send), "⏳ نتیجه برداشت نیاز به بررسی شبکه دارد.", result_msg
        )
        await message.answer(
            f"⏳ <b>برداشت در حال بررسی شبکه است.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            "برای جلوگیری از پرداخت دوباره، تا مشخص‌شدن نتیجه مبلغ در حالت رزرو می‌ماند.",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )
    else:
        await set_withdrawal_status(withdrawal_id, "failed", last_error=result_msg)
        refunded = await refund_withdrawal(withdrawal_id, result_msg, allowed_statuses=("failed",))
        refund_text = "مبلغ رزروشده خودکار برگشت داده شد." if refunded else "وضعیت برای بررسی ایمن ثبت شده است."
        await notify_wallet_issue(float(amount_to_send), result_msg, withdrawal_id)
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send), "⚠️ برداشت ارسال نشد و refund انجام شد.", result_msg
        )
        await message.answer(
            f"⚠️ <b>برداشت ارسال نشد.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            f"علت: {html.escape(str(result_msg))}\n{refund_text}",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )

async def notify_withdrawal_result(withdrawal_id: str, user_id: int, amount: float, status_text: str, detail: str = ""):
    """ثبت نتیجه‌ی هر برداشت خودکار در کانال عملیاتی با شناسه‌ی یکتا."""
    try:
        text = (
            "🤖 <b>گزارش برداشت خودکار</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🆔 شناسه: <code>{html.escape(str(withdrawal_id))}</code>\n"
            f"👤 کاربر: <code>{user_id}</code>\n"
            f"💎 مبلغ: <code>{amount:.4f} TON</code>\n"
            f"{status_text}"
        )
        if detail:
            text += f"\n📝 جزئیات: <code>{html.escape(str(detail))}</code>"
        await bot.send_message(chat_id=WITHDRAW_CHANNEL, text=text, parse_mode="HTML")
    except Exception as channel_error:
        logging.error(f"Automatic withdrawal notification error for {withdrawal_id}: {channel_error}")

# ==========================================
# رابط واریز TON
# ==========================================
@dp.callback_query(F.data == "start_deposit")
async def start_deposit_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id
    if is_banned(u_id):
        await call.answer("🚫 این حساب دسترسی فعال ندارد.", show_alert=True)
        return
    if not bot_active and not is_admin(u_id):
        await call.answer("🛠️ ربات موقتاً در حال ارتقاست.", show_alert=True)
        return
    if not await check_user_subscription(u_id):
        await call.answer("🔐 برای واریز، عضویت در همه کانال‌ها الزامی است!", show_alert=True)
        return
    await call.answer()
    await state.set_state(DepositForm.amount)
    await call.message.answer(
        f"💎 مقدار TON موردنظرت برای واریز را وارد کن.\nحداقل واریز: <code>{min_deposit_amount:.4f} TON</code>",
        parse_mode="HTML"
    )


@dp.message(DepositForm.amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = round(float((message.text or "").strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ لطفاً مقدار معتبر TON وارد کن.")
        return
    if not math.isfinite(amount) or amount < min_deposit_amount:
        await message.answer(f"⚠️ حداقل واریز <code>{min_deposit_amount:.4f} TON</code> است.", parse_mode="HTML")
        return

    wallet_address = await get_system_wallet_address()
    if not wallet_address:
        await state.clear()
        await message.answer("⚠️ آدرس ولت مرکزی فعلاً قابل دریافت نیست؛ بعداً دوباره تلاش کن.")
        return

    memo = get_deposit_memo(message.from_user.id)
    amount_nano = int(round(amount * 10**9))
    ton_link = f"ton://transfer/{wallet_address}?amount={amount_nano}&text={quote(memo)}"
    await state.clear()
    await message.answer(
        "💳 <b>واریز TON آماده است</b>\n\n"
        f"💎 مبلغ: <code>{amount:.4f} TON</code>\n"
        f"📬 آدرس مرکزی: <code>{html.escape(wallet_address)}</code>\n"
        f"🧾 کد شناسایی واریز: <code>{memo}</code>\n\n"
        "با دکمه زیر کیف‌پولت را باز کن و تراکنش را تأیید کن. حتماً memo را تغییر نده؛ ربات بعد از ثبت تراکنش آن را خودکار به موجودی تو اضافه می‌کند.",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 بازکردن کیف‌پول و تأیید واریز", url=ton_link)]
        ])
    )


# ==========================================
# پنل مدیریت پیشرفته ادمین
# ==========================================
@dp.message(F.text == "⚙️ پنل مدیریت ادمین 👑")
async def open_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    total_users = len(user_data)
    total_all_time = len(all_time_users)
    banned_count = len(banned_users)
    total_balance = sum(u.get("balance", 0.0) for u in user_data.values())
    
    sys_balance, wallet_addr = await get_system_wallet_balance()
    if sys_balance is not None:
        wallet_str = f"<code>{sys_balance:.4f} TON</code>\n💳 <b>آدرس ولت:</b> <code>{wallet_addr}</code>"
    else:
        wallet_str = f"⚠️ <b>خطا در استعلام:</b> {wallet_addr}"

    ch_list_str = ", ".join(required_channels) if required_channels else "هیچ کانالی تنظیم نشده است."

    admin_text = (
        "👑 <b>مرکز فرماندهی Void Giveaway</b> 🚀\n<code>v6.1.0</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>موجودی واقعی ولت اصلی ربات:</b> {wallet_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>وضعیت ربات:</b> {'روشن ✅' if bot_active else 'خاموش/تعمیرات 🛑'}\n"
        f"🚀 <b>برداشت خودکار:</b> {'فعال ✅' if withdrawals_enabled else 'خاموش 🛑'}\n"
        f"📢 <b>کانال‌های جوین اجباری ({len(required_channels)}):</b> {ch_list_str}\n"
        f"👥 <b>کاربران فعال فعلی:</b> <code>{total_users}</code> نفر\n"
        f"📜 <b>کل کاربران تاریخی:</b> <code>{total_all_time}</code> نفر\n"
        f"🚫 <b>کاربران بن شده:</b> <code>{banned_count}</code> نفر\n"
        f"💰 <b>مجموع موجودی ولت کاربران:</b> <code>{total_balance:.4f} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "از دکمه‌های زیر برای مدیریت حرفه‌ای ربات استفاده کنید 👇"
    )
    
    await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_inline_keyboard())

# --- افزودن و حذف کانال جوین اجباری ---
@dp.callback_query(F.data == "admin_add_channel")
async def start_add_channel(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminAddChannelForm.channel)
    await call.message.edit_text("➕ <b>یوزرنیم کانال جدید را برای فعال‌کردن عضویت اجباری بفرست:</b>\nمثال: <code>@mychannel</code>", parse_mode="HTML")

@dp.message(AdminAddChannelForm.channel)
async def process_add_channel(message: types.Message, state: FSMContext):
    raw_channel = message.text.strip()
    if "t.me/" in raw_channel:
        raw_channel = raw_channel.split("t.me/")[-1].replace("/", "")
    channel_id = raw_channel if raw_channel.startswith("@") else "@" + raw_channel

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(chat_id=chat.id, user_id=bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer("❌ ربات در این کانال ادمین نیست! ابتدا ربات را ادمین کانال کنید.")
            return
    except Exception:
        await message.answer("❌ کانال یافت نشد یا ربات دسترسی ندارد!")
        return

    if channel_id in required_channels:
        await message.answer("⚠️ این کانال قبلاً در لیست موجود می‌باشد.")
        await state.clear()
        return

    required_channels.append(channel_id)
    await save_data()
    await state.clear()
    await message.answer(f"🎉 کانال <code>{channel_id}</code> با موفقیت به لیست عضویت اجباری اضافه شد!", parse_mode="HTML")

@dp.callback_query(F.data == "admin_remove_channel")
async def start_remove_channel(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    
    if not required_channels:
        await call.message.edit_text("📭 هنوز هیچ کانالی برای عضویت اجباری تنظیم نشده است.")
        return

    buttons = []
    for ch in required_channels:
        buttons.append([InlineKeyboardButton(text=f"❌ حذف {ch}", callback_data=f"remove_ch_{ch}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به مرکز فرماندهی", callback_data="admin_back_panel")])
    
    await call.message.edit_text("🗑️ <b>برای حذف کانال، گزینه موردنظر را انتخاب کن:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("remove_ch_"))
async def process_remove_channel_callback(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return

    ch_to_remove = call.data.replace("remove_ch_", "")
    if ch_to_remove in required_channels:
        required_channels.remove(ch_to_remove)
        await save_data()
        await call.answer(f"✅ کانال {ch_to_remove} حذف شد!", show_alert=True)
    else:
        await call.answer("⚠️ کانال در لیست یافت نشد.", show_alert=True)
    
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot_callback(call: types.CallbackQuery):
    global bot_active
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    
    bot_active = not bot_active
    await save_data()
    status_msg = "🛑 ربات خاموش شد." if not bot_active else "✅ ربات روشن شد."
    await call.answer(status_msg, show_alert=True)
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_toggle_withdrawals")
async def toggle_withdrawals_callback(call: types.CallbackQuery):
    global withdrawals_enabled
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    withdrawals_enabled = not withdrawals_enabled
    await save_data()
    status_msg = "✅ برداشت خودکار روشن شد." if withdrawals_enabled else "🛑 برداشت خودکار خاموش شد."
    await call.answer(status_msg, show_alert=True)
    await open_admin_panel(call.message)


@dp.callback_query(F.data == "admin_back_panel")
async def admin_back_panel_callback(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    await call.answer()
    await open_admin_panel(call.message)


# --- سیستم بن و آن‌بن ---
@dp.callback_query(F.data == "admin_ban_user")
async def start_ban_user(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminBanUserForm.user_id)
    await call.message.edit_text("🚫 <b>آیدی عددی (User ID) کاربر جهت بن کردن را وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminBanUserForm.user_id)
async def process_ban_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً آیدی عددی معتبر وارد کنید!")
        return

    target_id = int(message.text)
    await state.clear()

    if target_id in ADMIN_IDS:
        await message.answer("❌ امکان بن کردن ادمین وجود ندارد!")
        return

    banned_users.add(target_id)
    await save_data()
    await message.answer(f"🚫 کاربر <code>{target_id}</code> با موفقیت مسدود شد!", parse_mode="HTML")

@dp.callback_query(F.data == "admin_unban_user")
async def start_unban_user(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminUnbanUserForm.user_id)
    await call.message.edit_text("🟢 <b>آیدی عددی (User ID) کاربر جهت آن‌بن کردن را وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminUnbanUserForm.user_id)
async def process_unban_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً آیدی عددی معتبر وارد کنید!")
        return

    target_id = int(message.text)
    await state.clear()

    if target_id in banned_users:
        banned_users.remove(target_id)
        await save_data()
        await message.answer(f"🟢 کاربر <code>{target_id}</code> با موفقیت از بن خارج شد!", parse_mode="HTML")
    else:
        await message.answer("⚠️ این کاربر در لیست بن شده‌ها قرار ندارد.")

# --- ارسال پیام مستقیم ---
@dp.callback_query(F.data == "admin_direct_msg")
async def start_direct_message(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminDirectMessageForm.user_id)
    await call.message.edit_text("💬 <b>آیدی عددی کاربر دریافت‌کننده پیام را بفرست:</b>", parse_mode="HTML")

@dp.message(AdminDirectMessageForm.user_id)
async def process_direct_msg_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً آیدی عددی معتبر وارد کنید!")
        return

    target_id = int(message.text)
    await state.update_data(target_u_id=target_id)
    await state.set_state(AdminDirectMessageForm.message)
    await message.answer(f"📝 <b>پیام خود را جهت ارسال به کاربر <code>{target_id}</code> وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminDirectMessageForm.message)
async def process_direct_msg_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_u_id")
    await state.clear()

    try:
        await message.copy_to(chat_id=target_id)
        await message.answer(f"📨 <b>پیام با موفقیت برای کاربر <code>{target_id}</code> ارسال شد!</b>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ <b>خطا در ارسال پیام:</b> {e}", parse_mode="HTML")

# --- جستجوی کاربر ---
@dp.callback_query(F.data == "admin_search_user")
async def start_search_user(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSearchUserForm.user_id)
    await call.message.edit_text("🔍 <b>آیدی عددی کاربر موردنظر را برای جستجو بفرست:</b>", parse_mode="HTML")

@dp.message(AdminSearchUserForm.user_id)
async def process_search_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً یک آیدی عددی معتبر وارد کن!")
        return

    target_id = int(message.text)
    await state.clear()

    if target_id not in user_data:
        await message.answer(f"❌ کاربر با آیدی <code>{target_id}</code> در دیتابیس ربات یافت نشد!", parse_mode="HTML")
        return

    target_prof = user_data[target_id]
    ban_status = "بله 🚫" if is_banned(target_id) else "خیر 🟢"
    
    user_info_text = (
        f"👤 <b>اطلاعات کاربر <code>{target_id}</code>:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>نام:</b> {html.escape(target_prof.get('first_name', 'User'))}\n"
        f"🆔 <b>یوزرنیم:</b> @{target_prof.get('username', 'ندارد')}\n"
        f"🚫 <b>وضعیت بن:</b> {ban_status}\n"
        f"💰 <b>موجودی TON:</b> <code>{target_prof.get('balance', 0.0):.4f} TON</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💳 <b>کد واریز:</b> <code>{get_deposit_memo(target_id)}</code>"
    )

    await message.answer(user_info_text, parse_mode="HTML")

# ==========================================
# مدیریت صفر کردن موجودی کاربران
# ==========================================
@dp.callback_query(F.data == "admin_reset_balances")
async def start_reset_balances(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    await call.answer("⚠️ این عملیات برگشت‌پذیر نیست.", show_alert=True)
    await call.message.edit_text(
        f"⚠️ <b>صفر کردن موجودی همه کاربران</b>\n\nتعداد کاربران فعلی: <code>{len(user_data)}</code>\nاین کار فقط موجودی‌ها را صفر می‌کند و قابل بازگشت خودکار نیست. ادامه می‌دهی؟",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚠️ بله، همه را صفر کن", callback_data="admin_reset_balances_confirm")],
            [InlineKeyboardButton(text="لغو", callback_data="admin_back_panel")]
        ])
    )


@dp.callback_query(F.data == "admin_reset_balances_confirm")
async def confirm_reset_balances(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    result = await users_col.update_many({}, {"$set": {"balance": 0.0}})
    for profile in user_data.values():
        profile["balance"] = 0.0
    await call.answer("✅ موجودی همه کاربران صفر شد.", show_alert=True)
    await call.message.edit_text(
        f"✅ <b>عملیات انجام شد.</b>\nموجودی <code>{result.modified_count}</code> کاربر صفر شد.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 بازگشت به پنل ادمین", callback_data="admin_back_panel")]
        ])
    )


# --- تغییر کانفیگ‌ها ---
@dp.callback_query(F.data == "admin_set_min_wd")
async def start_set_min_wd(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMinWithdrawForm.amount)
    await call.message.edit_text(f"⚙️ <b>حداقل مقدار جدید برای برداشت TON را وارد کنید (فعلی: {min_withdraw_amount}):</b>", parse_mode="HTML")

@dp.message(AdminSetMinWithdrawForm.amount)
async def process_set_min_wd(message: types.Message, state: FSMContext):
    global min_withdraw_amount
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount <= 0 or amount > max_withdraw_amount:
            raise ValueError
        min_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ حداقل برداشت با موفقیت روی <code>{min_withdraw_amount} TON</code> تنظیم شد 🚀", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر و قابل قبول وارد کن!")

@dp.callback_query(F.data == "admin_set_max_wd")
async def start_set_max_wd(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMaxWithdrawForm.amount)
    await call.message.edit_text(f"🔝 <b>حداکثر سقف جدید برای برداشت TON را وارد کنید (فعلی: {max_withdraw_amount}):</b>", parse_mode="HTML")

@dp.message(AdminSetMaxWithdrawForm.amount)
async def process_set_max_wd(message: types.Message, state: FSMContext):
    global max_withdraw_amount
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount < min_withdraw_amount:
            raise ValueError
        max_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ سقف برداشت با موفقیت روی <code>{max_withdraw_amount} TON</code> تنظیم شد 🚀", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر و قابل قبول وارد کن!")

@dp.callback_query(F.data == "admin_set_gas_fee")
async def start_set_gas_fee(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetGasFeeForm.amount)
    await call.message.edit_text(f"⛽️ <b>مقدار گس‌فی شبکه TON را به عدد وارد کنید (فعلی: {ton_gas_fee} TON):</b>", parse_mode="HTML")

@dp.message(AdminSetGasFeeForm.amount)
async def process_set_gas_fee(message: types.Message, state: FSMContext):
    global ton_gas_fee
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount < 0:
            raise ValueError
        ton_gas_fee = amount
        await save_data()
        await state.clear()
        await message.answer(f"⚡️ کارمزد شبکه TON روی <code>{ton_gas_fee} TON</code> تنظیم شد 🚀", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر و قابل قبول وارد کن!")

@dp.callback_query(F.data == "admin_edit_balance")
async def start_edit_balance(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminManageUserForm.user_id)
    await call.message.edit_text("👤 <b>آیدی عددی کاربر را برای مدیریت ارسال کن:</b>", parse_mode="HTML")

@dp.message(AdminManageUserForm.user_id)
async def process_edit_balance_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً یک آیدی عددی معتبر وارد کن!")
        return
    await state.update_data(target_u_id=int(message.text))
    await state.set_state(AdminManageUserForm.amount)
    await message.answer("💎 مقدار تغییر موجودی را وارد کن؛ مثال: <code>0.5</code> برای افزایش یا <code>-0.5</code> برای کاهش:", parse_mode="HTML")

@dp.message(AdminManageUserForm.amount)
async def process_edit_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ مقدار عددی معتبر وارد کنید!")
        return

    if not math.isfinite(amount):
        await message.answer("⚠️ مقدار باید یک عدد محدود و معتبر باشد!")
        return
    data = await state.get_data()
    target_id = data.get("target_u_id")
    
    prof = get_user_profile(target_id)
    new_balance = round(prof["balance"] + amount, 4)
    if new_balance < 0:
        await message.answer("⚠️ موجودی کاربر نمی‌تواند منفی شود!")
        return
    prof["balance"] = new_balance
    await save_data()
    await state.clear()

    await message.answer(
        f"✅ موجودی کاربر <code>{target_id}</code> به‌روزرسانی شد.\n💰 موجودی جدید: <code>{prof['balance']} TON</code>",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminBroadcastForm.message)
    await call.message.edit_text("📢 <b>پیام همه‌فرستی را برای ارسال به کاربران بفرست:</b>", parse_mode="HTML")

@dp.message(AdminBroadcastForm.message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    await state.clear()
    sent_count = 0
    fail_count = 0
    
    msg = await message.answer("📢 پیام در حال ارسال به کاربران است؛ کمی صبر کنید...")
    
    for u_id in list(user_data.keys()):
        try:
            await message.copy_to(chat_id=u_id)
            sent_count += 1
            await asyncio.sleep(0.04)
        except Exception:
            fail_count += 1

    await msg.edit_text(
        f"🎉 <b>همه‌فرستی با موفقیت تمام شد!</b>\n\n📥 موفق: {sent_count}\n❌ ناموفق: {fail_count}",
        parse_mode="HTML"
    )

# ==========================================
# اجرای اصلی برنامه
# ==========================================
async def main():
    await load_data()
    keep_alive()
    asyncio.create_task(wallet_balance_tracker_loop())
    asyncio.create_task(deposit_tracker_loop())
    asyncio.create_task(withdrawal_recovery_loop())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
