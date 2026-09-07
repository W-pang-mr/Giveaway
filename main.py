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
from pytoniq_core import Address, begin_cell
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
# DOGS Jetton روی شبکه اصلی TON؛ decimals رسمی این توکن ۹ است.
DOGS_JETTON_MASTER = "EQCvxJy4eG8hyHBFsZ7eePxrRsUQSFE_jpptRAYBmcG_DOGS"
DOGS_OWNER_WALLET_ADDRESS = os.environ.get("DOGS_OWNER_WALLET_ADDRESS", "UQB26xkOJbJyP5oqhW1fYjZvfb-H4UhgNllpRj7lMqJwW_Bt")
DOGS_DECIMALS = 9

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
# تنظیمات مستقل DOGS؛ کارمزد شبکه برای برداشت DOGS با TON پرداخت می‌شود.
dogs_gas_fee_ton = 0.05
dogs_min_withdraw_amount = 1000.0
dogs_max_withdraw_amount = 1000000.0
dogs_withdrawals_enabled = True
tracker_message_id = None
system_wallet_address = None
system_dogs_wallet_address = None
# ارسال‌های TON باید پشت‌سرهم انجام شوند تا چند برداشت هم‌زمان از یک موجودی عبور نکند.
payout_lock = asyncio.Lock()
# کل مسیر رزرو/بازگشت موجودی و برداشت در یک پردازش سریالی انجام می‌شود.
withdrawal_flow_lock = asyncio.Lock()
# DOGS deposit scans are serialized to avoid overlapping chain reads and credits.
dogs_deposit_scan_lock = asyncio.Lock()

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
            balance_dogs, dogs_wallet_addr = await get_system_dogs_balance()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            ton_line = (
                f"💎 <b>موجودی:</b> <code>{balance_ton:.4f} TON</code>\n"
                f"💳 <b>آدرس ولت TON:</b>\n<code>{wallet_addr}</code>"
                if balance_ton is not None else
                f"⚠️ <b>خطای موجودی TON:</b> {html.escape(str(wallet_addr))}"
            )
            dogs_line = (
                f"🐶 <b>موجودی:</b> <code>{balance_dogs:.4f} DOGS</code>\n"
                f"🧾 <b>Jetton Wallet DOGS:</b>\n<code>{dogs_wallet_addr}</code>"
                if balance_dogs is not None else
                f"⚠️ <b>خطای موجودی DOGS:</b> {html.escape(str(dogs_wallet_addr))}"
            )
            text = (
                f"💎 <b>گزارش لحظه‌ای موجودی ولت اصلی سیستم</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{ton_line}\n\n{dogs_line}\n\n"
                f"⏰ <b>آخرین بروزرسانی:</b> {now_str}\n"
                f"🔄 <i>بروزرسانی خودکار هر ۳ دقیقه انجام می‌شود.</i>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━"
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

    async def check_channel(ch):
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(chat_id=ch, user_id=user_id),
                timeout=1.2
            )
            return member.status in ["creator", "administrator", "member"]
        except asyncio.TimeoutError:
            logging.warning(f"Subscription check timed out for {ch}")
            return False
        except Exception as e:
            logging.error(f"Subscription Check Error for {ch}: {e}")
            return False

    results = await asyncio.gather(*(check_channel(ch) for ch in required_channels))
    return all(results)

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


async def wait_for_wallet_seqno(previous_seqno: int, attempts: int = 20, interval: int = 3) -> bool:
    """Poll a fresh TON LiteClient so confirmation is not based on a stale connection."""
    for attempt in range(attempts):
        probe = None
        try:
            probe = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await probe.connect()
            probe_wallet = await WalletV5R1.from_mnemonic(
                probe, TON_MNEMONIC.strip().split(), network_global_id=-239
            )
            if await probe_wallet.get_seqno() > previous_seqno:
                return True
        except Exception as confirm_error:
            logging.warning(f"Wallet seqno confirmation attempt {attempt + 1} failed: {confirm_error}")
        finally:
            await close_lite_client(probe)
        if attempt < attempts - 1:
            await asyncio.sleep(interval)
    return False


async def send_ton_payout(destination_address: str, amount_ton: float):
    """Send TON and confirm the wallet message through a fresh network query."""
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
            transfer_started = True
            await wallet.transfer(
                destination=destination_address.strip(),
                amount=int(round(amount_ton * 10**9)),
                body="Payout from Void Giveaway Bot 🎉"
            )
            await close_lite_client(client)
            client = None

            if await wait_for_wallet_seqno(seqno_before):
                return "sent", (
                    f"ارسال {amount_ton:.4f} TON روی شبکه ثبت و تأیید شد؛ "
                    "نمایش تراکنش در کیف‌پول مقصد ممکن است کمی زمان ببرد."
                )
            return "uncertain", "پیام TON ارسال شده اما تأیید شبکه هنوز دریافت نشده است؛ مبلغ رزرو می‌ماند."
        except Exception as e:
            logging.error(f"pytoniq W5 Payout Error: {e}")
            await close_lite_client(client)
            if transfer_started:
                return "uncertain", "نتیجه ارسال TON قطعی نیست؛ مبلغ برای بررسی بیشتر رزرو می‌ماند."
            return "failed", str(e)

def build_dogs_transfer_body(amount_units: int, destination_address: str,
                             response_address: str, comment: str):
    """Build a standards-compliant Jetton transfer body."""
    forward_payload = (
        begin_cell()
        .store_uint(0, 32)
        .store_snake_string(comment)
        .end_cell()
    )
    return (
        begin_cell()
        .store_uint(0x0f8a7ea5, 32)  # jetton::transfer
        .store_uint(uuid.uuid4().int & ((1 << 64) - 1), 64)
        .store_coins(amount_units)
        .store_address(destination_address)
        .store_address(response_address)
        .store_uint(0, 1)  # no custom payload
        .store_coins(1)    # forward TON amount
        .store_uint(1, 1)  # forward payload is a reference
        .store_ref(forward_payload)
        .end_cell()
    )

