# ==========================================
# Void Giveaway Bot - Version 6.2.0 (Fully Automatic TON Withdrawals)
# (Multi-Channel Forced Join, Live Wallet Tracker, Direct Admin DM, Ban System, MongoDB Integrated)
# ==========================================

import asyncio
import base64
import json
import os
import time
import logging
import html
import math
import re
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote
from urllib.request import Request, urlopen
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, CommandObject, Command
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
    return "⚡ Void Giveaway Bot (v6.2.0) is running smoothly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6879499219]
BOT_VERSION = "6.3.0"
WITHDRAW_CHANNEL = "@voidwithraw"
WALLET_TRACKER_CHANNEL = "@Voidchanneloffical"  # کانال ارسال و بروزرسانی خودکار موجودی ولت سیستم
TON_MNEMONIC = os.environ.get("TON_MNEMONIC")
# DOGS Jetton روی شبکه اصلی TON؛ decimals رسمی این توکن ۹ است.
DOGS_JETTON_MASTER = "EQCvxJy4eG8hyHBFsZ7eePxrRsUQSFE_jpptRAYBmcG_DOGS"
DOGS_OWNER_WALLET_ADDRESS = os.environ.get("DOGS_OWNER_WALLET_ADDRESS", "")
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
support_tickets_col = db['support_tickets']
price_alerts_col = db['price_alerts']


bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_data = {}
all_time_users = set()
banned_users = set()
required_channels = ["@Voidchanneloffical"]  # پشتیبانی از چند کانال جوین اجباری

# قیمت ارزها برای استفاده در گروه‌ها
CRYPTO_PRICE_CACHE_TTL = 60
crypto_price_cache = {}
crypto_price_rate_limit = {}
CRYPTO_PRICE_COINS = {
    # نمادهای بازار عمومی؛ برای دریافت قیمت به کلید API نیاز ندارند.
    "TON": ("TONUSDT", "TON-USDT", "TON"),
    "DOGS": ("DOGSUSDT", "DOGS-USDT", "DOGS"),
    "BTC": ("BTCUSDT", "BTC-USDT", "BTC"),
    "ETH": ("ETHUSDT", "ETH-USDT", "ETH"),
    "USDT": (None, None, "USDT"),
    "SOL": ("SOLUSDT", "SOL-USDT", "SOL"),
    "BNB": ("BNBUSDT", "BNB-USDT", "BNB"),
    "NOT": ("NOTUSDT", "NOT-USDT", "NOT"),
    "TRX": ("TRXUSDT", "TRX-USDT", "TRX"),
    "XRP": ("XRPUSDT", "XRP-USDT", "XRP"),
}
USD_TO_TOMAN_CACHE_TTL = 60
usd_to_toman_cache = {}


bot_active = True
withdrawals_enabled = True
min_withdraw_amount = 0.1
max_withdraw_amount = 10.0
min_deposit_amount = 0.01
ton_gas_fee = 0.005
# کارمزدی که از کاربر DOGS کسر می‌شود؛ صفر مجاز است.
dogs_gas_fee_ton = 0.05
# حتی وقتی کاربر گس‌فی صفر دارد، تراکنش Jetton باید حداقل TON شبکه را از ولت سیستم بگیرد.
DOGS_NETWORK_GAS_TON = 0.05
dogs_min_withdraw_amount = 1000.0
dogs_max_withdraw_amount = 1000000.0
dogs_withdrawals_enabled = True
# سیستم رفرال: پاداش به‌صورت اعتبار داخلی TON در کیف‌پول ربات ثبت می‌شود.
referrals_enabled = True
referral_reward_ton = 0.01
tracker_message_id = None
system_wallet_address = None
system_dogs_wallet_address = None
# ارسال‌های TON باید پشت‌سرهم انجام شوند تا چند برداشت هم‌زمان از یک موجودی عبور نکند.
payout_lock = asyncio.Lock()
# کل مسیر رزرو/بازگشت موجودی و برداشت در یک پردازش سریالی انجام می‌شود.
withdrawal_flow_lock = asyncio.Lock()
# DOGS deposit scans are serialized to avoid overlapping chain reads and credits.
dogs_deposit_scan_lock = asyncio.Lock()
# از ثبت پاداش تکراری در شروع هم‌زمان جلوگیری می‌کند.
referral_lock = asyncio.Lock()

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
    """Send TON and distinguish a rejected transfer from an ambiguous broadcast."""
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
        seqno_before = None
        transfer_submitted = False
        try:
            client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await client.connect()
            wallet = await WalletV5R1.from_mnemonic(
                client, TON_MNEMONIC.strip().split(), network_global_id=-239
            )
            seqno_before = await wallet.get_seqno()
            await wallet.transfer(
                destination=destination_address.strip(),
                amount=int(round(amount_ton * 10**9)),
                body="Payout from Void Giveaway Bot 🎉"
            )
            transfer_submitted = True
            await close_lite_client(client)
            client = None

            if await wait_for_wallet_seqno(seqno_before, attempts=12, interval=2):
                return "sent", (
                    f"ارسال {amount_ton:.4f} TON روی شبکه ثبت و تأیید شد؛ "
                    "نمایش تراکنش در کیف‌پول مقصد ممکن است کمی زمان ببرد."
                )
            return "uncertain", "پیام TON ارسال شده اما تأیید شبکه هنوز دریافت نشده است؛ مبلغ رزرو می‌ماند."
        except Exception as e:
            logging.error(f"pytoniq W5 TON payout error: {e}")
            await close_lite_client(client)
            if transfer_submitted:
                return "uncertain", "نتیجه ارسال TON قطعی نیست؛ مبلغ برای بررسی بیشتر رزرو می‌ماند."
            if seqno_before is not None:
                try:
                    if await wait_for_wallet_seqno(seqno_before, attempts=2, interval=1):
                        return "uncertain", "ارسال TON احتمالاً انجام شده اما نتیجه قطعی نیست؛ مبلغ رزرو می‌ماند."
                except Exception as confirm_error:
                    logging.warning(f"TON post-error confirmation failed: {confirm_error}")
            return "failed", f"ارسال TON قبل از ثبت تراکنش شکست خورد: {e}"

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
    """Send DOGS from the Jetton Wallet owned by the TON mnemonic wallet."""
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
        system_wallet = await get_system_wallet_address()
        system_dogs_wallet = await get_system_dogs_wallet_address()
        if not system_wallet or not system_dogs_wallet:
            return "failed", "آدرس ولت مرکزی DOGS یا ولت TON قابل دریافت نیست."

        system_balance, balance_info = await get_system_wallet_balance()
        user_fee_ton = max(float(dogs_gas_fee_ton), 0.0)
        network_value_ton = max(user_fee_ton, DOGS_NETWORK_GAS_TON)
        if system_balance is None:
            return "failed", f"موجودی TON ولت ربات قابل بررسی نیست: {balance_info}"
        if system_balance < network_value_ton:
            return "failed", (
                f"موجودی TON ولت ربات برای گس DOGS کافی نیست. موجودی فعلی: {system_balance:.4f} TON؛ "
                f"مبلغ موردنیاز شبکه: {network_value_ton:.4f} TON"
            )

        real_dogs_balance, dogs_wallet_info = await get_system_dogs_balance()
        if real_dogs_balance is None:
            return "failed", f"موجودی واقعی DOGS قابل بررسی نیست: {dogs_wallet_info}"
        if real_dogs_balance + 1e-9 < amount_dogs:
            return "failed", (
                f"موجودی DOGS ولت مرکزی کافی نیست. موجودی فعلی: {real_dogs_balance:.4f} DOGS؛ "
                f"مبلغ موردنیاز: {amount_dogs:.4f} DOGS"
            )

        client = None
        seqno_before = None
        transfer_submitted = False
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
            await wallet.transfer(
                destination=system_dogs_wallet,
                amount=int(round(network_value_ton * 10 ** 9)),
                body=body
            )
            transfer_submitted = True
            await close_lite_client(client)
            client = None

            if await wait_for_wallet_seqno(seqno_before, attempts=12, interval=2):
                return "sent", (
                    f"ارسال {amount_dogs:.4f} DOGS روی شبکه ثبت و تأیید شد؛ "
                    "نمایش تراکنش ممکن است چند ثانیه زمان ببرد."
                )
            return "uncertain", "پیام DOGS ارسال شده اما تأیید شبکه هنوز دریافت نشده است؛ مبلغ رزرو می‌ماند."
        except Exception as e:
            logging.error(f"DOGS payout error: {e}")
            await close_lite_client(client)
            if transfer_submitted:
                return "uncertain", "نتیجه ارسال DOGS قطعی نیست؛ مبلغ برای بررسی بیشتر رزرو می‌ماند."
            if seqno_before is not None:
                try:
                    if await wait_for_wallet_seqno(seqno_before, attempts=2, interval=1):
                        return "uncertain", "ارسال DOGS احتمالاً انجام شده اما نتیجه قطعی نیست؛ مبلغ رزرو می‌ماند."
                except Exception as confirm_error:
                    logging.warning(f"DOGS post-error confirmation failed: {confirm_error}")
            return "failed", f"ارسال DOGS قبل از ثبت تراکنش شکست خورد: {e}"

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



async def get_dogs_owner_wallet_address():
    """Use the TON wallet derived from TON_MNEMONIC as the DOGS Jetton owner."""
    actual_owner = await get_system_wallet_address()
    if actual_owner and is_valid_ton_address(actual_owner):
        configured_owner = (DOGS_OWNER_WALLET_ADDRESS or "").strip()
        if configured_owner and configured_owner != actual_owner:
            logging.warning(
                "DOGS_OWNER_WALLET_ADDRESS does not match TON_MNEMONIC wallet; using derived wallet"
            )
        return actual_owner
    return None

async def get_system_dogs_wallet_address():
    """Return the DOGS Jetton Wallet owned by the configured central TON wallet."""
    global system_dogs_wallet_address
    if system_dogs_wallet_address:
        return system_dogs_wallet_address

    owner_address = await get_dogs_owner_wallet_address()
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
                "first_name": info.get("first_name", "User"),
                "referral_count": int(info.get("referral_count", 0) or 0),
                "referral_rewarded": bool(info.get("referral_rewarded", False)),
                "pending_referrer_id": info.get("pending_referrer_id"),
                "phone_verified": bool(info.get("phone_verified", False))
            }
            if info.get("started_at"):
                user_doc["started_at"] = info["started_at"]
            if info.get("referred_by") is not None:
                user_doc["referred_by"] = int(info["referred_by"])
            user_doc["pending_referrer_id"] = info.get("pending_referrer_id")
            user_doc["phone_verified"] = bool(info.get("phone_verified", False))
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
            "referrals_enabled": referrals_enabled,
            "referral_reward_ton": referral_reward_ton,
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
            "first_name": info.get("first_name", "User"),
            "referral_count": int(info.get("referral_count", 0) or 0),
            "referral_rewarded": bool(info.get("referral_rewarded", False)),
            "pending_referrer_id": info.get("pending_referrer_id"),
            "phone_verified": bool(info.get("phone_verified", False))
        }
        if info.get("started_at"):
            user_doc["started_at"] = info["started_at"]
        if info.get("referred_by") is not None:
            user_doc["referred_by"] = int(info["referred_by"])
        user_doc["pending_referrer_id"] = info.get("pending_referrer_id")
        user_doc["phone_verified"] = bool(info.get("phone_verified", False))
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
    global user_data, all_time_users, banned_users, required_channels, bot_active, withdrawals_enabled, min_withdraw_amount, max_withdraw_amount, ton_gas_fee, dogs_gas_fee_ton, dogs_min_withdraw_amount, dogs_max_withdraw_amount, dogs_withdrawals_enabled, referrals_enabled, referral_reward_ton, tracker_message_id
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
            referrals_enabled = settings_doc.get("referrals_enabled", True)
            referral_reward_ton = settings_doc.get("referral_reward_ton", 0.01)
            tracker_message_id = settings_doc.get("tracker_message_id", None)

        async for user_doc in users_col.find():
            u_id = int(user_doc["user_id"])
            user_data[u_id] = {
                "balance": round(float(user_doc.get("balance", 0.0) or 0.0), 4),
                "dogs_balance": round(float(user_doc.get("dogs_balance", 0.0) or 0.0), 4),
                "username": user_doc.get("username", ""),
                "first_name": user_doc.get("first_name", "User"),
                "started_at": user_doc.get("started_at"),
                "referred_by": user_doc.get("referred_by"),
                "referral_rewarded": bool(user_doc.get("referral_rewarded", False)),
                "referral_count": int(user_doc.get("referral_count", 0) or 0),
                "pending_referrer_id": user_doc.get("pending_referrer_id"),
                "phone_verified": bool(user_doc.get("phone_verified", False))
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
    asset = State()
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

class AdminSetReferralRewardForm(StatesGroup):
    amount = State()

class AdminAddChannelForm(StatesGroup):
    channel = State()

class AdminRemoveChannelForm(StatesGroup):
    channel = State()

class SupportTicketForm(StatesGroup):
    message = State()


class AdminSupportReplyForm(StatesGroup):
    message = State()

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
            "first_name": "User",
            "referral_count": 0,
            "referral_rewarded": False,
            "pending_referrer_id": None,
            "phone_verified": False
        }
    if user_obj:
        user_data[user_id]["username"] = user_obj.username or ""
        user_data[user_id]["first_name"] = user_obj.first_name or "User"
    return user_data[user_id]

def get_main_keyboard(user_id: int):
    kb = [
        [KeyboardButton(text="💎 کیف‌پول من")],
        [KeyboardButton(text="❓ راهنما"), KeyboardButton(text="🆘 پشتیبانی")],
        [KeyboardButton(text="🎁 دعوت دوستان"), KeyboardButton(text="🏆 رتبه‌بندی")]
    ]
    if is_admin(user_id):
        kb.insert(0, [KeyboardButton(text="🛠 پنل فرماندهی 👑")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)



def get_referral_contact_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 تأیید شماره و دریافت جایزه", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def format_crypto_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    if value >= 0.01:
        return f"{value:,.6f}"
    return f"{value:,.8f}"


async def fetch_usd_to_toman():
    """Read USD/IRR from public FX feeds and convert it to toman."""
    now = time.monotonic()
    cached = usd_to_toman_cache.get("USD")
    if cached and now - cached["fetched_at"] < USD_TO_TOMAN_CACHE_TTL:
        return cached["value"], cached["source"]

    providers = [
        ("Open ER API", "https://open.er-api.com/v6/latest/USD"),
        ("ExchangeRate API", "https://api.exchangerate-api.com/v4/latest/USD"),
    ]

    def request_rate(endpoint):
        request = Request(
            endpoint,
            headers={"Accept": "application/json", "User-Agent": "VoidGiveawayBot/6.3"}
        )
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    for provider_name, endpoint in providers:
        try:
            payload = await asyncio.to_thread(request_rate, endpoint)
            irr_rate = float((payload.get("rates") or {})["IRR"])
            # نرخ API ریال است؛ خروجی ربات تومان است.
            toman_rate = irr_rate / 10
            if toman_rate > 0:
                usd_to_toman_cache["USD"] = {
                    "fetched_at": time.monotonic(),
                    "value": toman_rate,
                    "source": provider_name,
                }
                return toman_rate, provider_name
        except Exception as e:
            logging.warning(f"{provider_name} FX request failed: {e}")
    return None, None


def tradingview_chart_url(symbol: str):
    market_info = CRYPTO_PRICE_COINS.get((symbol or "").strip().upper().lstrip("$"))
    if not market_info or not market_info[0]:
        return None
    return f"https://www.tradingview.com/chart/?symbol=BINANCE%3A{quote(market_info[0], safe='')}"


def tradingview_keyboard(data):
    chart_url = data.get("chart_url") if data else None
    if not chart_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📈 باز کردن چارت TradingView", url=chart_url)
    ]])


async def build_chart_image_url(market_symbol: str):
    """Build a shareable PNG chart URL from public OHLC data."""
    if not market_symbol:
        return None
    endpoint = (
        "https://api.binance.com/api/v3/klines"
        f"?symbol={quote(market_symbol, safe='')}&interval=1h&limit=48"
    )

    def request_candles():
        request = Request(
            endpoint,
            headers={"Accept": "application/json", "User-Agent": "VoidGiveawayBot/6.3"}
        )
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        candles = await asyncio.to_thread(request_candles)
        if not isinstance(candles, list) or not candles:
            return None
        labels = [datetime.fromtimestamp(float(row[0]) / 1000).strftime("%m/%d %H:%M") for row in candles]
        closes = [round(float(row[4]), 8) for row in candles]
        chart_config = {
            "type": "line",
            "data": {
                "labels": labels,
                "datasets": [{
                    "label": f"{market_symbol} · 1h close",
                    "data": closes,
                    "borderColor": "#22c55e",
                    "backgroundColor": "rgba(34, 197, 94, 0.16)",
                    "fill": True,
                    "pointRadius": 0,
                    "borderWidth": 2,
                }],
            },
            "options": {
                "plugins": {"legend": {"display": True}},
                "scales": {"x": {"display": False}},
            },
        }
        encoded_config = quote(json.dumps(chart_config, separators=(",", ":"), ensure_ascii=False), safe="")
        return f"https://quickchart.io/chart?width=1000&height=520&format=png&c={encoded_config}"
    except Exception as e:
        logging.warning(f"Chart image request failed for {market_symbol}: {e}")
        return None