async def send_dogs_payout(destination_address: str, amount_dogs: float):
    """Send DOGS and confirm the central wallet seqno through a fresh connection."""
    if not TON_MNEMONIC:
        return "failed", "کلید امنیتی ولت (TON_MNEMONIC) تنظیم نشده است!"
    if not is_valid_ton_address(destination_address):
        return "failed", "آدرس کیف‌پول TON معتبر نیست یا checksum آن درست نیست."
    if not math.isfinite(amount_dogs) or amount_dogs <= 0:
        return "failed", "مبلغ DOGS معتبر نیست."

    amount_units = int(round(amount_dogs * 10 ** DOGS_DECIMALS))
    if amount_units <= 0:
        return "failed", "مبلغ DOGS برای ارسال خیلی کوچک است."

    async with payout_lock:
        system_balance, balance_info = await get_system_wallet_balance()
        required_balance = max(float(dogs_gas_fee_ton), 0.0)
        if system_balance is None:
            return "failed", f"موجودی TON ولت ربات قابل بررسی نیست: {balance_info}"
        if system_balance < required_balance:
            return "failed", (
                f"موجودی TON ولت ربات برای گس DOGS کافی نیست. موجودی فعلی: {system_balance:.4f} TON؛ "
                f"مبلغ موردنیاز: {required_balance:.4f} TON"
            )

        real_dogs_balance, dogs_wallet_info = await get_system_dogs_balance()
        if real_dogs_balance is None:
            return "failed", f"موجودی واقعی DOGS قابل بررسی نیست: {dogs_wallet_info}"
        if real_dogs_balance + 1e-9 < amount_dogs:
            return "failed", (
                f"موجودی DOGS ولت مرکزی کافی نیست. موجودی فعلی: {real_dogs_balance:.4f} DOGS؛ "
                f"مبلغ موردنیاز: {amount_dogs:.4f} DOGS"
            )

        system_dogs_wallet = await get_system_dogs_wallet_address()
        system_wallet = await get_system_wallet_address()
        if not system_dogs_wallet or not system_wallet:
            return "failed", "آدرس ولت مرکزی DOGS یا ولت TON قابل دریافت نیست."

        client = None
        transfer_started = False
        try:
            client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await client.connect()
            wallet = await WalletV5R1.from_mnemonic(
                client, TON_MNEMONIC.strip().split(), network_global_id=-239
            )
            seqno_before = await wallet.get_seqno()
            body = build_dogs_transfer_body(
                amount_units, destination_address.strip(), system_wallet,
                f"DOGS payout {uuid.uuid4().hex[:12]}"
            )
            transfer_started = True
            await wallet.transfer(
                destination=system_dogs_wallet,
                amount=int(round(required_balance * 10 ** 9)),
                body=body
            )
            await close_lite_client(client)
            client = None

            if await wait_for_wallet_seqno(seqno_before):
                return "sent", (
                    f"ارسال {amount_dogs:.4f} DOGS روی شبکه ثبت و تأیید شد؛ "
                    "نمایش تراکنش ممکن است چند ثانیه زمان ببرد."
                )
            return "uncertain", "پیام DOGS ارسال شده اما تأیید شبکه هنوز دریافت نشده است؛ مبلغ رزرو می‌ماند."
        except Exception as e:
            logging.error(f"DOGS payout error: {e}")
            await close_lite_client(client)
            if transfer_started:
                return "uncertain", "نتیجه ارسال DOGS قطعی نیست؛ مبلغ برای بررسی بیشتر رزرو می‌ماند."
            return "failed", str(e)