async def send_price_message(message: types.Message, text: str, data):
    """Upload a chart image when possible, with a text fallback."""
    keyboard = tradingview_keyboard(data)
    chart_image_url = await build_chart_image_url(data.get("market_symbol"))
    if chart_image_url:
        try:
            await message.answer_photo(
                photo=chart_image_url,
                caption=text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            logging.warning(f"Could not upload chart image: {e}")
    await message.answer(
        text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=keyboard,
    )


async def fetch_crypto_price(symbol: str):
    normalized = (symbol or "").strip().upper().lstrip("$")
    market_info = CRYPTO_PRICE_COINS.get(normalized)
    if not market_info:
        return None

    binance_symbol, okx_symbol, display_symbol = market_info
    now = time.monotonic()
    cached = crypto_price_cache.get(normalized)
    if cached and now - cached["fetched_at"] < CRYPTO_PRICE_CACHE_TTL:
        return cached["data"]

    market_price = None
    price_source = None
    providers = []
    if normalized == "TON":
        # TONAPI uses the TON-native market rate; Binance remains a fallback.
        providers.append(("TonAPI", "https://tonapi.io/v2/rates?tokens=ton&currencies=usd"))
    if binance_symbol:
        providers.append(("Binance", f"https://api.binance.com/api/v3/ticker/24hr?symbol={quote(binance_symbol, safe='')}"))
    if okx_symbol:
        providers.append(("OKX", f"https://www.okx.com/api/v5/market/ticker?instId={quote(okx_symbol, safe='')}"))
    if normalized == "USDT":
        market_price = (1.0, 0.0)
        price_source = "ثابت USDT"

    for provider_name, endpoint in providers:
        if market_price:
            break

        def request_market():
            request = Request(
                endpoint,
                headers={"Accept": "application/json", "User-Agent": "VoidGiveawayBot/6.3"}
            )
            with urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            payload = await asyncio.to_thread(request_market)
            if provider_name == "TonAPI":
                rate = payload["rates"]["TON"]
                usd_price = float(rate["prices"]["USD"])
                change_raw = str((rate.get("diff_24h") or {}).get("USD", "0"))
                change_match = re.search(r"[-+]?\d+(?:\.\d+)?", change_raw.replace("−", "-"))
                change_24h = float(change_match.group(0)) if change_match else 0.0
            elif provider_name == "Binance":
                usd_price = float(payload["lastPrice"])
                change_24h = float(payload.get("priceChangePercent") or 0)
            else:
                row = (payload.get("data") or [])[0]
                usd_price = float(row["last"])
                open_24h = float(row.get("open24h") or 0)
                change_24h = ((usd_price - open_24h) / open_24h * 100) if open_24h else 0.0
            if usd_price > 0:
                market_price = (usd_price, change_24h)
                price_source = provider_name
        except Exception as e:
            logging.warning(f"{provider_name} price request failed for {normalized}: {e}")

    if not market_price:
        return None

    usd_price, change_24h = market_price
    toman_rate, toman_source = await fetch_usd_to_toman()
    data = {
        "symbol": display_symbol,
        "price": usd_price,
        "change_24h": change_24h,
        "toman": (usd_price * toman_rate if toman_rate is not None else None),
        "price_source": price_source,
        "toman_source": toman_source,
        "market_symbol": binance_symbol,
        "chart_url": tradingview_chart_url(normalized),
    }
    crypto_price_cache[normalized] = {"fetched_at": time.monotonic(), "data": data}
    return data

def get_admin_inline_keyboard():
    status_btn = "🛑 خاموش کردن ربات" if bot_active else "✅ روشن کردن ربات"
    withdrawals_btn = "🛑 خاموش کردن برداشت‌ها" if withdrawals_enabled else "✅ روشن کردن برداشت‌ها"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن کانال", callback_data="admin_add_channel"), InlineKeyboardButton(text="➖ حذف کانال", callback_data="admin_remove_channel")],
            [InlineKeyboardButton(text="🔎 جستجوی کاربر", callback_data="admin_search_user"), InlineKeyboardButton(text="💰 ویرایش موجودی", callback_data="admin_edit_balance")],
            [InlineKeyboardButton(text="💬 پیام مستقیم", callback_data="admin_direct_msg")],
            [InlineKeyboardButton(text="⛔ مسدودکردن کاربر", callback_data="admin_ban_user"), InlineKeyboardButton(text="✅ رفع مسدودی", callback_data="admin_unban_user")],
            [InlineKeyboardButton(text="⚙️ حداقل برداشت TON", callback_data="admin_set_min_wd"), InlineKeyboardButton(text="🔝 سقف برداشت TON", callback_data="admin_set_max_wd")],
            [InlineKeyboardButton(text="⛽️ کارمزد شبکه TON", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text="🐶 کارمزد برداشت DOGS", callback_data="admin_set_dogs_gas_fee")],
            [InlineKeyboardButton(text="🐶 حداقل برداشت DOGS", callback_data="admin_set_min_dogs_wd"), InlineKeyboardButton(text="🐶 حداکثر برداشت DOGS", callback_data="admin_set_max_dogs_wd")],
            [InlineKeyboardButton(text=("🛑 خاموش‌کردن برداشت DOGS" if dogs_withdrawals_enabled else "✅ روشن‌کردن برداشت DOGS"), callback_data="admin_toggle_dogs_withdrawals")],
            [InlineKeyboardButton(text=("🛑 خاموش کردن رفرال‌گیری" if referrals_enabled else "✅ روشن کردن رفرال‌گیری"), callback_data="admin_toggle_referrals"), InlineKeyboardButton(text="🎁 تنظیم پاداش رفرال", callback_data="admin_set_referral_reward")],
            [InlineKeyboardButton(text="🧹 صفرکردن موجودی‌ها", callback_data="admin_reset_balances")],
            [InlineKeyboardButton(text=withdrawals_btn, callback_data="admin_toggle_withdrawals")],
            [InlineKeyboardButton(text="📊 گزارش مالی", callback_data="admin_financial_report"), InlineKeyboardButton(text="🩺 سلامت سیستم", callback_data="admin_system_health")],
            [InlineKeyboardButton(text="🆘 تیکت‌های باز", callback_data="admin_open_tickets")],
            [InlineKeyboardButton(text=status_btn, callback_data="admin_toggle_bot"), InlineKeyboardButton(text="📢 ارسال همگانی", callback_data="admin_broadcast")],
        ]
    )

def parse_referrer_id(command_args):
    """Parse Telegram deep-link payloads such as /start ref_123."""
    match = re.fullmatch(r"ref_(\d+)", (command_args or "").strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


async def process_referral_signup(referred_user_id: int, referrer_id: int) -> bool:
    """Reward a valid new referral exactly once, atomically in MongoDB."""
    if not referrals_enabled or not referrer_id or referrer_id == referred_user_id:
        return False
    if is_banned(referrer_id) or not await has_started_bot(referrer_id):
        return False

    reward = round(float(referral_reward_ton), 4)
    if not math.isfinite(reward) or reward <= 0:
        return False

    # Make sure the referred user's document exists before the conditional update.
    await save_user_data(referred_user_id)
    session = None
    try:
        async with referral_lock:
            session = await mongo_client.start_session()
            async with session.start_transaction():
                referred_after = await users_col.find_one_and_update(
                    {
                        "user_id": referred_user_id,
                        "referral_rewarded": {"$ne": True},
                        "referred_by": {"$exists": False}
                    },
                    {
                        "$set": {
                            "referred_by": referrer_id,
                            "referral_rewarded": True
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                    session=session
                )
                if not referred_after:
                    return False

                referrer_after = await users_col.find_one_and_update(
                    {"user_id": referrer_id},
                    {
                        "$inc": {
                            "balance": reward,
                            "referral_count": 1
                        }
                    },
                    return_document=ReturnDocument.AFTER,
                    session=session
                )
                if not referrer_after:
                    raise RuntimeError("Referrer profile no longer exists")

            referred_profile = get_user_profile(referred_user_id)
            referred_profile["referred_by"] = referrer_id
            referred_profile["referral_rewarded"] = True
            referrer_profile = get_user_profile(referrer_id)
            referrer_profile["balance"] = round(float(referrer_after.get("balance", 0.0)), 4)
            referrer_profile["referral_count"] = int(referrer_after.get("referral_count", 0) or 0)
            return True
    except Exception as e:
        logging.error(f"Referral reward failed for {referred_user_id} -> {referrer_id}: {e}")
        return False
    finally:
        if session:
            await session.end_session()


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
        pending_profile = get_user_profile(u_id)
        if pending_profile.get("pending_referrer_id") and not pending_profile.get("referral_rewarded"):
            await call.message.answer(
                "🤝 <b>عضویت تأیید شد و لینک رفرال شناسایی شد.</b>\n\n"
                "برای ثبت نهایی دعوت، شماره تلگرامت را با دکمه زیر ارسال کن.\n"
                "فقط شماره‌های ایران با پیش‌شماره <code>+98</code> تأیید می‌شوند.",
                parse_mode="HTML",
                reply_markup=get_referral_contact_keyboard()
            )
            return
        await call.message.answer(
            f"🚀 <b>به Void Giveaway خوش اومدی!</b>\nاینجا هر دعوت و هر فعالیت می‌تونه موجودی واقعی TON و DOGS بسازه.\n"
            f"⚡ <b>نسخه فعال:</b> <code>{BOT_VERSION}</code> | سریع، شفاف و آماده\n\n"

            f"از منوی زیر شروع کن؛ موجودی، جایزه‌ها و برداشت‌هات همین‌جا مدیریت می‌شن 👇",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(u_id)
        )
    else:
        await call.answer("⏳ هنوز عضویتت در همه کانال‌ها تأیید نشده؛ یک بار دیگه بررسی کن!", show_alert=True)

async def complete_start_response(message: types.Message, loading_message: types.Message, u_id: int, referrer_id: int = None):
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
                f"🌟 <b>برای فعال‌شدن جایزه‌ها، اول عضو کانال‌های رسمی Void شو.</b>\n\n"
                f"✅ بعد از عضویت، روی «✅ بررسی عضویت / ورود» بزن تا وارد ربات بشی.",
                parse_mode="HTML",
                reply_markup=get_join_channel_keyboard()
            )
            return

        profile = get_user_profile(u_id)
        pending_referrer_id = profile.get("pending_referrer_id") or referrer_id
        if pending_referrer_id and not profile.get("referral_rewarded"):
            profile["pending_referrer_id"] = int(pending_referrer_id)
            await save_user_data(u_id)
            await loading_message.edit_text(
                "🤝 <b>لینک رفرال شناسایی شد.</b>",
                parse_mode="HTML"
            )
            await message.answer(
                "برای ثبت نهایی دعوت و جلوگیری از سوءاستفاده، شماره تلگرامت را با دکمه زیر ارسال کن.\n"
                "فقط شماره‌های ایران با پیش‌شماره <code>+98</code> تأیید می‌شوند.",
                parse_mode="HTML",
                reply_markup=get_referral_contact_keyboard()
            )
            return

        await message.answer(
            f"🚀 <b>به Void Giveaway خوش اومدی!</b>\nاینجا هر دعوت و هر فعالیت می‌تونه موجودی واقعی TON و DOGS بسازه.\n"
            f"🧩 <b>نسخه فعال:</b> <code>v6.3.0</code> 💎\n\n"
            f"از منوی زیر شروع کن؛ موجودی، جایزه‌ها و برداشت‌هات همین‌جا مدیریت می‌شن 👇",
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


@dp.message(Command(commands=["price", "p"]))
async def crypto_price_handler(message: types.Message, command: CommandObject):
    if message.chat.type not in ("group", "supergroup"):
        toman_line = ""
    if data.get("toman") is not None:
        toman_line = f"\n🇮🇷 <b>تومان</b> {format_quantity(data['toman'])}"
    caption = (
        f"📊 <b>قیمت لحظه‌ای {data['symbol']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>USD {format_crypto_price(data['price'])}</b>{toman_line}\n"
        f"{change_text}\n\n"
        "🕒 داده‌ها هر ۶۰ ثانیه تازه می‌شوند.\n"
        f"🔗 منبع قیمت: {data['price_source']} | نرخ تومان: {data.get('toman_source') or 'در دسترس نیست'}"
    )
    await send_price_message(message, caption, data)


PERSIAN_PRICE_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
PRICE_TEXT_ALIASES = {
    "TON": ("ton", "تون", "gram", "گرام", "toncoin", "تون‌کوین"),
    "DOGS": ("dogs", "داگز", "داگس"),
    "BTC": ("btc", "bitcoin", "بیت کوین", "بیت‌کوین", "بیتکوین"),
    "ETH": ("eth", "ethereum", "اتریوم"),
    "USDT": ("usdt", "tether", "تتر"),
    "SOL": ("sol", "solana", "سولانا"),
    "BNB": ("bnb", "binance coin", "بایننس", "بی‌ان‌بی"),
    "NOT": ("not", "notcoin", "نات کوین", "نات‌کوین", "ناتکوین"),
    "TRX": ("trx", "tron", "ترون"),
    "XRP": ("xrp", "ripple", "ریپل"),
}


def normalize_price_query(value: str) -> str:
    normalized = (value or "").translate(PERSIAN_PRICE_DIGITS).lower()
    normalized = normalized.replace("ي", "ی").replace("ك", "ک")
    normalized = normalized.replace("تومن", "تومان").replace("مليون", "میلیون")
    normalized = re.sub(r"[\u200c\u200f]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def price_alias_position(query: str, alias: str):
    if re.fullmatch(r"[a-z0-9]+", alias):
        match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", query)
        return match.start() if match else None
    position = query.find(alias)
    return position if position >= 0 else None


def find_price_coins(query: str):
    found = []
    for symbol, aliases in PRICE_TEXT_ALIASES.items():
        positions = [price_alias_position(query, alias) for alias in aliases]
        positions = [position for position in positions if position is not None]
        if positions:
            found.append((min(positions), symbol))
    return [symbol for _, symbol in sorted(found)]


def parse_natural_price_query(value: str):
    query = normalize_price_query(value)
    coins = find_price_coins(query)
    if not coins:
        return None

    number_match = re.search(
        r"(?<![a-z0-9])(\d+(?:[.,]\d+)?)\s*(میلیون|هزار|k|m)?",
        query,
        re.IGNORECASE
    )
    amount = 1.0
    multiplier = 1.0
    if number_match:
        amount = float(number_match.group(1).replace(",", ""))
        unit = (number_match.group(2) or "").lower()
        multiplier = {"میلیون": 1_000_000, "هزار": 1_000, "k": 1_000, "m": 1_000_000}.get(unit, 1.0)
        amount *= multiplier

    is_toman = bool(re.search(r"تومان|irr", query))
    return {"query": query, "coins": coins, "amount": amount, "is_toman": is_toman}


def format_quantity(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.6f}".rstrip("0").rstrip(".")


def format_change(change) -> str:
    if change is None:
        return "تغییر ۲۴ساعته نامشخص"
    return f"{'📈' if change >= 0 else '📉'} {change:+.2f}% در ۲۴ ساعت"


@dp.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"(?i)(ton|تون|gram|گرام|toncoin|dogs|داگز|داگس|تومان|تومن)")
)
async def natural_crypto_price_handler(message: types.Message):
    parsed = parse_natural_price_query(message.text or "")
    if not parsed:
        return

    rate_key = (message.chat.id, message.from_user.id)
    now = time.monotonic()
    if now - crypto_price_rate_limit.get(rate_key, 0) < 3:
        await message.answer("⏳ یک لحظه صبر کن؛ قیمت‌ها هر چند ثانیه یک‌بار تازه می‌شوند.")
        return
    crypto_price_rate_limit[rate_key] = now

    symbols = parsed["coins"]
    data = await asyncio.gather(*(fetch_crypto_price(symbol) for symbol in symbols))
    if any(item is None for item in data):
        await message.answer("⚠️ قیمت این ارز فعلاً از سرویس بازار دریافت نشد؛ کمی بعد دوباره امتحان کن.")
        return

    chart_data = data[0]
    amount = parsed["amount"]
    if parsed["is_toman"]:
        if len(data) != 1 or data[0].get("toman") is None:
            await message.answer("⚠️ تبدیل تومانی این درخواست فعلاً در دسترس نیست؛ قیمت دلاری را امتحان کن.")
            return
        coin = data[0]
        coin_amount = amount / coin["toman"]
        reply = (
            "💱 <b>محاسبه تقریبی بازار</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💵 <b>{format_quantity(amount)} تومان</b> ≈ "
            f"<b>{format_quantity(coin_amount)} {coin['symbol']}</b>\n"
            f"📌 قیمت هر {coin['symbol']}: حدود <code>{format_quantity(coin['toman'])} تومان</code>\n"
            f"{format_change(coin.get('change_24h'))}\n\n"
            "⚠️ نرخ تومان تقریبی است و برای قیمت دقیق خرید/فروش استفاده نشود."
        )
    elif len(data) == 2:
        source, target = data
        target_amount = amount * source["price"] / target["price"]
        reply = (
            "🔄 <b>تبدیل تقریبی ارزها</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💎 <b>{format_quantity(amount)} {source['symbol']}</b> ≈ "
            f"<b>{format_quantity(target_amount)} {target['symbol']}</b>\n"
            f"💵 ارزش مبنا: حدود <code>USD {format_crypto_price(amount * source['price'])}</code>\n"
            f"📈 {source['symbol']}: {format_change(source.get('change_24h'))}\n"
            f"📉 {target['symbol']}: {format_change(target.get('change_24h'))}"
        )
    else:
        coin = data[0]
        total_usd = amount * coin["price"]
        toman_line = ""
        if coin.get("toman") is not None:
            toman_line = f"\n🇮🇷 ارزش تقریبی: <code>{format_quantity(amount * coin['toman'])} تومان</code>"
        reply = (
            f"📊 <b>قیمت لحظه‌ای {coin['symbol']}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"💎 {format_quantity(amount)} {coin['symbol']} ≈ "
            f"<b>USD {format_crypto_price(total_usd)}</b>{toman_line}\n"
            f"{format_change(coin.get('change_24h'))}"
        )

    await send_price_message(
        message,
        reply + "\n\n🕒 داده‌ها حداکثر هر ۶۰ ثانیه تازه می‌شوند.\n🔗 منبع قیمت: Binance / OKX / TonAPI | نرخ تومان: Open ER API",
        chart_data,
    )


@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>دسترسی این حساب متوقف شده است.</b>\nاگر فکر می‌کنی اشتباهی رخ داده، با پشتیبانی تماس بگیر.", parse_mode="HTML")
        return

    profile = get_user_profile(u_id, message.from_user)
    profile["started_at"] = profile.get("started_at") or datetime.utcnow().isoformat()
    all_time_users.add(u_id)
    referrer_id = parse_referrer_id(command.args)
    if (
        referrals_enabled
        and referrer_id
        and referrer_id != u_id
        and not profile.get("referral_rewarded")
        and not profile.get("pending_referrer_id")
    ):
        profile["pending_referrer_id"] = referrer_id

    # Send the acknowledgement before any database or network check.
    loading_message = await message.answer("⏳ <b>در حال آماده‌سازی ربات...</b>", parse_mode="HTML")
    asyncio.create_task(save_user_data(u_id))
    asyncio.create_task(complete_start_response(message, loading_message, u_id, referrer_id))


@dp.message(F.text == "🎁 دعوت دوستان")
async def show_referral_menu(message: types.Message):
    u_id = message.from_user.id
    if is_banned(u_id):
        return
    if not await check_user_subscription(u_id):
        await message.answer("🔐 برای استفاده از بخش دعوت دوستان، ابتدا در کانال‌های رسمی عضو شو.", reply_markup=get_join_channel_keyboard())
        return

    bot_username = (await bot.get_me()).username
    referral_link = f"https://t.me/{bot_username}?start=ref_{u_id}"
    profile = get_user_profile(u_id, message.from_user)
    referral_count = int(profile.get("referral_count", 0) or 0)
    await message.answer(
        "🎁 <b>دعوت کن، جایزه بگیر</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💎 پاداش هر دعوت موفق: <code>{referral_reward_ton:.4f} TON</code>\n"
        f"👥 دعوت‌های موفق: <code>{referral_count}</code> نفر\n\n"
        "لینک اختصاصی‌ات را بفرست. وقتی دوستت وارد شود و شرایط را کامل کند، پاداش مستقیم به موجودی داخلی تو می‌آید.\n\n"
        f"🔗 <code>{referral_link}</code>",
        parse_mode="HTML"
    )


@dp.message(F.text == "🏆 رتبه‌بندی")
async def show_leaderboard(message: types.Message):
    u_id = message.from_user.id
    if is_banned(u_id):
        return
    if not await check_user_subscription(u_id):
        await message.answer("🔐 برای دیدن لیدربورد، ابتدا در کانال‌های رسمی عضو شو.", reply_markup=get_join_channel_keyboard())
        return

    ranked_users = [
        (user_id, profile)
        for user_id, profile in user_data.items()
        if user_id not in banned_users
    ]
    ranked_users.sort(key=lambda item: float(item[1].get("balance", 0.0) or 0.0), reverse=True)
    top_users = ranked_users[:5]
    if not top_users:
        await message.answer("🏆 هنوز کاربری برای نمایش در لیدربورد وجود ندارد.")
        return

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    rows = []
    for rank, (user_id, profile) in enumerate(top_users, 1):
        username = profile.get("username") or ""
        display_name = f"@{username}" if username else profile.get("first_name") or f"کاربر {user_id}"
        rows.append(
            f"{medals[rank - 1]} <b>{html.escape(str(display_name))}</b> — "
            f"<code>{float(profile.get('balance', 0.0) or 0.0):.4f} TON</code> | "
            f"<code>{float(profile.get('dogs_balance', 0.0) or 0.0):.2f} DOGS</code>"
        )

    await message.answer(
        "🏆 <b>رتبه‌بندی برترین‌ها</b>\n"
        "<i>رتبه‌ها بر اساس موجودی TON محاسبه می‌شوند.</i>\n"
        "━━━━━━━━━━━━━━━━━━\n" + "\n".join(rows),
        parse_mode="HTML"
    )


def normalize_iranian_phone(phone: str) -> str:
    normalized = re.sub(r"[\s().-]", "", phone or "")
    if normalized.startswith("0098"):
        normalized = "+98" + normalized[4:]
    elif normalized.startswith("98"):
        normalized = "+98" + normalized[2:]
    return normalized


def is_valid_iranian_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"\+989\d{9}", normalize_iranian_phone(phone)))