async def notify_wallet_issue(amount: float, reason: str, withdrawal_id: str = None, asset: str = "TON"):
    """هشدار قابل پیگیری برای ادمین هنگام توقف یا شکست برداشت."""
    unit = "DOGS" if asset.upper() == "DOGS" else "TON"
    request_line = f"\n🆔 <b>شناسه درخواست:</b> <code>{html.escape(str(withdrawal_id))}</code>" if withdrawal_id else ""
    alert = (
        f"🚨 <b>هشدار برداشت {unit}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>مبلغ موردنیاز:</b> <code>{amount:.4f} {unit}</code>\n"
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



async def get_system_dogs_wallet_address():
    """Return the DOGS Jetton Wallet owned by the configured central TON wallet."""
    global system_dogs_wallet_address
    if system_dogs_wallet_address:
        return system_dogs_wallet_address

    owner_address = (DOGS_OWNER_WALLET_ADDRESS or "").strip()
    if not owner_address:
        return None

    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()
        result = await client.run_get_method(
            address=DOGS_JETTON_MASTER,
            method="get_wallet_address",
            stack=[Address(owner_address).to_cell().begin_parse()]
        )
        if not result:
            return None
        jetton_address = result[0].load_address()
        if not jetton_address:
            return None
        system_dogs_wallet_address = jetton_address.to_str(
            is_user_friendly=True, is_bounceable=False
        )
        return system_dogs_wallet_address
    except Exception as e:
        logging.error(f"DOGS Jetton wallet address lookup failed: {e}")
        return None
    finally:
        await close_lite_client(client)

async def get_system_dogs_balance():
    """Read the real DOGS balance from the central Jetton Wallet."""
    jetton_wallet_address = await get_system_dogs_wallet_address()
    if not jetton_wallet_address:
        return None, "آدرس Jetton Wallet مرکزی DOGS قابل دریافت نیست."

    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()
        result = await client.run_get_method(
            address=jetton_wallet_address,
            method="get_wallet_data",
            stack=[]
        )
        if not result:
            return None, "قرارداد DOGS پاسخی برای موجودی نداد."
        amount_units = int(result[0])
        return amount_units / 10 ** DOGS_DECIMALS, jetton_wallet_address
    except Exception as e:
        logging.error(f"DOGS balance lookup failed: {e}")
        return None, str(e)
    finally:
        await close_lite_client(client)

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



async def credit_dogs_deposit_transaction(tx_id: str, user_id: int, amount_units: int,
                                           memo: str, lt):
    """Credit one DOGS transfer exactly once using the transaction id as an idempotency key."""
    amount_units = int(amount_units)
    amount_dogs = round(amount_units / 10 ** DOGS_DECIMALS, 4)
    if amount_units <= 0 or amount_dogs <= 0:
        return False

    now = datetime.utcnow().isoformat()
    session = None
    try:
        session = await mongo_client.start_session()
        async with session.start_transaction():
            if await deposits_col.find_one({"tx_id": tx_id}, session=session):
                return False
            await deposits_col.insert_one({
                "tx_id": tx_id,
                "asset": "DOGS",
                "user_id": user_id,
                "memo": memo,
                "amount_units": amount_units,
                "amount_dogs": amount_dogs,
                "lt": lt,
                "status": "credited",
                "created_at": now,
                "credited_at": now
            }, session=session)
            # Do not update dogs_balance through both $inc and $setOnInsert.
            await users_col.update_one(
                {"user_id": user_id},
                {
                    "$inc": {"dogs_balance": amount_dogs},
                    "$setOnInsert": {
                        "user_id": user_id,
                        "balance": 0.0,
                        "username": "",
                        "first_name": "User"
                    }
                },
                upsert=True,
                session=session
            )
    except Exception as e:
        logging.error(f"DOGS deposit credit failed for {tx_id}: {e}")
        return False
    finally:
        if session:
            await session.end_session()

    prof = get_user_profile(user_id)
    prof["dogs_balance"] = round(float(prof.get("dogs_balance", 0.0)) + amount_dogs, 4)
    try:
        await bot.send_message(
            user_id,
            f"✅ <b>واریز DOGS تأیید شد.</b>\n🐶 مبلغ افزوده‌شده: <code>{amount_dogs:.4f} DOGS</code>\n"
            f"💰 موجودی DOGS جدید: <code>{prof['dogs_balance']:.4f} DOGS</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass
    return True

def extract_forward_payload_comment(parser) -> str:
    """Read a Jetton forward_payload comment from inline or referenced payloads."""
    try:
        if parser.remaining_bits < 1:
            return ""
        is_ref = parser.load_uint(1)
        if is_ref:
            if getattr(parser, "remaining_refs", 0) < 1:
                return ""
            payload = parser.load_ref().begin_parse()
        else:
            payload = parser
        if payload.remaining_bits < 32 or payload.load_uint(32) != 0:
            return ""
        return payload.load_snake_string().strip()
    except Exception as e:
        logging.debug(f"Jetton comment parse skipped: {e}")
        return ""


def parse_dogs_incoming_transfer(tx):
    """Parse standard Jetton internal_transfer and transfer_notification messages."""
    try:
        in_msg = get_object_field(tx, "in_msg")
        info = get_object_field(in_msg, "info")
        if get_object_field(info, "bounced", False):
            return None
        description = get_object_field(tx, "description")
        if get_object_field(description, "aborted", False):
            return None
        body = get_object_field(in_msg, "body")
        if not body:
            return None

        parser = body.begin_parse()
        if parser.remaining_bits < 32:
            return None
        opcode = parser.load_uint(32)
        if opcode not in (0x178d4519, 0x7362d09c):
            return None
        if parser.remaining_bits < 64:
            return None
        parser.load_uint(64)  # query_id
        amount_units = int(parser.load_coins())
        if amount_units <= 0:
            return None

        if opcode == 0x178d4519:  # internal_transfer
            parser.load_address()  # sender jetton wallet
            parser.load_address()  # response destination
            parser.load_coins()    # forward_ton_amount
        else:  # transfer_notification
            parser.load_address()  # sender owner

        memo = extract_forward_payload_comment(parser)
        match = re.fullmatch(r"VG-(\d+)", memo)
        if not match:
            return None
        return amount_units, memo, int(match.group(1))
    except Exception as e:
        logging.debug(f"DOGS transfer parse skipped: {e}")
        return None


async def scan_incoming_dogs_deposits():
    """Scan the central DOGS Jetton Wallet and credit memo-tagged deposits once."""
    jetton_wallet_address = await get_system_dogs_wallet_address()
    if not jetton_wallet_address:
        return

    async with dogs_deposit_scan_lock:
        client = None
        try:
            client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await client.connect()
            # Read a wider window so normal bursts do not fall through between scans.
            transactions = await client.get_transactions(
                address=jetton_wallet_address, count=256
            )
            for tx in transactions:
                parsed = parse_dogs_incoming_transfer(tx)
                if not parsed:
                    continue
                amount_units, memo, user_id = parsed
                lt = get_object_field(tx, "lt")
                if lt in (None, ""):
                    continue
                tx_id = f"DOGS:{jetton_wallet_address}:{lt}"
                await credit_dogs_deposit_transaction(
                    tx_id, user_id, amount_units, memo, lt
                )
        except Exception as e:
            logging.error(f"Incoming DOGS deposit scan failed: {e}")
        finally:
            await close_lite_client(client)

async def deposit_tracker_loop():
    await asyncio.sleep(12)
    while True:
        try:
            await scan_incoming_deposits()
            await scan_incoming_dogs_deposits()
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
                        withdrawal_id,
                        asset=updated.get("asset", "TON")
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
                "dogs_balance": info.get("dogs_balance", 0.0),
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
            "dogs_gas_fee_ton": dogs_gas_fee_ton,
            "dogs_min_withdraw_amount": dogs_min_withdraw_amount,
            "dogs_max_withdraw_amount": dogs_max_withdraw_amount,
            "dogs_withdrawals_enabled": dogs_withdrawals_enabled,
            "tracker_message_id": tracker_message_id
        }
        await settings_col.update_one({"setting_id": "global_config"}, {"$set": settings_doc}, upsert=True)

    except Exception as e:
        logging.error(f"Error saving data to MongoDB: {e}")

async def save_user_data(user_id: int):
    """Persist only the user touched by /start instead of rewriting every user."""
    try:
        info = user_data.get(user_id)
        if info is None:
            return
        user_doc = {
            "user_id": user_id,
            "balance": info.get("balance", 0.0),
            "dogs_balance": info.get("dogs_balance", 0.0),
            "username": info.get("username", ""),
            "first_name": info.get("first_name", "User")
        }
        if info.get("started_at"):
            user_doc["started_at"] = info["started_at"]
        await users_col.update_one({"user_id": user_id}, {"$set": user_doc}, upsert=True)
        await settings_col.update_one(
            {"setting_id": "global_config"},
            {"$set": {
                "all_time_users": list(all_time_users),
                "banned_users": list(banned_users)
            }},
            upsert=True
        )
    except Exception as e:
        logging.error(f"Error saving /start data to MongoDB: {e}")

async def load_data():
    global user_data, all_time_users, banned_users, required_channels, bot_active, withdrawals_enabled, min_withdraw_amount, max_withdraw_amount, ton_gas_fee, dogs_gas_fee_ton, dogs_min_withdraw_amount, dogs_max_withdraw_amount, dogs_withdrawals_enabled, tracker_message_id
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
            dogs_gas_fee_ton = settings_doc.get("dogs_gas_fee_ton", 0.05)
            dogs_min_withdraw_amount = settings_doc.get("dogs_min_withdraw_amount", 1000.0)
            dogs_max_withdraw_amount = settings_doc.get("dogs_max_withdraw_amount", 1000000.0)
            dogs_withdrawals_enabled = settings_doc.get("dogs_withdrawals_enabled", True)
            tracker_message_id = settings_doc.get("tracker_message_id", None)

        async for user_doc in users_col.find():
            u_id = int(user_doc["user_id"])
            user_data[u_id] = {
                "balance": round(float(user_doc.get("balance", 0.0) or 0.0), 4),
                "dogs_balance": round(float(user_doc.get("dogs_balance", 0.0) or 0.0), 4),
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

class DogsDepositForm(StatesGroup):
    amount = State()

class DogsWithdrawForm(StatesGroup):
    amount = State()
    wallet_address = State()

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

class AdminSetDogsGasFeeForm(StatesGroup):
    amount = State()

class AdminSetMinDogsWithdrawForm(StatesGroup):
    amount = State()

class AdminSetMaxDogsWithdrawForm(StatesGroup):
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
            "dogs_balance": 0.0,
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
            [InlineKeyboardButton(text="⛽️ تنظیم گس‌فی شبکه TON", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text="🐶 گس‌فی برداشت DOGS", callback_data="admin_set_dogs_gas_fee")],
            [InlineKeyboardButton(text="🐶 حداقل برداشت DOGS", callback_data="admin_set_min_dogs_wd"), InlineKeyboardButton(text="🐶 حداکثر برداشت DOGS", callback_data="admin_set_max_dogs_wd")],
            [InlineKeyboardButton(text=("🛑 خاموش‌کردن برداشت DOGS" if dogs_withdrawals_enabled else "✅ روشن‌کردن برداشت DOGS"), callback_data="admin_toggle_dogs_withdrawals")],
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

async def complete_start_response(message: types.Message, loading_message: types.Message, u_id: int):
    """Finish /start after the immediate acknowledgement has already been sent."""
    try:
        if not bot_active and not is_admin(u_id):
            await loading_message.edit_text(
                "🛠️ <b>ربات موقتاً در حالت تعمیر و ارتقاست.</b>\nخیلی زود برمی‌گردیم؛ موجودی شما کاملاً محفوظ است.",
                parse_mode="HTML"
            )
            return

        is_subscribed = await check_user_subscription(u_id)
        if not is_subscribed:
            await loading_message.edit_text(
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
        try:
            await loading_message.delete()
        except Exception as e:
            logging.warning(f"Could not delete /start loading message: {e}")
    except Exception as e:
        logging.exception(f"/start background response failed for {u_id}: {e}")
        try:
            await loading_message.edit_text(
                "⚠️ پاسخ نهایی دیر شد؛ لطفاً دوباره /start را بفرست.",
                parse_mode="HTML"
            )
        except Exception:
            pass


@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>دسترسی این حساب متوقف شده است.</b>\nاگر فکر می‌کنی اشتباهی رخ داده، با پشتیبانی تماس بگیر.", parse_mode="HTML")
        return

    profile = get_user_profile(u_id, message.from_user)
    profile["started_at"] = profile.get("started_at") or datetime.utcnow().isoformat()
    all_time_users.add(u_id)

    # Send the acknowledgement before any database or network check.
    loading_message = await message.answer("⏳ <b>در حال آماده‌سازی ربات...</b>", parse_mode="HTML")
    asyncio.create_task(save_user_data(u_id))
    asyncio.create_task(complete_start_response(message, loading_message, u_id))


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


async def transfer_user_balance(sender_id: int, recipient_id: int, amount: float,
                                recipient: types.User, asset: str = "TON"):
    """Atomically transfer either TON or DOGS between two started users."""
    asset = "DOGS" if str(asset).upper() == "DOGS" else "TON"
    balance_field = "dogs_balance" if asset == "DOGS" else "balance"
    transfer_id = "TR-" + uuid.uuid4().hex[:16].upper()
    now = datetime.utcnow().isoformat()
    session = None
    try:
        session = await mongo_client.start_session()
        async with session.start_transaction():
            sender_after = await users_col.find_one_and_update(
                {"user_id": sender_id, balance_field: {"$gte": amount}},
                {"$inc": {balance_field: -amount}},
                return_document=ReturnDocument.AFTER,
                session=session
            )
            if not sender_after:
                return None, "insufficient_balance"

            recipient_after = await users_col.find_one_and_update(
                {"user_id": recipient_id},
                {
                    "$inc": {balance_field: amount},
                    "$setOnInsert": {
                        "user_id": recipient_id,
                        "username": recipient.username or "",
                        "first_name": recipient.first_name or "User",
                        "started_at": now
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
                session=session
            )
            await transfers_col.insert_one({
                "transfer_id": transfer_id,
                "sender_id": sender_id,
                "recipient_id": recipient_id,
                "asset": asset,
                "amount": round(amount, 4),
                "status": "completed",
                "created_at": now
            }, session=session)

        sender_profile = get_user_profile(sender_id)
        sender_profile[balance_field] = round(float(sender_after.get(balance_field, 0.0)), 4)
        recipient_profile = get_user_profile(recipient_id, recipient)
        recipient_profile[balance_field] = round(float(recipient_after.get(balance_field, 0.0)), 4)
        return transfer_id, "ok"
    except Exception as e:
        logging.error(f"{asset} balance transfer failed from {sender_id} to {recipient_id}: {e}")
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
        await message.answer(
            "⚠️ روی پیام کاربر مقصد reply کن و بنویس: <code>wallet 100 dogs</code> یا <code>wallet 0.01 ton</code>",
            parse_mode="HTML"
        )
        return
    if recipient.id == sender.id:
        await message.answer("⚠️ انتقال موجودی به خودت امکان‌پذیر نیست.")
        return

    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in (2, 3):
        await message.answer(
            "⚠️ فرمت صحیح: <code>wallet 100 dogs</code> یا <code>wallet 0.01 ton</code>",
            parse_mode="HTML"
        )
        return

    try:
        amount = float(parts[1])
    except (TypeError, ValueError):
        amount = 0
    asset_text = parts[2].lower() if len(parts) == 3 else "ton"
    if asset_text in ("dog", "dogs"):
        asset = "DOGS"
        amount = round(amount, 4)
    elif asset_text in ("ton", "t"):
        asset = "TON"
        amount = round(amount, 4)
    else:
        await message.answer("⚠️ واحد معتبر فقط TON یا DOGS است.")
        return

    if not math.isfinite(amount) or amount <= 0:
        await message.answer("⚠️ مقدار انتقال باید یک عدد مثبت باشد.")
        return

    if not await has_started_bot(recipient.id):
        target_name = html.escape(recipient.full_name or "کاربر")
        await message.answer(
            f"⚠️ <a href=\"tg://user?id={recipient.id}\">{target_name}</a> هنوز ربات را Start نکرده است.\n"
            "ابتدا در خصوصی ربات دستور /start را بفرستد؛ هیچ مبلغی کم نشد.",
            parse_mode="HTML"
        )
        return

    transfer_id, result = await transfer_user_balance(
        sender.id, recipient.id, amount, recipient, asset=asset
    )
    unit = asset
    if result == "insufficient_balance":
        await message.answer(f"💰 موجودی {unit} برای این انتقال کافی نیست؛ هیچ مبلغی کم نشد.")
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
        f"💎 مبلغ: <code>{amount:.4f} {unit}</code>"
    )
    await message.answer(group_text, parse_mode="HTML")
    try:
        await bot.send_message(
            recipient.id,
            "✅ <b>یک انتقال موجودی برایت انجام شد.</b>\n"
            f"👤 از طرف: {sender_name}\n"
            f"💎 مبلغ دریافت‌شده: <code>{amount:.4f} {unit}</code>\n"
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
                                   amount_to_send: float, deducted_amount: float,
                                   asset: str = "TON", fee_ton: float = 0.0) -> str:
    withdrawal_id = uuid.uuid4().hex[:16]
    now = datetime.utcnow().isoformat()
    await withdrawals_col.insert_one({
        "withdrawal_id": withdrawal_id,
        "user_id": user_id,
        "wallet_address": wallet_address,
        "asset": asset,
        "fee_ton": round(fee_ton, 4),
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
async def show_wallet(message: types.Message, user_id: int = None):
    u_id = user_id or message.from_user.id
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
        f"💰 <b>موجودی TON:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"🐶 <b>موجودی DOGS:</b> <code>{prof.get('dogs_balance', 0.0):.4f} DOGS</code>\n"
        f"⚡️ <b>کارمزد برداشت TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🐶 <b>کارمزد برداشت DOGS:</b> <code>{dogs_gas_fee_ton} TON</code>\n"
        f"🔻 <b>حداقل/حداکثر TON:</b> <code>{min_withdraw_amount} / {max_withdraw_amount}</code>\n"
        f"🔻 <b>حداقل/حداکثر DOGS:</b> <code>{dogs_min_withdraw_amount} / {dogs_max_withdraw_amount}</code>\n━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ واریز TON", callback_data="start_deposit"), InlineKeyboardButton(text="🐶 واریز DOGS", callback_data="start_dogs_deposit")],
            [InlineKeyboardButton(text="🚀 برداشت TON", callback_data="start_withdraw"), InlineKeyboardButton(text="🐶 برداشت DOGS", callback_data="start_dogs_withdraw")]
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


@dp.callback_query(F.data == "start_dogs_withdraw")
async def start_dogs_withdraw_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id
    if is_banned(u_id):
        await call.answer("🚫 این حساب دسترسی فعال ندارد.", show_alert=True)
        return
    if not bot_active and not is_admin(u_id):
        await call.answer("🛠️ ربات موقتاً در حال ارتقاست.", show_alert=True)
        return
    if not dogs_withdrawals_enabled and not is_admin(u_id):
        await call.answer("🛑 برداشت DOGS موقتاً خاموش است.", show_alert=True)
        return
    if not await check_user_subscription(u_id):
        await call.answer("🔐 برای برداشت، عضویت در همه کانال‌ها الزامی است!", show_alert=True)
        return

    prof = get_user_profile(u_id, call.from_user)
    dogs_balance = float(prof.get("dogs_balance", 0.0))
    fee_ton = max(float(dogs_gas_fee_ton), 0.0)
    if dogs_balance < dogs_min_withdraw_amount:
        await call.answer(f"🐶 حداقل موجودی لازم {dogs_min_withdraw_amount:.4f} DOGS است.", show_alert=True)
        return
    if float(prof.get("balance", 0.0)) < fee_ton:
        await call.answer(f"⛽️ برای کارمزد برداشت حداقل {fee_ton:.4f} TON لازم داری.", show_alert=True)
        return

    await call.answer()
    await state.set_state(DogsWithdrawForm.amount)
    await call.message.answer(
        f"🐶 <b>موجودی DOGS:</b> <code>{dogs_balance:.4f}</code>\n"
        f"🔻 حداقل: <code>{dogs_min_withdraw_amount:.4f}</code> | 🔝 حداکثر: <code>{dogs_max_withdraw_amount:.4f}</code>\n"
        f"⛽️ کارمزد از موجودی TON: <code>{fee_ton:.4f} TON</code>\n\n"
        "مقدار DOGS برای برداشت را وارد کن:",
        parse_mode="HTML"
    )

@dp.message(DogsWithdrawForm.amount)
async def process_dogs_withdraw_amount(message: types.Message, state: FSMContext):
    if is_banned(message.from_user.id):
        await state.clear()
        return
    try:
        req_amount = round(float((message.text or "").strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ لطفاً یک عدد معتبر DOGS وارد کن.")
        return
    if not math.isfinite(req_amount) or req_amount <= 0:
        await message.answer("⚠️ مبلغ DOGS باید بیشتر از صفر باشد.")
        return
    if req_amount < dogs_min_withdraw_amount:
        await message.answer(f"🔻 حداقل برداشت <code>{dogs_min_withdraw_amount:.4f} DOGS</code> است.", parse_mode="HTML")
        return
    if req_amount > dogs_max_withdraw_amount:
        await message.answer(f"🔝 سقف برداشت <code>{dogs_max_withdraw_amount:.4f} DOGS</code> است.", parse_mode="HTML")
        return

    prof = get_user_profile(message.from_user.id, message.from_user)
    if req_amount > float(prof.get("dogs_balance", 0.0)):
        await message.answer("🐶 مبلغ درخواستی از موجودی DOGS فعلی‌ات بیشتر است.")
        return
    fee_ton = max(float(dogs_gas_fee_ton), 0.0)
    if float(prof.get("balance", 0.0)) < fee_ton:
        await message.answer(f"⛽️ برای این برداشت حداقل <code>{fee_ton:.4f} TON</code> لازم داری.", parse_mode="HTML")
        return

    await state.update_data(
        requested_amount=req_amount,
        amount_to_send=req_amount,
        deducted_amount=req_amount,
        fee_ton=fee_ton
    )
    await state.set_state(DogsWithdrawForm.wallet_address)
    await message.answer("📬 آدرس کیف‌پول TON مقصد را بفرست؛ آدرس باید با EQ یا UQ شروع شود:")

async def reserve_dogs_withdrawal(user_id: int, dogs_amount: float, fee_ton: float) -> bool:
    """Atomically reserve DOGS plus the TON gas fee from one user."""
    dogs_amount = round(float(dogs_amount), 4)
    fee_ton = round(max(float(fee_ton), 0.0), 4)
    prof = get_user_profile(user_id)
    await users_col.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {
            "user_id": user_id,
            "balance": round(float(prof.get("balance", 0.0)), 4),
            "dogs_balance": round(float(prof.get("dogs_balance", 0.0)), 4),
            "username": prof.get("username", ""),
            "first_name": prof.get("first_name", "User")
        }},
        upsert=True
    )
    result = await users_col.update_one(
        {
            "user_id": user_id,
            "dogs_balance": {"$gte": dogs_amount},
            "balance": {"$gte": fee_ton}
        },
        {"$inc": {"dogs_balance": -dogs_amount, "balance": -fee_ton}}
    )
    if result.matched_count != 1:
        return False
    prof["dogs_balance"] = round(float(prof.get("dogs_balance", 0.0)) - dogs_amount, 4)
    prof["balance"] = round(float(prof.get("balance", 0.0)) - fee_ton, 4)
    return True

async def refund_dogs_withdrawal(withdrawal_id: str, reason: str, allowed_statuses=("failed",)) -> bool:
    """Refund a DOGS withdrawal only once, based on its previous status."""
    status_filter = allowed_statuses[0] if len(allowed_statuses) == 1 else {"$in": list(allowed_statuses)}
    withdrawal = await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": status_filter, "asset": "DOGS"},
        {"$set": {
            "status": "refunded",
            "refund_reason": reason,
            "updated_at": datetime.utcnow().isoformat()
        }},
        return_document=ReturnDocument.BEFORE
    )
    if not withdrawal:
        return False

    user_id = int(withdrawal["user_id"])
    dogs_amount = round(float(withdrawal.get("deducted_amount", 0.0)), 4)
    fee_ton = round(float(withdrawal.get("fee_ton", 0.0)), 4)
    await users_col.update_one(
        {"user_id": user_id},
        {"$inc": {"dogs_balance": dogs_amount, "balance": fee_ton}},
        upsert=True
    )
    prof = get_user_profile(user_id)
    prof["dogs_balance"] = round(float(prof.get("dogs_balance", 0.0)) + dogs_amount, 4)
    prof["balance"] = round(float(prof.get("balance", 0.0)) + fee_ton, 4)
    return True

@dp.message(DogsWithdrawForm.wallet_address)
async def process_dogs_withdraw_address(message: types.Message, state: FSMContext):
    user = message.from_user
    if is_banned(user.id):
        await state.clear()
        return
    if not dogs_withdrawals_enabled and not is_admin(user.id):
        await state.clear()
        await message.answer("🛑 برداشت DOGS موقتاً خاموش است؛ موجودی شما محفوظ است.", reply_markup=get_main_keyboard(user.id))
        return

    wallet_addr = (message.text or "").strip()
    if not is_valid_ton_address(wallet_addr):
        await message.answer("⚠️ این آدرس TON معتبر نیست؛ آدرس کامل EQ یا UQ با checksum صحیح بفرست.")
        return

    data = await state.get_data()
    requested_amount = data.get("requested_amount")
    amount_to_send = data.get("amount_to_send")
    fee_ton = data.get("fee_ton", max(dogs_gas_fee_ton, 0.0))
    if None in (requested_amount, amount_to_send):
        await state.clear()
        await message.answer("⏱️ نشست برداشت منقضی شد؛ دوباره از کیف‌پول شروع کن.")
        return

    withdrawal_id = None
    try:
        async with withdrawal_flow_lock:
            withdrawal_id = await create_withdrawal_record(
                user.id,
                wallet_addr,
                float(requested_amount),
                float(amount_to_send),
                float(requested_amount),
                asset="DOGS",
                fee_ton=float(fee_ton)
            )
            reserved = await reserve_dogs_withdrawal(
                user.id, float(requested_amount), float(fee_ton)
            )
            if not reserved:
                await set_withdrawal_status(
                    withdrawal_id, "failed",
                    last_error="موجودی DOGS یا TON کافی نبود"
                )
                await state.clear()
                await message.answer(
                    "💰 موجودی DOGS یا TON برای ثبت این برداشت کافی نیست؛ مبلغی کم نشد.",
                    reply_markup=get_main_keyboard(user.id)
                )
                return
            await set_withdrawal_status(
                withdrawal_id,
                "processing",
                reserved_at=datetime.utcnow().isoformat()
            )
    except Exception as e:
        logging.error(f"DOGS withdrawal reservation error: {e}")
        if withdrawal_id:
            try:
                await set_withdrawal_status(
                    withdrawal_id, "failed", last_error="خطا هنگام رزرو برداشت"
                )
                await refund_dogs_withdrawal(
                    withdrawal_id,
                    "خطا هنگام رزرو برداشت",
                    allowed_statuses=("failed",)
                )
            except Exception as refund_error:
                logging.error(f"DOGS withdrawal reservation refund error: {refund_error}")
        await state.clear()
        await message.answer(
            "⚠️ ثبت برداشت DOGS کامل نشد؛ اگر مبلغی رزرو شده بود، بررسی می‌شود.",
            reply_markup=get_main_keyboard(user.id)
        )
        return

    await state.clear()
    await message.answer(
        f"🚀 <b>برداشت DOGS ثبت شد.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
        f"مبلغ: <code>{float(amount_to_send):.4f} DOGS</code>\n"
        "در حال ارسال امن به شبکه TON...",
        parse_mode="HTML"
    )

    payout_status, result_msg = await send_dogs_payout(
        wallet_addr, float(amount_to_send)
    )
    if payout_status == "sent":
        await set_withdrawal_status(
            withdrawal_id,
            "sent",
            sent_at=datetime.utcnow().isoformat(),
            result_message=result_msg
        )
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send),
            "✅ برداشت DOGS ارسال شد.", result_msg, asset="DOGS"
        )
        await message.answer(
            f"🎉 <b>برداشت DOGS با موفقیت ارسال شد!</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            f"مبلغ: <code>{float(amount_to_send):.4f} DOGS</code>",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )
    elif payout_status == "uncertain":
        await set_withdrawal_status(
            withdrawal_id,
            "pending_verification",
            last_error=result_msg,
            verification_required_at=datetime.utcnow().isoformat()
        )
        await notify_wallet_issue(
            float(amount_to_send), result_msg, withdrawal_id, asset="DOGS"
        )
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send),
            "⏳ نتیجه برداشت DOGS نیاز به بررسی شبکه دارد.",
            result_msg, asset="DOGS"
        )
        await message.answer(
            f"⏳ <b>برداشت DOGS در حال بررسی شبکه است.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            "برای جلوگیری از پرداخت دوباره، DOGS و کارمزد TON فعلاً رزرو می‌مانند.",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )
    else:
        await set_withdrawal_status(
            withdrawal_id, "failed", last_error=result_msg
        )
        refunded = await refund_dogs_withdrawal(
            withdrawal_id, result_msg, allowed_statuses=("failed",)
        )
        refund_text = (
            "موجودی DOGS و کارمزد TON خودکار برگشت داده شد."
            if refunded else
            "وضعیت برای بررسی ایمن ثبت شده است."
        )
        await notify_wallet_issue(
            float(amount_to_send), result_msg, withdrawal_id, asset="DOGS"
        )
        await notify_withdrawal_result(
            withdrawal_id, user.id, float(amount_to_send),
            "⚠️ برداشت DOGS ارسال نشد و refund انجام شد.",
            result_msg, asset="DOGS"
        )
        await message.answer(
            f"⚠️ <b>برداشت DOGS ارسال نشد.</b>\nشناسه: <code>{withdrawal_id}</code>\n"
            f"علت: {html.escape(str(result_msg))}\n{refund_text}",
            parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
        )

async def notify_withdrawal_result(withdrawal_id: str, user_id: int, amount: float,
                                      status_text: str, detail: str = "", asset: str = "TON"):
    """ثبت نتیجه هر برداشت خودکار در کانال عملیاتی با واحد درست."""
    unit = "DOGS" if asset.upper() == "DOGS" else "TON"
    try:
        text = (
            f"🤖 <b>گزارش برداشت خودکار {unit}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🆔 شناسه: <code>{html.escape(str(withdrawal_id))}</code>\n"
            f"👤 کاربر: <code>{user_id}</code>\n"
            f"💎 مبلغ: <code>{amount:.4f} {unit}</code>\n"
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



@dp.callback_query(F.data == "start_dogs_deposit")
async def start_dogs_deposit_callback(call: types.CallbackQuery, state: FSMContext):
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
    if not DOGS_OWNER_WALLET_ADDRESS:
        await call.answer("⚠️ آدرس ولت مرکزی DOGS تنظیم نشده است.", show_alert=True)
        return
    if not await get_system_dogs_wallet_address():
        await call.answer("⚠️ Jetton Wallet مرکزی DOGS فعلاً قابل دریافت نیست؛ بعداً دوباره تلاش کن.", show_alert=True)
        return
    await call.answer()
    await state.set_state(DogsDepositForm.amount)
    await call.message.answer(
        "🐶 <b>مقدار DOGS برای واریز را وارد کن.</b>\n"
        "لینک امن واریز با memo اختصاصی تو ساخته می‌شود.",
        parse_mode="HTML"
    )

@dp.message(DogsDepositForm.amount)
async def process_dogs_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = round(float((message.text or "").strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ لطفاً مقدار معتبر DOGS وارد کن.")
        return
    if not math.isfinite(amount) or amount <= 0:
        await message.answer("⚠️ مقدار DOGS باید بیشتر از صفر باشد.")
        return

    amount_units = int(round(amount * 10 ** DOGS_DECIMALS))
    if amount_units <= 0:
        await message.answer("⚠️ مقدار DOGS برای انتقال خیلی کوچک است.")
        return
    memo = get_deposit_memo(message.from_user.id)
    owner = quote(DOGS_OWNER_WALLET_ADDRESS.strip(), safe="")
    jetton = quote(DOGS_JETTON_MASTER, safe="")
    text = quote(memo, safe="")
    ton_uri = f"ton://transfer/{owner}?jetton={jetton}&amount={amount_units}&text={text}"
    tonkeeper_link = f"https://app.tonkeeper.com/transfer/{owner}?jetton={jetton}&amount={amount_units}&text={text}"
    dogs_wallet = await get_system_dogs_wallet_address()
    if not dogs_wallet:
        await state.clear()
        await message.answer("⚠️ Jetton Wallet مرکزی DOGS فعلاً قابل دریافت نیست؛ بعداً دوباره تلاش کن.")
        return

    await state.clear()
    await message.answer(
        "🐶 <b>واریز DOGS آماده است</b>\n\n"
        f"🐶 مبلغ: <code>{amount:.4f} DOGS</code>\n"
        f"🧾 Memo: <code>{memo}</code>\n"
        f"📬 ولت مالک مرکزی: <code>{html.escape(DOGS_OWNER_WALLET_ADDRESS)}</code>\n"
        f"🧩 Jetton Wallet دریافت‌کننده: <code>{html.escape(dogs_wallet)}</code>\n\n"
        "memo را تغییر نده؛ بعد از ثبت تراکنش، واریز خودکار به موجودی اضافه می‌شود.",
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🐶 بازکردن Tonkeeper", url=tonkeeper_link)],
            [InlineKeyboardButton(text="📲 بازکردن کیف‌پول TON", url=ton_uri)],
            [InlineKeyboardButton(text="🔙 بازگشت به کیف‌پول", callback_data="back_to_wallet")]
        ])
    )

@dp.callback_query(F.data == "back_to_wallet")
async def back_to_wallet_callback(call: types.CallbackQuery):
    await call.answer()
    try:
        await call.message.delete()
    except Exception:
        pass
    await show_wallet(call.message, call.from_user.id)


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
    total_dogs_balance = sum(u.get("dogs_balance", 0.0) for u in user_data.values())

    sys_balance, wallet_addr = await get_system_wallet_balance()
    dogs_balance, dogs_wallet_addr = await get_system_dogs_balance()
    ton_wallet_str = (
        f"💎 <b>موجودی واقعی:</b> <code>{sys_balance:.4f} TON</code>\n"
        f"💳 <b>آدرس ولت TON:</b> <code>{wallet_addr}</code>"
        if sys_balance is not None else
        f"⚠️ <b>خطای استعلام TON:</b> {html.escape(str(wallet_addr))}"
    )
    dogs_wallet_str = (
        f"🐶 <b>موجودی واقعی:</b> <code>{dogs_balance:.4f} DOGS</code>\n"
        f"🧾 <b>Jetton Wallet DOGS:</b> <code>{dogs_wallet_addr}</code>"
        if dogs_balance is not None else
        f"⚠️ <b>خطای استعلام DOGS:</b> {html.escape(str(dogs_wallet_addr))}"
    )
    wallet_str = ton_wallet_str + "\n" + dogs_wallet_str

    ch_list_str = ", ".join(required_channels) if required_channels else "هیچ کانالی تنظیم نشده است."

    admin_text = (
        "👑 <b>مرکز فرماندهی Void Giveaway</b> 🚀\n<code>v6.1.0</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>موجودی واقعی ولت اصلی ربات:</b>\n{wallet_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>وضعیت ربات:</b> {'روشن ✅' if bot_active else 'خاموش/تعمیرات 🛑'}\n"
        f"🚀 <b>برداشت خودکار:</b> {'فعال ✅' if withdrawals_enabled else 'خاموش 🛑'}\n"
        f"📢 <b>کانال‌های جوین اجباری ({len(required_channels)}):</b> {ch_list_str}\n"
        f"👥 <b>کاربران فعال فعلی:</b> <code>{total_users}</code> نفر\n"
        f"📜 <b>کل کاربران تاریخی:</b> <code>{total_all_time}</code> نفر\n"
        f"🚫 <b>کاربران بن شده:</b> <code>{banned_count}</code> نفر\n"
        f"💰 <b>مجموع موجودی ولت کاربران:</b> <code>{total_balance:.4f} TON</code>\n"
        f"🐶 <b>مجموع موجودی DOGS کاربران:</b> <code>{total_dogs_balance:.4f} DOGS</code>\n"
        f"⛽️ <b>گس‌فی شبکه TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت TON:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت TON:</b> <code>{max_withdraw_amount} TON</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🐶 <b>تنظیمات DOGS:</b> {'فعال ✅' if dogs_withdrawals_enabled else 'خاموش 🛑'}\n"
        f"⛽️ <b>گس‌فی برداشت DOGS:</b> <code>{dogs_gas_fee_ton} TON</code>\n"
        f"🔻 <b>حداقل برداشت DOGS:</b> <code>{dogs_min_withdraw_amount} DOGS</code>\n"
        f"🔝 <b>حداکثر برداشت DOGS:</b> <code>{dogs_max_withdraw_amount} DOGS</code>\n"
        f"🧾 <b>قرارداد DOGS:</b> <code>{DOGS_JETTON_MASTER}</code>\n"
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

@dp.callback_query(F.data == "admin_set_dogs_gas_fee")
async def start_set_dogs_gas_fee(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetDogsGasFeeForm.amount)
    await call.message.edit_text(
        f"🐶 <b>گس‌فی برداشت DOGS را به TON وارد کن</b>\nمقدار فعلی: <code>{dogs_gas_fee_ton} TON</code>",
        parse_mode="HTML"
    )

@dp.message(AdminSetDogsGasFeeForm.amount)
async def process_set_dogs_gas_fee(message: types.Message, state: FSMContext):
    global dogs_gas_fee_ton
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount <= 0:
            raise ValueError
        dogs_gas_fee_ton = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ گس‌فی برداشت DOGS روی <code>{dogs_gas_fee_ton} TON</code> تنظیم شد.", parse_mode="HTML")
    except (ValueError, AttributeError):
        await message.answer("⚠️ یک مقدار مثبت و معتبر به TON وارد کن.")

@dp.callback_query(F.data == "admin_set_min_dogs_wd")
async def start_set_min_dogs_wd(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMinDogsWithdrawForm.amount)
    await call.message.edit_text(
        f"🐶 <b>حداقل برداشت DOGS را وارد کن</b>\nمقدار فعلی: <code>{dogs_min_withdraw_amount} DOGS</code>",
        parse_mode="HTML"
    )

@dp.message(AdminSetMinDogsWithdrawForm.amount)
async def process_set_min_dogs_wd(message: types.Message, state: FSMContext):
    global dogs_min_withdraw_amount
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount <= 0 or amount >= dogs_max_withdraw_amount:
            raise ValueError
        dogs_min_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ حداقل برداشت DOGS روی <code>{dogs_min_withdraw_amount}</code> تنظیم شد.", parse_mode="HTML")
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار باید مثبت و کمتر از سقف برداشت DOGS باشد.")

@dp.callback_query(F.data == "admin_set_max_dogs_wd")
async def start_set_max_dogs_wd(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMaxDogsWithdrawForm.amount)
    await call.message.edit_text(
        f"🐶 <b>حداکثر برداشت DOGS را وارد کن</b>\nمقدار فعلی: <code>{dogs_max_withdraw_amount} DOGS</code>",
        parse_mode="HTML"
    )

@dp.message(AdminSetMaxDogsWithdrawForm.amount)
async def process_set_max_dogs_wd(message: types.Message, state: FSMContext):
    global dogs_max_withdraw_amount
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount <= dogs_min_withdraw_amount:
            raise ValueError
        dogs_max_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ حداکثر برداشت DOGS روی <code>{dogs_max_withdraw_amount}</code> تنظیم شد.", parse_mode="HTML")
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار باید بیشتر از حداقل برداشت DOGS باشد.")

@dp.callback_query(F.data == "admin_toggle_dogs_withdrawals")
async def toggle_dogs_withdrawals_callback(call: types.CallbackQuery):
    global dogs_withdrawals_enabled
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    dogs_withdrawals_enabled = not dogs_withdrawals_enabled
    await save_data()
    status_msg = "✅ برداشت DOGS روشن شد." if dogs_withdrawals_enabled else "🛑 برداشت DOGS خاموش شد."
    await call.answer(status_msg, show_alert=True)
    await open_admin_panel(call.message)


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