@dp.message(F.contact)
async def confirm_referral_contact(message: types.Message):
    u_id = message.from_user.id
    profile = get_user_profile(u_id, message.from_user)
    pending_referrer_id = profile.get("pending_referrer_id")
    if not pending_referrer_id:
        return

    contact = message.contact
    if not contact or contact.user_id != u_id:
        await message.answer(
            "⚠️ لطفاً فقط شماره‌ی خودت را با دکمه‌ی تأیید ارسال کن؛ شماره‌ی فورواردشده پذیرفته نمی‌شود.",
            reply_markup=get_referral_contact_keyboard()
        )
        return
    if not is_valid_iranian_phone(contact.phone_number):
        await message.answer(
            "⚠️ فقط شماره‌های موبایل ایران با پیش‌شماره‌ی <code>+98</code> قابل تأیید هستند.",
            parse_mode="HTML",
            reply_markup=get_referral_contact_keyboard()
        )
        return

    referral_added = await process_referral_signup(u_id, int(pending_referrer_id))
    if referral_added:
        profile["pending_referrer_id"] = None
        profile["phone_verified"] = True
        await save_user_data(u_id)
        await message.answer(
            f"✅ <b>رفرال با موفقیت ثبت شد!</b>\n\n"
            f"شماره تأیید شد و دعوت تو ثبت گردید. دعوت‌کننده‌ات <code>{referral_reward_ton:.4f} TON</code> پاداش گرفت.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(u_id)
        )
        try:
            await bot.send_message(
                int(pending_referrer_id),
                f"🎉 <b>تبریک!</b> یک نفر با لینک دعوت تو وارد ربات شد و <code>{referral_reward_ton:.4f} TON</code> به کیف‌پولت اضافه شد.",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.warning(f"Referral notification failed for {u_id}: {e}")
    else:
        await save_user_data(u_id)
        await message.answer(
            "✅ شماره تأیید شد، اما این دعوت قابل ثبت نبود؛ ممکن است رفرال‌گیری خاموش باشد یا دعوت‌کننده معتبر نباشد.",
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
        await message.reply("ℹ️ انتقال موجودی فقط با reply به یک کاربر در گروه انجام می‌شود.")
        return
    if not bot_active and not is_admin(sender.id):
        await message.reply("🛠️ ربات موقتاً در حال ارتقاست؛ انتقالی انجام نشد.")
        return

    reply = message.reply_to_message
    recipient = reply.from_user if reply else None
    if not recipient or recipient.is_bot:
        await message.reply(
            "⚠️ روی پیام کاربر مقصد reply کن و بنویس: <code>wallet 100 dogs</code> یا <code>wallet 0.01 ton</code>",
            parse_mode="HTML"
        )
        return
    if recipient.id == sender.id:
        await message.reply("⚠️ انتقال موجودی به خودت امکان‌پذیر نیست.")
        return

    parts = re.split(r"\s+", (message.text or "").strip())
    if len(parts) not in (2, 3):
        await message.reply(
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
        await message.reply("⚠️ واحد معتبر فقط TON یا DOGS است.")
        return

    if not math.isfinite(amount) or amount <= 0:
        await message.reply("⚠️ مقدار انتقال باید یک عدد مثبت باشد.")
        return

    if not await has_started_bot(recipient.id):
        target_name = html.escape(recipient.full_name or "کاربر")
        await message.reply(
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
        await message.reply(f"💰 موجودی {unit} برای این انتقال کافی نیست؛ هیچ مبلغی کم نشد.")
        return
    if result != "ok":
        await message.reply("⚠️ انتقال انجام نشد و موجودی‌ها تغییر نکردند. دوباره تلاش کن.")
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
    await message.reply(group_text, parse_mode="HTML")
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


@dp.message(F.text == "💎 کیف‌پول من")
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
        f"💎 <b>داشبورد کیف‌پول</b>\n━━━━━━━━━━━━━━━━━━\n🔐 موجودی‌ها داخلی و قابل پیگیری هستند.\n\n"
        f"💰 <b>موجودی TON:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"🐶 <b>موجودی DOGS:</b> <code>{prof.get('dogs_balance', 0.0):.4f} DOGS</code>\n"
        f"⛽️ <b>کارمزد شبکه TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🐶 <b>کارمزد شبکه برداشت DOGS:</b> <code>{dogs_gas_fee_ton} TON</code>\n"
        f"🔻 <b>حداقل/حداکثر TON:</b> <code>{min_withdraw_amount} / {max_withdraw_amount}</code>\n"
        f"🔻 <b>حداقل/حداکثر DOGS:</b> <code>{dogs_min_withdraw_amount} / {dogs_max_withdraw_amount}</code>\n━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ واریز سریع TON", callback_data="start_deposit"), InlineKeyboardButton(text="⚡ واریز سریع DOGS", callback_data="start_dogs_deposit")],
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
        f"⚡ <b>واریز سریع TON</b>\nمبلغی که می‌خواهی واریز کنی را بفرست.\nحداقل واریز: <code>{min_deposit_amount:.4f} TON</code>",
        parse_mode="HTML"
    )


@dp.message(DepositForm.amount)
async def process_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = round(float((message.text or "").strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار TON درست نیست؛ یک عدد معتبر وارد کن.")
        return
    if not math.isfinite(amount) or amount < min_deposit_amount:
        await message.answer(f"⚠️ مبلغ کمتر از حداقل واریز است: <code>{min_deposit_amount:.4f} TON</code>", parse_mode="HTML")
        return

    wallet_address = await get_system_wallet_address()
    if not wallet_address:
        await state.clear()
        await message.answer("⚠️ کیف‌پول اصلی موقتاً در دسترس نیست؛ چند لحظه بعد دوباره امتحان کن.")
        return

    memo = get_deposit_memo(message.from_user.id)
    amount_nano = int(round(amount * 10**9))
    ton_link = f"ton://transfer/{wallet_address}?amount={amount_nano}&text={quote(memo)}"
    await state.clear()
    await message.answer(
        "✅ <b>واریز TON آماده‌ست</b>\n\n"
        f"💎 مبلغ: <code>{amount:.4f} TON</code>\n"
        f"📬 آدرس کیف‌پول اصلی: <code>{html.escape(wallet_address)}</code>\n"
        f"🧾 Memo اختصاصی: <code>{memo}</code>\n\n"
        "با دکمه زیر کیف‌پولت را باز کن و تراکنش را تأیید کن. Memo را تغییر نده؛ سیستم بعد از تأیید شبکه، موجودی‌ات را خودکار شارژ می‌کند.",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 بازکردن کیف‌پول و پرداخت", url=ton_link)]
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
    owner_address = await get_dogs_owner_wallet_address()
    if not owner_address:
        await call.answer("⚠️ آدرس ولت مرکزی DOGS از TON_MNEMONIC قابل دریافت نیست.", show_alert=True)
        return
    if not await get_system_dogs_wallet_address():
        await call.answer("⚠️ Jetton Wallet مرکزی DOGS فعلاً قابل دریافت نیست؛ بعداً دوباره تلاش کن.", show_alert=True)
        return
    await call.answer()
    await state.set_state(DogsDepositForm.amount)
    await call.message.answer(
        "🐶 <b>واریز سریع DOGS</b>\nمقدار DOGS موردنظر را وارد کن.\n"
        "لینک پرداخت با Memo اختصاصی تو ساخته می‌شود؛ Memo را دست‌کاری نکن.",
        parse_mode="HTML"
    )

@dp.message(DogsDepositForm.amount)
async def process_dogs_deposit_amount(message: types.Message, state: FSMContext):
    try:
        amount = round(float((message.text or "").strip()), 4)
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار DOGS درست نیست؛ یک عدد معتبر وارد کن.")
        return
    if not math.isfinite(amount) or amount <= 0:
        await message.answer("⚠️ مقدار DOGS باید بیشتر از صفر باشد؛ دوباره امتحان کن.")
        return

    amount_units = int(round(amount * 10 ** DOGS_DECIMALS))
    if amount_units <= 0:
        await message.answer("⚠️ مقدار DOGS برای انتقال خیلی کوچک است.")
        return
    memo = get_deposit_memo(message.from_user.id)
    owner_address = await get_dogs_owner_wallet_address()
    if not owner_address:
        await state.clear()
        await message.answer("⚠️ آدرس ولت مرکزی DOGS از TON_MNEMONIC قابل دریافت نیست.")
        return
    owner = quote(owner_address, safe="")
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
        "✅ <b>واریز DOGS آماده‌ست</b>\n\n"
        f"💰 مبلغ: <code>{amount:.4f} DOGS</code>\n"
        f"🧾 Memo اختصاصی: <code>{memo}</code>\n"
        f"📬 ولت مالک مرکزی: <code>{html.escape(owner_address)}</code>\n"
        f"🧩 Jetton Wallet دریافت‌کننده: <code>{html.escape(dogs_wallet)}</code>\n\n"
        "Memo را تغییر نده؛ بعد از تأیید شبکه، واریز خودکار به موجودی‌ات اضافه می‌شود.",
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🐶 پرداخت با Tonkeeper", url=tonkeeper_link)],
            [InlineKeyboardButton(text="📲 بازکردن کیف‌پول", url=ton_uri)],
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
@dp.message(F.text == "🛠 پنل فرماندهی 👑")
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
        f"🤝 <b>رفرال‌گیری:</b> {'فعال ✅' if referrals_enabled else 'خاموش 🛑'}\n"
        f"🎁 <b>پاداش هر رفرال:</b> <code>{referral_reward_ton:.4f} TON</code>\n"
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


@dp.callback_query(F.data == "admin_toggle_referrals")
async def toggle_referrals_callback(call: types.CallbackQuery):
    global referrals_enabled
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return
    referrals_enabled = not referrals_enabled
    await save_data()
    status_msg = "✅ رفرال‌گیری روشن شد." if referrals_enabled else "🛑 رفرال‌گیری خاموش شد."
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

@dp.callback_query(F.data == "admin_set_referral_reward")
async def start_set_referral_reward(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetReferralRewardForm.amount)
    await call.message.edit_text(
        f"🎁 <b>پاداش هر رفرال موفق را به TON وارد کنید.</b>\nمقدار فعلی: <code>{referral_reward_ton} TON</code>",
        parse_mode="HTML"
    )


@dp.message(AdminSetReferralRewardForm.amount)
async def process_set_referral_reward(message: types.Message, state: FSMContext):
    global referral_reward_ton
    try:
        amount = float(message.text.strip())
        if not math.isfinite(amount) or amount <= 0 or amount > 100:
            raise ValueError
        referral_reward_ton = round(amount, 4)
        await save_data()
        await state.clear()
        await message.answer(
            f"✅ پاداش رفرال با موفقیت روی <code>{referral_reward_ton:.4f} TON</code> تنظیم شد.",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("⚠️ مقدار باید عددی معتبر، بیشتر از صفر و حداکثر ۱۰۰ TON باشد.")


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
        if not math.isfinite(amount) or amount < 0:
            raise ValueError
        dogs_gas_fee_ton = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ گس‌فی کسرشده از کاربر روی <code>{dogs_gas_fee_ton} TON</code> تنظیم شد. حداقل هزینه شبکه از ولت سیستم پرداخت می‌شود.", parse_mode="HTML")
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار معتبر به TON وارد کن؛ صفر هم مجاز است.")

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
    target_id = int(message.text)
    await state.update_data(target_u_id=target_id)
    await state.set_state(AdminManageUserForm.asset)
    prof = get_user_profile(target_id)
    await message.answer(
        f"👤 کاربر <code>{target_id}</code>\n"
        f"💰 موجودی TON: <code>{prof.get('balance', 0.0):.4f}</code>\n"
        f"🐶 موجودی DOGS: <code>{prof.get('dogs_balance', 0.0):.4f}</code>\n\n"
        "دارایی موردنظر برای تغییر را انتخاب کن:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💎 تغییر TON", callback_data="admin_edit_asset_TON")],
            [InlineKeyboardButton(text="🐶 تغییر DOGS", callback_data="admin_edit_asset_DOGS")]
        ])
    )

@dp.callback_query(F.data.startswith("admin_edit_asset_"))
async def process_edit_balance_asset(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("🚫 دسترسی ندارید.", show_alert=True)
        return
    asset = call.data.removeprefix("admin_edit_asset_").upper()
    if asset not in ("TON", "DOGS"):
        await call.answer("دارایی نامعتبر است.", show_alert=True)
        return
    await state.update_data(asset=asset)
    await state.set_state(AdminManageUserForm.amount)
    await call.answer()
    unit = "TON" if asset == "TON" else "DOGS"
    await call.message.edit_text(
        f"مقدار تغییر موجودی {unit} را وارد کن؛ برای کاهش عدد منفی بفرست.\n"
        "مثال افزایش: <code>0.5</code> | مثال کاهش: <code>-0.5</code>",
        parse_mode="HTML"
    )

@dp.message(AdminManageUserForm.amount)
async def process_edit_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("⚠️ مقدار عددی معتبر وارد کنید!")
        return

    if not math.isfinite(amount):
        await message.answer("⚠️ مقدار باید یک عدد محدود و معتبر باشد!")
        return
    data = await state.get_data()
    target_id = data.get("target_u_id")
    asset = data.get("asset", "TON")
    if asset not in ("TON", "DOGS"):
        await state.clear()
        await message.answer("⚠️ نوع دارایی نامعتبر است؛ دوباره از پنل ادمین شروع کن.")
        return

    prof = get_user_profile(target_id)
    field = "dogs_balance" if asset == "DOGS" else "balance"
    unit = "DOGS" if asset == "DOGS" else "TON"
    current_balance = float(prof.get(field, 0.0))
    new_balance = round(current_balance + amount, 4)
    if new_balance < 0:
        await message.answer(f"⚠️ موجودی {unit} کاربر نمی‌تواند منفی شود!")
        return
    prof[field] = new_balance
    await save_data()
    await state.clear()

    await message.answer(
        f"✅ موجودی {unit} کاربر <code>{target_id}</code> به‌روزرسانی شد.\n"
        f"موجودی جدید: <code>{new_balance:.4f} {unit}</code>",
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
# ==========================================
# قابلیت‌های کاربردی: پشتیبانی، گروه، هشدار، گزارش و سلامت
# ==========================================
async def is_group_admin(chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


async def get_group_settings(chat_id: int):
    defaults = {
        "setting_id": f"group:{chat_id}",
        "chat_id": chat_id,
        "welcome_enabled": True,
        "antispam_enabled": True,
        "welcome_text": "👋 به گروه خوش اومدی، {name}!\n\nبرای دیدن قوانین /rules را بفرست.",
        "rules_text": "📌 قوانین گروه هنوز توسط ادمین تنظیم نشده است.",
    }
    try:
        saved = await settings_col.find_one({"setting_id": f"group:{chat_id}"})
        if saved:
            defaults.update(saved)
    except Exception as e:
        logging.warning(f"Group settings read failed: {e}")
    return defaults


async def save_group_settings(chat_id: int, **updates):
    await settings_col.update_one(
        {"setting_id": f"group:{chat_id}"},
        {"$set": {"chat_id": chat_id, **updates}},
        upsert=True
    )


@dp.message(Command(commands=["welcome", "rules"]))
async def group_info_command(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("📌 این دستور را داخل گروه استفاده کن.")
        return
    settings = await get_group_settings(message.chat.id)
    command_name = (message.text or "").split()[0].lower().lstrip("/").split("@")[0]
    if command_name == "rules":
        await message.answer(settings.get("rules_text") or "📌 قانونی برای این گروه ثبت نشده است.", parse_mode="HTML")
    else:
        name = html.escape(message.from_user.first_name or "دوست")
        await message.answer((settings.get("welcome_text") or "👋 خوش اومدی، {name}!").replace("{name}", name), parse_mode="HTML")


@dp.message(Command("setwelcome"))
async def set_group_welcome(message: types.Message, command: CommandObject):
    if message.chat.type not in ("group", "supergroup"):
        return
    if not await is_group_admin(message.chat.id, message.from_user.id):
        await message.answer("⛔ فقط ادمین‌های گروه می‌توانند پیام خوش‌آمد را تغییر دهند.")
        return
    welcome = (command.args or "").strip()
    if not welcome:
        await message.answer("نمونه: <code>/setwelcome سلام {name}، به گروه خوش اومدی!</code>", parse_mode="HTML")
        return
    await save_group_settings(message.chat.id, welcome_text=welcome[:1000], welcome_enabled=True)
    await message.answer("✅ پیام خوش‌آمد این گروه ذخیره شد.")


@dp.message(Command("setrules"))
async def set_group_rules(message: types.Message, command: CommandObject):
    if message.chat.type not in ("group", "supergroup"):
        return
    if not await is_group_admin(message.chat.id, message.from_user.id):
        await message.answer("⛔ فقط ادمین‌های گروه می‌توانند قوانین را تغییر دهند.")
        return
    rules = (command.args or "").strip()
    if not rules:
        await message.answer("نمونه: <code>/setrules بدون اسپم؛ بدون لینک تبلیغاتی؛ احترام به اعضا</code>", parse_mode="HTML")
        return
    await save_group_settings(message.chat.id, rules_text=rules[:3000])
    await message.answer("✅ قوانین گروه ذخیره شد. اعضا با /rules می‌توانند آن را ببینند.")


@dp.message(F.new_chat_members)
async def group_welcome_handler(message: types.Message):
    if message.chat.type not in ("group", "supergroup"):
        return
    settings = await get_group_settings(message.chat.id)
    if not settings.get("welcome_enabled", True):
        return
    for member in message.new_chat_members[:5]:
        name = html.escape(member.first_name or "دوست")
        welcome = (settings.get("welcome_text") or "👋 خوش اومدی، {name}!").replace("{name}", name)
        await message.answer(welcome, parse_mode="HTML")


@dp.message(Command(commands=["faq", "help"]))
async def faq_command(message: types.Message):
    await message.answer(
        "❓ <b>راهنمای سریع Void</b>\n\n"
        "💎 <b>کیف‌پول:</b> موجودی، واریز و برداشت TON/DOGS\n"
        "📊 <b>قیمت:</b> در گروه بنویس <code>تون</code> یا <code>۱۰۰ هزار تومان تون</code>\n"
        "🚨 <b>هشدار:</b> در چت خصوصی <code>/alert TON above 5</code>\n"
        "🔎 <b>تراکنش:</b> <code>/tx شناسه</code>\n"
        "🆘 <b>پشتیبانی:</b> از دکمهٔ پشتیبانی یا <code>/support</code> استفاده کن.",
        parse_mode="HTML"
    )


@dp.message(F.text == "❓ راهنما")
async def faq_button(message: types.Message):
    await faq_command(message)


@dp.message(Command("support"))
async def start_support_ticket(message: types.Message, state: FSMContext):
    await state.set_state(SupportTicketForm.message)
    await message.answer("🆘 <b>تیکت پشتیبانی</b>\nمشکل یا درخواستت را در یک پیام بنویس تا برای ادمین ارسال شود.", parse_mode="HTML")


@dp.message(F.text == "🆘 پشتیبانی")
async def support_button(message: types.Message, state: FSMContext):
    await start_support_ticket(message, state)


@dp.message(SupportTicketForm.message)
async def create_support_ticket(message: types.Message, state: FSMContext):
    body = (message.text or "").strip()
    if len(body) < 5:
        await message.answer("⚠️ توضیح مشکل خیلی کوتاه است؛ کمی کامل‌تر بنویس.")
        return
    ticket_id = uuid.uuid4().hex[:10].upper()
    await support_tickets_col.insert_one({
        "ticket_id": ticket_id,
        "user_id": message.from_user.id,
        "username": message.from_user.username or "",
        "body": body[:4000],
        "status": "open",
        "created_at": datetime.utcnow().isoformat(),
    })
    await state.clear()
    await message.answer(f"✅ تیکت <code>#{ticket_id}</code> ثبت شد. ادمین به‌زودی بررسی می‌کند.", parse_mode="HTML")
    admin_text = (
        f"🆘 <b>تیکت جدید #{ticket_id}</b>\n"
        f"👤 کاربر: <code>{message.from_user.id}</code>\n"
        f"📝 {html.escape(body[:3500])}"
    )
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✍️ پاسخ به تیکت", callback_data=f"support_reply:{ticket_id}")
    ]])
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            pass


@dp.callback_query(F.data == "admin_open_tickets")
async def admin_open_tickets(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    tickets = await support_tickets_col.find({"status": "open"}).sort("created_at", -1).to_list(length=10)
    if not tickets:
        await call.message.edit_text("✅ تیکت بازی وجود ندارد.")
        return
    rows = []
    for ticket in tickets:
        rows.append([InlineKeyboardButton(text=f"✍️ #{ticket['ticket_id']} | کاربر {ticket['user_id']}", callback_data=f"support_reply:{ticket['ticket_id']}")])
    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin_back_panel")])
    await call.message.edit_text("🆘 <b>تیکت‌های باز</b>\nبرای پاسخ، یک مورد را انتخاب کن.", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@dp.callback_query(F.data.startswith("support_reply:"))
async def start_support_reply(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    ticket_id = call.data.split(":", 1)[1]
    ticket = await support_tickets_col.find_one({"ticket_id": ticket_id, "status": "open"})
    if not ticket:
        await call.message.answer("⚠️ این تیکت دیگر باز نیست یا پیدا نشد.")
        return
    await state.update_data(ticket_id=ticket_id, target_user_id=ticket["user_id"])
    await state.set_state(AdminSupportReplyForm.message)
    await call.message.answer(f"✍️ پاسخ تیکت <code>#{ticket_id}</code> را بفرست:", parse_mode="HTML")


@dp.message(AdminSupportReplyForm.message)
async def finish_support_reply(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    target_user_id = data.get("target_user_id")
    reply = (message.text or "").strip()
    await support_tickets_col.update_one({"ticket_id": ticket_id}, {"$set": {"status": "answered", "answer": reply[:4000], "answered_at": datetime.utcnow().isoformat()}})
    await state.clear()
    try:
        await bot.send_message(target_user_id, f"📬 <b>پاسخ پشتیبانی #{ticket_id}</b>\n\n{html.escape(reply)}", parse_mode="HTML")
        await message.answer("✅ پاسخ ارسال و تیکت بسته شد.")
    except Exception:
        await message.answer("⚠️ پاسخ ثبت شد، اما ارسال آن به کاربر ممکن نشد.")


@dp.message(Command("alert"))
async def create_price_alert(message: types.Message, command: CommandObject):
    if message.chat.type != "private":
        await message.answer("🚨 هشدار قیمت را در چت خصوصی با ربات تنظیم کن.")
        return
    parts = (command.args or "").split()
    if len(parts) != 3 or parts[0].upper() not in CRYPTO_PRICE_COINS or parts[1].lower() not in ("above", "below", "بالا", "پایین"):
        await message.answer("نمونه: <code>/alert TON above 5</code> یا <code>/alert DOGS below 0.0002</code>", parse_mode="HTML")
        return
    try:
        threshold = float(parts[2].replace(",", ""))
    except ValueError:
        await message.answer("⚠️ قیمت هدف معتبر نیست.")
        return
    if not math.isfinite(threshold) or threshold <= 0:
        await message.answer("⚠️ قیمت هدف باید بیشتر از صفر باشد.")
        return
    direction = "above" if parts[1].lower() in ("above", "بالا") else "below"
    alert_id = uuid.uuid4().hex[:8].upper()
    await price_alerts_col.insert_one({
        "alert_id": alert_id,
        "user_id": message.from_user.id,
        "symbol": parts[0].upper(),
        "direction": direction,
        "threshold": threshold,
        "active": True,
        "created_at": datetime.utcnow().isoformat(),
    })
    await message.answer(f"✅ هشدار <code>#{alert_id}</code> ثبت شد.\nوقتی {parts[0].upper()} {'به بالای' if direction == 'above' else 'به زیر'} <code>{threshold}</code> دلار برسد، خبرت می‌کنم.", parse_mode="HTML")


@dp.message(Command("alerts"))
async def list_price_alerts(message: types.Message):
    alerts = await price_alerts_col.find({"user_id": message.from_user.id, "active": True}).sort("created_at", -1).to_list(length=20)
    if not alerts:
        await message.answer("📭 هشدار فعالی نداری.")
        return
    lines = ["🚨 <b>هشدارهای فعال تو</b>"]
    for item in alerts:
        direction = "بالای" if item["direction"] == "above" else "زیر"
        lines.append(f"• <code>#{item['alert_id']}</code> — {item['symbol']} {direction} {item['threshold']} USD")
    lines.append("\nبرای حذف: <code>/deletealert ID</code>")
    await message.answer("\n".join(lines), parse_mode="HTML")


@dp.message(Command("deletealert"))
async def delete_price_alert(message: types.Message, command: CommandObject):
    alert_id = (command.args or "").strip().upper()
    if not alert_id:
        await message.answer("نمونه: <code>/deletealert A1B2C3D4</code>", parse_mode="HTML")
        return
    result = await price_alerts_col.update_one({"alert_id": alert_id, "user_id": message.from_user.id, "active": True}, {"$set": {"active": False, "deleted_at": datetime.utcnow().isoformat()}})
    await message.answer("✅ هشدار حذف شد." if result.modified_count else "⚠️ هشدار فعالی با این شناسه پیدا نشد.")


async def price_alert_loop():
    while True:
        try:
            alerts = await price_alerts_col.find({"active": True}).to_list(length=500)
            for alert in alerts:
                data = await fetch_crypto_price(alert["symbol"])
                if not data:
                    continue
                price = data["price"]
                triggered = price >= alert["threshold"] if alert["direction"] == "above" else price <= alert["threshold"]
                if not triggered:
                    continue
                changed = await price_alerts_col.update_one({"alert_id": alert["alert_id"], "active": True}, {"$set": {"active": False, "triggered_at": datetime.utcnow().isoformat(), "triggered_price": price}})
                if changed.modified_count:
                    try:
                        await bot.send_message(alert["user_id"], f"🚨 <b>هشدار قیمت {data['symbol']}</b>\nقیمت فعلی: <code>USD {format_crypto_price(price)}</code>\n{format_change(data.get('change_24h'))}", parse_mode="HTML")
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"Price alert loop failed: {e}")
        await asyncio.sleep(60)


async def fetch_tonapi_transaction(tx_hash: str):
    endpoint = f"https://tonapi.io/v2/blockchain/transactions/{quote(tx_hash, safe='')}"
    def request_transaction():
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "VoidGiveawayBot/6.1"})
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))
    try:
        return await asyncio.to_thread(request_transaction)
    except Exception:
        return None


@dp.message(Command(commands=["tx", "track"]))
async def track_transaction(message: types.Message, command: CommandObject):
    query = (command.args or "").strip()
    if not query:
        await message.answer("🔎 شناسه برداشت یا TX Hash را بفرست.\nمثال: <code>/tx WD123456</code>", parse_mode="HTML")
        return
    withdrawal = await withdrawals_col.find_one({"$or": [{"withdrawal_id": query}, {"tx_hash": query}, {"tx_id": query}]})
    if withdrawal:
        status = html.escape(str(withdrawal.get("status", "unknown")))
        amount = withdrawal.get("amount_to_send", withdrawal.get("amount", "-"))
        asset = html.escape(str(withdrawal.get("asset", "TON")))
        tx_hash = withdrawal.get("tx_hash") or withdrawal.get("tx_id")
        text = f"🔎 <b>وضعیت برداشت</b>\n🆔 <code>{html.escape(query)}</code>\n💎 مبلغ: <code>{amount} {asset}</code>\n📌 وضعیت: <code>{status}</code>"
        if tx_hash:
            text += f"\n🔗 شناسه شبکه: <code>{html.escape(str(tx_hash))}</code>"
        await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
        return
    deposit = await deposits_col.find_one({"$or": [{"tx_id": query}, {"memo": query}]})
    if deposit:
        await message.answer(
            f"✅ <b>وضعیت واریز</b>\n💎 مبلغ: <code>{deposit.get('amount_ton', deposit.get('amount', '-'))} TON</code>\n📌 وضعیت: <code>{html.escape(str(deposit.get('status', 'unknown')))}</code>",
            parse_mode="HTML"
        )
        return
    chain_tx = await fetch_tonapi_transaction(query)
    if chain_tx:
        await message.answer("🔗 تراکنش روی شبکه پیدا شد؛ برای اتصال آن به حساب، Memo اختصاصی واریز لازم است.", parse_mode="HTML")
    else:
        await message.answer("📭 تراکنشی با این شناسه در سوابق ربات یا شبکه پیدا نشد.")


async def build_financial_report():
    since = (datetime.utcnow() - timedelta(days=1)).isoformat()
    deposits = await deposits_col.find({"created_at": {"$gte": since}}).to_list(length=5000)
    withdrawals = await withdrawals_col.find({"created_at": {"$gte": since}}).to_list(length=5000)
    def total(items, *keys):
        result = 0.0
        for item in items:
            for key in keys:
                value = item.get(key)
                if isinstance(value, (int, float)):
                    result += float(value)
                    break
        return result
    completed_withdrawals = [item for item in withdrawals if item.get("status") in ("sent", "completed", "success")]
    return {
        "deposits_count": len(deposits),
        "deposits_ton": total(deposits, "amount_ton", "amount"),
        "withdrawals_count": len(withdrawals),
        "withdrawals_ton": total(completed_withdrawals, "amount_to_send", "amount"),
        "users": len(user_data),
        "balance_ton": sum(float(item.get("balance", 0) or 0) for item in user_data.values()),
        "balance_dogs": sum(float(item.get("dogs_balance", 0) or 0) for item in user_data.values()),
    }


@dp.callback_query(F.data == "admin_financial_report")
async def admin_financial_report(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    try:
        report = await build_financial_report()
        await call.message.edit_text(
            "📊 <b>گزارش مالی ۲۴ ساعت اخیر</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📥 واریزها: <code>{report['deposits_count']}</code> | <code>{report['deposits_ton']:.4f} TON</code>\n"
            f"📤 برداشت‌های موفق: <code>{report['withdrawals_count']}</code> | <code>{report['withdrawals_ton']:.4f} TON</code>\n"
            f"👥 کاربران ثبت‌شده: <code>{report['users']}</code>\n"
            f"💎 موجودی داخلی TON: <code>{report['balance_ton']:.4f}</code>\n"
            f"🐶 موجودی داخلی DOGS: <code>{report['balance_dogs']:.2f}</code>",
            parse_mode="HTML",
            reply_markup=get_admin_inline_keyboard()
        )
    except Exception as e:
        logging.error(f"Financial report failed: {e}")
        await call.message.answer("⚠️ گزارش مالی فعلاً قابل دریافت نیست.")


@dp.callback_query(F.data == "admin_system_health")
async def admin_system_health(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    mongo_status = "✅"
    wallet_status = "✅"
    try:
        await asyncio.wait_for(mongo_client.admin.command("ping"), timeout=5)
    except Exception:
        mongo_status = "❌"
    try:
        wallet_balance, wallet_address = await asyncio.wait_for(get_system_wallet_balance(), timeout=8)
        if not wallet_address:
            wallet_status = "⚠️"
    except Exception:
        wallet_balance, wallet_address = None, None
        wallet_status = "❌"
    await call.message.edit_text(
        "🩺 <b>سلامت سیستم</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🗄 MongoDB: {mongo_status}\n"
        f"🌐 اتصال ولت TON: {wallet_status}\n"
        f"💎 موجودی ولت سیستم: <code>{wallet_balance if wallet_balance is not None else 'نامشخص'}</code>\n"
        f"👥 کاربران در حافظه: <code>{len(user_data)}</code>\n"
        f"🤖 وضعیت ربات: {'فعال ✅' if bot_active else 'خاموش 🛑'}",
        parse_mode="HTML",
        reply_markup=get_admin_inline_keyboard()
    )


spam_tracker = {}
spam_content_tracker = {}


@dp.message(
    F.chat.type.in_({"group", "supergroup"}),
    F.text.regexp(r"(?i)(https?://|www\.|t\.me/)")
)
async def group_link_moderation(message: types.Message):
    settings = await get_group_settings(message.chat.id)
    if not settings.get("antispam_enabled", True) or await is_group_admin(message.chat.id, message.from_user.id):
        return
    try:
        await message.delete()
        await message.answer("🛡 لینک تبلیغاتی بدون اجازهٔ ادمین حذف شد.")
    except Exception:
        pass


@dp.message(F.chat.type.in_({"group", "supergroup"}), F.text)
async def group_flood_moderation(message: types.Message):
    if (message.text or "").startswith("/"):
        return
    settings = await get_group_settings(message.chat.id)
    if not settings.get("antispam_enabled", True) or await is_group_admin(message.chat.id, message.from_user.id):
        return
    now = time.monotonic()
    key = (message.chat.id, message.from_user.id)
    timestamps = [stamp for stamp in spam_tracker.get(key, []) if now - stamp < 10]
    timestamps.append(now)
    spam_tracker[key] = timestamps
    content_key = (message.chat.id, message.from_user.id, (message.text or "").strip().lower())
    repeats = [stamp for stamp in spam_content_tracker.get(content_key, []) if now - stamp < 20]
    repeats.append(now)
    spam_content_tracker[content_key] = repeats
    if len(timestamps) >= 9 or len(repeats) >= 3:
        try:
            await message.delete()
            await message.answer("🛡 پیام‌های تکراری و اسپم حذف شدند؛ لطفاً کمی آهسته‌تر پیام بفرست.")
        except Exception:
            pass


@dp.message(Command("antispam"))
async def toggle_group_antispam(message: types.Message, command: CommandObject):
    if message.chat.type not in ("group", "supergroup") or not await is_group_admin(message.chat.id, message.from_user.id):
        return
    value = (command.args or "").strip().lower()
    if value not in ("on", "off"):
        await message.answer("نمونه: <code>/antispam on</code> یا <code>/antispam off</code>", parse_mode="HTML")
        return
    await save_group_settings(message.chat.id, antispam_enabled=value == "on")
    await message.answer("✅ ضداسپم روشن شد." if value == "on" else "🛑 ضداسپم خاموش شد.")


# قابلیت‌های قیمت طبیعی قبلی حفظ شده‌اند؛ فقط منبع هشدارها و گزارش‌ها مستقل است.

# اجرای اصلی برنامه
# ==========================================
async def main():
    await load_data()
    keep_alive()
    asyncio.create_task(wallet_balance_tracker_loop())
    asyncio.create_task(deposit_tracker_loop())
    asyncio.create_task(withdrawal_recovery_loop())
    asyncio.create_task(price_alert_loop())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
