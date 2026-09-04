# ==========================================
# Void Giveaway Bot - Version 4.5.2 (Updated Release)
# (Multi-Channel Forced Join, Live Wallet Tracker, Direct Admin DM, Ban System, MongoDB Integrated)
# ==========================================

import asyncio
import base64
import os
import logging
import html
import re
import uuid
from datetime import datetime
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
    return "⚡ Void Giveaway Bot (v4.6.0) is running smoothly!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6879499219]
BOT_VERSION = "4.6.1"
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
counters_col = db['counters']

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

user_data = {}
all_time_users = set()
banned_users = set()
required_channels = ["@Voidchanneloffical"]  # پشتیبانی از چند کانال جوین اجباری

bot_active = True
auto_payout_enabled = False
min_withdraw_amount = 0.1
max_withdraw_amount = 10.0
referral_reward = 0.048
max_referrals = 50
ton_gas_fee = 0.005
tracker_message_id = None
# ارسال‌های TON باید پشت‌سرهم انجام شوند تا چند برداشت هم‌زمان از یک موجودی عبور نکند.
payout_lock = asyncio.Lock()

async def close_lite_client(client):
    """بستن امن اتصال LiteClient در همه مسیرهای موفق و خطا."""
    if client is None:
        return
    try:
        if client.is_connected():
            await client.close()
    except Exception as close_error:
        logging.warning(f"LiteClient close warning: {close_error}")


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
def ton_field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def normalize_ton_address(value):
    """آدرس friendly یا Address را به شکل raw قابل مقایسه تبدیل می‌کند."""
    try:
        if hasattr(value, "to_str"):
            value = value.to_str(is_user_friendly=False)
        value = str(value).strip()
        if re.fullmatch(r"-?\d:[0-9a-fA-F]{64}", value):
            return value.lower()
        if not re.fullmatch(r"(?:EQ|UQ)[a-zA-Z0-9_-]{46}", value):
            return None
        raw = base64.urlsafe_b64decode(value + "==")
        if len(raw) != 36:
            return None
        workchain = raw[1] if raw[1] < 128 else raw[1] - 256
        return f"{workchain}:{raw[2:].hex()}"
    except (ValueError, TypeError, base64.binascii.Error):
        return None


def extract_ton_comment(message):
    """کامنت ساده تراکنش TON را از body پیام می‌خواند."""
    body = ton_field(message, "body")
    if body is None or not hasattr(body, "begin_parse"):
        return None
    try:
        cs = body.begin_parse()
        if cs.remaining_bits < 32 or cs.load_uint(32) != 0:
            return None
        return cs.load_snake_string().strip()
    except Exception:
        return None


async def verify_payout_on_chain(sender_address: str, destination_address: str, amount_ton: float, memo: str) -> bool:
    """ورودی واقعی ولت مقصد را با فرستنده، مبلغ و memo تطبیق می‌دهد."""
    client = None
    sender_raw = normalize_ton_address(sender_address)
    destination_raw = normalize_ton_address(destination_address)
    expected_nano = int(round(amount_ton * 10**9))
    if not sender_raw or not destination_raw:
        return False

    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()
        for _ in range(6):
            transactions = await client.get_transactions(address=destination_address, count=30)
            for transaction in transactions or []:
                description = ton_field(transaction, "description")
                if ton_field(description, "aborted", False):
                    continue

                incoming = ton_field(transaction, "in_msg")
                info = ton_field(incoming, "info")
                source = ton_field(info, "src")
                destination = ton_field(info, "dest")
                value_nano = ton_field(info, "value_coins")
                if value_nano is None:
                    value = ton_field(info, "value", {})
                    value_nano = ton_field(value, "grams")

                if (
                    normalize_ton_address(source) == sender_raw
                    and normalize_ton_address(destination) == destination_raw
                    and int(value_nano or 0) == expected_nano
                    and extract_ton_comment(incoming) == memo
                ):
                    return True
            await asyncio.sleep(2)
        return False
    except Exception as e:
        logging.error(f"TON destination verification error: {e}")
        return False
    finally:
        await close_lite_client(client)


async def send_ton_payout(destination_address: str, amount_ton: float, memo: str):
    if not TON_MNEMONIC:
        return False, "کلید امنیتی ولت (TON_MNEMONIC) تنظیم نشده است!"
    if not is_valid_ton_address(destination_address):
        return False, "آدرس کیف‌پول TON معتبر نیست یا checksum آن درست نیست."
    if amount_ton <= 0:
        return False, "مبلغ واریز باید بیشتر از صفر باشد."
    if not memo or len(memo) > 64:
        return False, "شناسه memo برداشت معتبر نیست."

    # بررسی و ارسال را سریالی می‌کنیم تا برداشت‌های هم‌زمان موجودی را دوبار مصرف نکنند.
    async with payout_lock:
        system_balance, balance_info = await get_system_wallet_balance()
        required_balance = amount_ton + max(ton_gas_fee, 0)
        if system_balance is None:
            return False, f"موجودی ولت ربات قابل بررسی نیست: {balance_info}"
        if system_balance < required_balance:
            return False, (
                f"موجودی ولت ربات کافی نمی‌باشد. موجودی فعلی: {system_balance:.4f} TON؛ "
                f"مبلغ موردنیاز با کارمزد: {required_balance:.4f} TON"
            )

        client = None
        try:
            client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
            await client.connect()

            mnemonics = TON_MNEMONIC.strip().split()
            wallet = await WalletV5R1.from_mnemonic(client, mnemonics, network_global_id=-239)
            seqno_before = await wallet.get_seqno()
            await wallet.transfer(
                destination=destination_address.strip(),
                amount=int(amount_ton * 10**9),
                body=memo
            )

            # ابتدا ارسال از ولت سیستم و سپس ورود همان تراکنش به ولت مقصد تأیید می‌شود.
            for _ in range(6):
                await asyncio.sleep(2)
                try:
                    if await wallet.get_seqno() > seqno_before:
                        sender_address = wallet.address.to_str(is_user_friendly=False)
                        await close_lite_client(client)
                        client = None
                        try:
                            destination_verified = await verify_payout_on_chain(
                                sender_address, destination_address, amount_ton, memo
                            )
                        except Exception as verification_error:
                            logging.error(f"TON destination verification exception: {verification_error}")
                            destination_verified = False
                        if destination_verified:
                            return True, f"تراکنش با memo {memo} روی شبکه و ولت مقصد تأیید شد! 🚀"
                        return "pending", (
                            f"تراکنش با memo {memo} از ولت سیستم ارسال شد، اما هنوز در ولت مقصد تأیید نشده است."
                        )
                except Exception as confirm_error:
                    logging.warning(f"TON payout confirmation check failed: {confirm_error}")

            await close_lite_client(client)
            client = None
            return False, "تراکنش ارسال شد اما ارسال از ولت سیستم روی شبکه تأیید نشده است."

        except Exception as e:
            logging.error(f"pytoniq W5 Payout Error: {e}")
            await close_lite_client(client)
            return False, str(e)


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
# ذخیره و بازیابی دیتابیس MongoDB
# ==========================================
async def save_data():
    try:
        for u_id, info in user_data.items():
            user_doc = {
                "user_id": u_id,
                "balance": info.get("balance", 0.0),
                "referrals_count": info.get("referrals_count", 0),
                "referred_by": info.get("referred_by", None),
                "username": info.get("username", ""),
                "first_name": info.get("first_name", "User")
            }
            await users_col.update_one({"user_id": u_id}, {"$set": user_doc}, upsert=True)

        settings_doc = {
            "setting_id": "global_config",
            "all_time_users": list(all_time_users),
            "banned_users": list(banned_users),
            "required_channels": required_channels,
            "bot_active": bot_active,
            "auto_payout_enabled": auto_payout_enabled,
            "min_withdraw_amount": min_withdraw_amount,
            "max_withdraw_amount": max_withdraw_amount,
            "referral_reward": referral_reward,
            "max_referrals": max_referrals,
            "ton_gas_fee": ton_gas_fee,
            "tracker_message_id": tracker_message_id
        }
        await settings_col.update_one({"setting_id": "global_config"}, {"$set": settings_doc}, upsert=True)

    except Exception as e:
        logging.error(f"Error saving data to MongoDB: {e}")

async def load_data():
    global user_data, all_time_users, banned_users, required_channels, bot_active, auto_payout_enabled, min_withdraw_amount, max_withdraw_amount, referral_reward, max_referrals, ton_gas_fee, tracker_message_id
    try:
        settings_doc = await settings_col.find_one({"setting_id": "global_config"})
        if settings_doc:
            all_time_users = set(settings_doc.get("all_time_users", []))
            banned_users = set(settings_doc.get("banned_users", []))
            required_channels = settings_doc.get("required_channels", ["@Voidchanneloffical"])
            bot_active = settings_doc.get("bot_active", True)
            auto_payout_enabled = settings_doc.get("auto_payout_enabled", False)
            min_withdraw_amount = settings_doc.get("min_withdraw_amount", 0.1)
            max_withdraw_amount = settings_doc.get("max_withdraw_amount", 10.0)
            referral_reward = settings_doc.get("referral_reward", 0.048)
            max_referrals = settings_doc.get("max_referrals", 50)
            ton_gas_fee = settings_doc.get("ton_gas_fee", 0.005)
            tracker_message_id = settings_doc.get("tracker_message_id", None)

        async for user_doc in users_col.find():
            u_id = int(user_doc["user_id"])
            user_data[u_id] = {
                "balance": round(user_doc.get("balance", 0.0), 4),
                "referrals_count": user_doc.get("referrals_count", 0),
                "referred_by": user_doc.get("referred_by", None),
                "username": user_doc.get("username", ""),
                "first_name": user_doc.get("first_name", "User")
            }

    except Exception as e:
        logging.error(f"Error loading data from MongoDB: {e}")

# ==========================================
# FSM States
# ==========================================
class WithdrawForm(StatesGroup):
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

class AdminSetRefRewardForm(StatesGroup):
    amount = State()

class AdminSetMaxRefForm(StatesGroup):
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
            "referrals_count": 0,
            "referred_by": None,
            "username": "",
            "first_name": "User"
        }
    if user_obj:
        user_data[user_id]["username"] = user_obj.username or ""
        user_data[user_id]["first_name"] = user_obj.first_name or "User"
    return user_data[user_id]

def get_main_keyboard(user_id: int):
    kb = [
        [KeyboardButton(text="💎 کیف‌پول من (Wallet)"), KeyboardButton(text="🔗 دریافت لینک رفرال 🚀")],
        [KeyboardButton(text="👤 پروفایل من"), KeyboardButton(text="📊 آمار و راهنما")]
    ]
    if is_admin(user_id):
        kb.insert(0, [KeyboardButton(text="⚙️ پنل مدیریت ادمین 👑")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_admin_inline_keyboard():
    status_btn = "🛑 خاموش کردن ربات" if bot_active else "✅ روشن کردن ربات"
    auto_btn = "⚡️ واریز خودکار: غیرفعال" if not auto_payout_enabled else "⚡️ واریز خودکار: فعال"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ افزودن کانال اجباری", callback_data="admin_add_channel"), InlineKeyboardButton(text="➖ حذف کانال اجباری", callback_data="admin_remove_channel")],
            [InlineKeyboardButton(text="👥 جستجوی کاربر", callback_data="admin_search_user"), InlineKeyboardButton(text="➕/➖ تغییر موجودی", callback_data="admin_edit_balance")],
            [InlineKeyboardButton(text="💬 ارسال پیام مستقیم", callback_data="admin_direct_msg")],
            [InlineKeyboardButton(text="🚫 بن کردن کاربر", callback_data="admin_ban_user"), InlineKeyboardButton(text="🟢 آن‌بن کاربر", callback_data="admin_unban_user")],
            [InlineKeyboardButton(text="⚙️ حداقل برداشت", callback_data="admin_set_min_wd"), InlineKeyboardButton(text="🔝 حداکثر برداشت", callback_data="admin_set_max_wd")],
            [InlineKeyboardButton(text="💎 تنظیم پاداش رفرال", callback_data="admin_set_ref_reward"), InlineKeyboardButton(text="👥 تنظیم سقف رفرال", callback_data="admin_set_max_ref")],
            [InlineKeyboardButton(text="⛽️ تنظیم گس‌فی شبکه", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text=auto_btn, callback_data="admin_toggle_auto_payout")],
            [InlineKeyboardButton(text=status_btn, callback_data="admin_toggle_bot"), InlineKeyboardButton(text="📢 همه‌فرستی (Broadcast)", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="🔄 بروزرسانی آمار", callback_data="admin_stats")]
        ]
    )

# ==========================================
# پردازش رفرال و آنتی‌فیک
# ==========================================
async def process_referral_logic(user: types.User, args: str, state: FSMContext):
    u_id = user.id
    prof = get_user_profile(u_id, user)

    is_new_user = u_id not in all_time_users
    if is_new_user:
        all_time_users.add(u_id)

    if args and is_new_user and args.startswith("ref_"):
        try:
            referrer_id = int(args.replace("ref_", ""))
            if referrer_id != u_id and referrer_id in user_data:
                prof["referred_by"] = referrer_id
                ref_prof = get_user_profile(referrer_id)

                if ref_prof["referrals_count"] < max_referrals:
                    ref_prof["balance"] = round(ref_prof["balance"] + referral_reward, 4)
                    ref_prof["referrals_count"] += 1
                    await users_col.update_one(
                        {"user_id": referrer_id},
                        {"$set": user_data[referrer_id]},
                        upsert=True
                    )
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 <b>یک کاربر جدید با لینک شما وارد ربات شد!</b>\n"
                            f"💎 <b>+{referral_reward} TON</b> مستقیماً به کیف‌پول شما اضافه شد!\n"
                            f"📊 رفرال‌های شما: <code>{ref_prof['referrals_count']}/{max_referrals}</code>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"⚠️ <b>یک کاربر با لینک شما وارد شد، اما سقف رفرال شما پر است.</b>\n"
                            f"سقف مجاز: <code>{max_referrals}</code> نفر",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logging.error(f"Direct Referral Error: {e}")

    await save_data()

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
        await call.answer("✅ عضویت شما تایید شد!", show_alert=True)
        
        state_data = await state.get_data()
        pending_args = state_data.get("pending_ref_args", None)

        await process_referral_logic(call.from_user, pending_args, state)
        await state.clear()

        await call.message.delete()
        await call.message.answer(
            f"⚡️ <b>به ربات Void Giveaway خوش آمدید!</b>\n"
            f"📌 <b>نسخه ربات:</b> <code>{BOT_VERSION}</code> 💎\n\n"
            f"🎁 <b>به ازای هر رفرال {referral_reward} TON مستقیماً به کیف‌پول شما اضافه می‌شود!</b>\n\n"
            f"از منوی زیر جهت مدیریت موجودی، برداشت و دریافت لینک دعوت استفاده کنید 👇",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(u_id)
        )
    else:
        await call.answer("❌ شما هنوز در تمام کانال‌های مشخص شده عضو نشده‌اید!", show_alert=True)

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    args = command.args
    if args:
        await state.update_data(pending_ref_args=args)

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer(
            f"⚠️ <b>جهت استفاده از ربات و دریافت پاداش‌ها، ابتدا باید در کانال‌های رسمی ما عضو شوید:</b>\n\n"
            f"پس از عضویت در تمام کانال‌ها، روی دکمه «✅ بررسی عضویت / ورود» کلیک کنید.",
            parse_mode="HTML",
            reply_markup=get_join_channel_keyboard()
        )
        return

    await process_referral_logic(message.from_user, args, state)
    await state.clear()

    await message.answer(
        f"⚡️ <b>به ربات Void Giveaway خوش آمدید!</b>\n"
        f"📌 <b>نسخه ربات:</b> <code>v4.6.0</code> 💎\n\n"
        f"🎁 <b>به ازای هر رفرال {referral_reward} TON مستقیماً به کیف‌پول شما اضافه می‌شود!</b>\n\n"
        f"از منوی زیر جهت مدیریت موجودی، برداشت و دریافت لینک دعوت استفاده کنید 👇",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(u_id)
    )

# ==========================================
# سیستم برداشت و مدیریت موجودی
# ==========================================
def is_valid_ton_address(wallet_address: str) -> bool:
    """اعتبارسنجی آدرس TON با فرمت friendly و checksum استاندارد TON."""
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


def get_withdrawal_keyboard(withdrawal_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ بررسی و واریز TON", callback_data=f"wd_approve_{withdrawal_id}")],
            [InlineKeyboardButton(text="🚫 رد برداشت", callback_data=f"wd_reject_menu_{withdrawal_id}")]
        ]
    )


def get_reject_withdrawal_keyboard(withdrawal_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ رد و بازگشت مبلغ به ولت کاربر", callback_data=f"wd_reject_refund_{withdrawal_id}")],
            [InlineKeyboardButton(text="🚫 رد بدون بازگشت مبلغ", callback_data=f"wd_reject_no_refund_{withdrawal_id}")]
        ]
    )


def get_pending_verification_keyboard(withdrawal_id: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 بررسی دوباره memo", callback_data=f"wd_verify_{withdrawal_id}")]
        ]
    )


async def get_next_withdrawal_memo() -> str:
    """شمارنده اتمیک memo؛ gap مجاز است اما شماره تکراری نیست."""
    counter = await counters_col.find_one_and_update(
        {"_id": "withdrawal_memo"},
        {"$inc": {"value": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    return f"VOID-WD-{int(counter['value']):06d}"


async def create_withdrawal_record(user_id: int, wallet_address: str, requested_amount: float,
                                   amount_to_send: float, deducted_amount: float, status: str):
    withdrawal_id = uuid.uuid4().hex[:16]
    memo = await get_next_withdrawal_memo()
    now = datetime.utcnow().isoformat()
    await withdrawals_col.insert_one({
        "withdrawal_id": withdrawal_id,
        "memo": memo,
        "user_id": user_id,
        "wallet_address": wallet_address,
        "requested_amount": requested_amount,
        "amount_to_send": amount_to_send,
        "deducted_amount": deducted_amount,
        "status": status,
        "created_at": now,
        "updated_at": now
    })
    return withdrawal_id, memo


async def set_withdrawal_status(withdrawal_id: str, status: str, **fields):
    fields["status"] = status
    fields["updated_at"] = datetime.utcnow().isoformat()
    await withdrawals_col.update_one(
        {"withdrawal_id": withdrawal_id},
        {"$set": fields}
    )


async def claim_withdrawal(withdrawal_id: str):
    return await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": "pending"},
        {"$set": {"status": "processing", "updated_at": datetime.utcnow().isoformat()}},
        return_document=ReturnDocument.AFTER
    )


async def reject_withdrawal_without_refund(withdrawal_id: str, reason: str) -> bool:
    rejected = await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": "pending"},
        {"$set": {
            "status": "rejected",
            "reject_reason": reason,
            "updated_at": datetime.utcnow().isoformat()
        }},
        return_document=ReturnDocument.BEFORE
    )
    return bool(rejected)


async def refund_withdrawal(withdrawal_id: str, reason: str, allowed_statuses=("pending", "processing")) -> bool:
    status_filter = allowed_statuses[0] if len(allowed_statuses) == 1 else {"$in": list(allowed_statuses)}
    withdrawal = await withdrawals_col.find_one_and_update(
        {"withdrawal_id": withdrawal_id, "status": status_filter},
        {"$set": {
            "status": "refunded",
            "refund_reason": reason,
            "updated_at": datetime.utcnow().isoformat()
        }},
        return_document=ReturnDocument.BEFORE
    )
    if not withdrawal:
        return False

    prof = get_user_profile(int(withdrawal["user_id"]))
    prof["balance"] = round(prof.get("balance", 0.0) + float(withdrawal["deducted_amount"]), 4)
    await save_data()
    return True


@dp.message(F.text == "💎 کیف‌پول من (Wallet)")
async def show_wallet(message: types.Message):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer("⚠️ <b>برای دسترسی ابتدا باید در کانال‌ها عضو شوید:</b>", parse_mode="HTML", reply_markup=get_join_channel_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    text = (
        f"💎 <b>کیف‌پول کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>موجودی کل:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 <b>تعداد کل رفرال‌ها:</b> <code>{prof['referrals_count']}</code> نفر (سقف: {max_referrals})\n"
        f"🔹 <b>درآمد هر رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 ثبت درخواست برداشت (Withdraw)", callback_data="start_withdraw")]
        ]
    )
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@dp.callback_query(F.data == "start_withdraw")
async def start_withdraw_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id

    if is_banned(u_id):
        await call.answer("🚫 حساب شما مسدود می‌باشد.", show_alert=True)
        return

    if not bot_active and not is_admin(u_id):
        await call.answer("🛑 ربات خاموش می‌باشد.", show_alert=True)
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await call.answer("❌ ابتدا در تمام کانال‌ها عضو شوید!", show_alert=True)
        return

    prof = get_user_profile(u_id, call.from_user)
    if prof["balance"] < min_withdraw_amount:
        await call.answer(
            f"❌ موجودی شما کافی نیست! حداقل مقدار برداشت {min_withdraw_amount} TON می‌باشد.",
            show_alert=True
        )
        return

    await call.answer()
    await state.set_state(WithdrawForm.amount)
    await call.message.answer(
        f"💰 <b>موجودی قابل برداشت شما:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n\n"
        f"لطفاً مقداری که قصد برداشت دارید را به عدد وارد کنید:",
        parse_mode="HTML"
    )


@dp.message(WithdrawForm.amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    u_id = message.from_user.id
    if is_banned(u_id):
        return

    prof = get_user_profile(u_id, message.from_user)
    try:
        req_amount = float(message.text.strip())
    except (ValueError, AttributeError):
        await message.answer("⚠️ لطفاً یک عدد معتبر وارد کنید!")
        return

    if req_amount <= 0:
        await message.answer("⚠️ مقدار برداشت باید بیشتر از صفر باشد!")
        return
    if req_amount < min_withdraw_amount:
        await message.answer(f"⚠️ حداقل مقدار برداشت <code>{min_withdraw_amount} TON</code> می‌باشد!", parse_mode="HTML")
        return
    if req_amount > max_withdraw_amount:
        await message.answer(f"⚠️ حداکثر سقف برداشت در هر بار <code>{max_withdraw_amount} TON</code> می‌باشد!", parse_mode="HTML")
        return
    if req_amount > prof["balance"]:
        await message.answer("⚠️ مقدار درخواستی بیشتر از موجودی کیف‌پول شما است!")
        return

    deduct_from_balance = round(req_amount, 4)
    amount_to_send = round(req_amount - ton_gas_fee, 4)
    if amount_to_send <= 0:
        await message.answer(f"❌ مبلغ درخواستی شما باید بیشتر از کارمزد شبکه ({ton_gas_fee} TON) باشد!")
        return

    await state.update_data(
        requested_amount=req_amount,
        amount_to_send=amount_to_send,
        deduct_from_balance=deduct_from_balance
    )
    await state.set_state(WithdrawForm.wallet_address)
    await message.answer(
        f"📊 <b>جزئیات تراکنش شما:</b>\n"
        f"🔹 <b>مبلغ درخواستی:</b> <code>{req_amount:.4f} TON</code>\n"
        f"⛽️ <b>گس‌فی کسر شده:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🚀 <b>خالص دریافتی به کیف‌پول شما:</b> <code>{amount_to_send:.4f} TON</code>\n"
        f"💰 <b>مبلغ رزروشده از موجودی:</b> <code>{deduct_from_balance:.4f} TON</code>\n\n"
        f"📝 لطفاً <b>آدرس کیف‌پول TON</b> خود را بفرستید:",
        parse_mode="HTML"
    )


@dp.message(WithdrawForm.wallet_address)
async def process_withdraw_address(message: types.Message, state: FSMContext):
    u_id = message.from_user.id
    if is_banned(u_id):
        return

    wallet_addr = message.text.strip()
    if not is_valid_ton_address(wallet_addr):
        await message.answer("⚠️ آدرس TON معتبر نیست. آدرس باید با EQ یا UQ شروع شود و کامل باشد.")
        return

    data = await state.get_data()
    amount_to_send = data.get("amount_to_send")
    deduct_from_balance = data.get("deduct_from_balance")
    requested_amount = data.get("requested_amount")
    if amount_to_send is None or deduct_from_balance is None or requested_amount is None:
        await state.clear()
        await message.answer("❌ نشست برداشت منقضی شد؛ لطفاً دوباره درخواست برداشت ثبت کنید.")
        return

    user = message.from_user
    prof = get_user_profile(user.id, user)
    if deduct_from_balance > prof["balance"]:
        await state.clear()
        await message.answer("❌ موجودی حساب شما تغییر کرده است؛ درخواست برداشت لغو شد.")
        return

    is_auto = auto_payout_enabled
    if is_auto:
        # قبل از رزرو موجودی کاربر، موجودی واقعی ولت سیستم را چک می‌کنیم.
        system_balance, balance_info = await get_system_wallet_balance()
        required_balance = amount_to_send + max(ton_gas_fee, 0)
        if system_balance is None:
            reason = f"موجودی ولت سیستم قابل بررسی نیست: {balance_info}"
            await notify_wallet_issue(amount_to_send, reason)
            await state.clear()
            await message.answer(
                "⚠️ <b>برداشت خودکار موقتاً در دسترس نیست.</b>\n"
                "موجودی ولت سیستم قابل بررسی نیست؛ لطفاً بعداً دوباره تلاش کنید.",
                parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
            )
            return
        if system_balance < required_balance:
            reason = (
                f"موجودی فعلی {system_balance:.4f} TON است و "
                f"برای این برداشت حداقل {required_balance:.4f} TON لازم است."
            )
            await notify_wallet_issue(amount_to_send, reason)
            await state.clear()
            await message.answer(
                "⚠️ <b>موجودی ولت سیستم برای این برداشت کافی نیست.</b>\n"
                f"موجودی فعلی: <code>{system_balance:.4f} TON</code>\n"
                "درخواست ثبت نشد و موجودی کیف‌پول شما کسر نشد.",
                parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
            )
            return

    try:
        withdrawal_id, withdrawal_memo = await create_withdrawal_record(
            user.id, wallet_addr, requested_amount, amount_to_send,
            deduct_from_balance, "processing" if is_auto else "pending"
        )
    except Exception as e:
        logging.error(f"Withdrawal record creation error: {e}")
        await state.clear()
        await message.answer("❌ ثبت درخواست برداشت ممکن نشد؛ موجودی شما کسر نشده است.")
        return

    prof["balance"] = round(prof["balance"] - deduct_from_balance, 4)
    await save_data()
    user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a>'

    if is_auto:
        await message.answer(
            f"⏳ در حال بررسی موجودی ولت ربات و ارسال خودکار...\n🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>",
            parse_mode="HTML"
        )
        success, result_msg = await send_ton_payout(wallet_addr, amount_to_send, withdrawal_memo)
        if success:
            await set_withdrawal_status(withdrawal_id, "paid", paid_at=datetime.utcnow().isoformat())
            wallet_preview = f"{wallet_addr[:6]}...{wallet_addr[-6:]}"
            try:
                await bot.send_message(
                    chat_id=WITHDRAW_CHANNEL,
                    text=(
                        "🤖 <b>واریز خودکار انجام شد ✅</b>\n"
                        "━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>کاربر:</b> {user_mention}\n"
                        f"💎 <b>مبلغ واریزی:</b> <code>{amount_to_send:.4f} TON</code>\n"
                        f"🏦 <b>ولت مقصد:</b> <code>{html.escape(wallet_preview)}</code>\n"
                        f"🆔 <b>شناسه:</b> <code>{withdrawal_id}</code>\n"
                        f"🏷️ <b>تگ پرداخت:</b> <code>{withdrawal_memo}</code>"
                    ),
                    parse_mode="HTML"
                )
            except Exception as channel_error:
                logging.error(f"Auto payout channel announcement error: {channel_error}")

            await message.answer(
                f"🎉 <b>تراکنش با موفقیت تأیید و ارسال شد!</b>\n\n"
                f"🚀 <b>مبلغ واریزی:</b> <code>{amount_to_send:.4f} TON</code>\n"
                f"🏷️ <b>تگ پرداخت:</b> <code>{withdrawal_memo}</code>",
                parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
            )
        elif success == "pending":
            await notify_wallet_issue(amount_to_send, str(result_msg), withdrawal_id)
            await set_withdrawal_status(
                withdrawal_id, "sent_pending_verification", last_error=str(result_msg)
            )
            await message.answer(
                "⏳ <b>تراکنش از ولت سیستم ارسال شد و در انتظار تأیید ولت مقصد است.</b>\n\n"
                f"🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>\n"
                "مبلغ شما رزرو شده و پس از تأیید memo وضعیت نهایی اعلام می‌شود.",
                parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
            )
        else:
            await notify_wallet_issue(amount_to_send, str(result_msg), withdrawal_id)
            await refund_withdrawal(withdrawal_id, str(result_msg))
            await message.answer(
                f"⚠️ <b>واریز خودکار انجام نشد.</b>\n\n"
                f"علت: {html.escape(str(result_msg))}\n"
                f"💰 مبلغ <code>{deduct_from_balance:.4f} TON</code> به کیف‌پول شما برگشت داده شد.",
                parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
            )
        await state.clear()
        return

    withdraw_text = (
        f"🔔 <b>درخواست برداشت جدید TON</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>شناسه درخواست:</b> <code>{withdrawal_id}</code>\n"
        f"🏷️ <b>تگ پرداخت:</b> <code>{withdrawal_memo}</code>\n"
        f"👤 <b>کاربر:</b> {user_mention} (ID: <code>{user.id}</code>)\n"
        f"💎 <b>خالص واریزی:</b> <code>{amount_to_send:.4f} TON</code>\n"
        f"💰 <b>مبلغ رزروشده:</b> <code>{deduct_from_balance:.4f} TON</code>\n"
        f"📝 <b>آدرس کیف‌پول:</b>\n<code>{html.escape(wallet_addr)}</code>\n"
        f"⏳ <b>وضعیت:</b> معلق تا بررسی و تأیید ادمین\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

    try:
        admin_message = await bot.send_message(
            chat_id=WITHDRAW_CHANNEL,
            text=withdraw_text,
            parse_mode="HTML",
            reply_markup=get_withdrawal_keyboard(withdrawal_id)
        )
        await set_withdrawal_status(
            withdrawal_id, "pending",
            admin_message_id=admin_message.message_id,
            admin_channel=WITHDRAW_CHANNEL
        )
    except Exception as e:
        logging.error(f"Withdraw channel send error: {e}")
        await refund_withdrawal(withdrawal_id, "ارسال درخواست به کانال ادمین انجام نشد")
        await state.clear()
        await message.answer("❌ درخواست به کانال ادمین ارسال نشد؛ مبلغ به کیف‌پول شما برگشت داده شد.")
        return

    await state.clear()
    await message.answer(
        f"✅ <b>درخواست برداشت ثبت شد و معلق است.</b>\n\n"
        f"شناسه: <code>{withdrawal_id}</code>\n"
        f"🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>\n"
        f"پس از تأیید ادمین، ارسال انجام و memo در ولت مقصد بررسی می‌شود.",
        parse_mode="HTML", reply_markup=get_main_keyboard(user.id)
    )


# ==========================================
# تایید، معلق‌کردن و بازگرداندن واریزها توسط ادمین
# ==========================================
@dp.callback_query(F.data.startswith("wd_approve_"))
async def approve_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_approve_", "", 1)
    withdrawal = await claim_withdrawal(withdrawal_id)
    if not withdrawal:
        current = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
        status = current.get("status", "نامشخص") if current else "پیدا نشد"
        await call.answer(f"این درخواست قبلاً پردازش شده یا وضعیت آن {status} است.", show_alert=True)
        return

    await call.answer("⏳ در حال بررسی موجودی، memo و تأیید تراکنش...", show_alert=False)
    withdrawal_memo = withdrawal.get("memo") or f"VOID-WD-{withdrawal_id.upper()}"
    success, result_msg = await send_ton_payout(
        withdrawal["wallet_address"], float(withdrawal["amount_to_send"]), withdrawal_memo
    )
    base_text = call.message.text or call.message.caption or ""

    if success:
        await set_withdrawal_status(withdrawal_id, "paid", paid_at=datetime.utcnow().isoformat())
        await call.message.edit_text(
            base_text + f"\n\n✅ <b>وضعیت: واریز {float(withdrawal['amount_to_send']):.4f} TON با memo <code>{withdrawal_memo}</code> در ولت مقصد تأیید شد.</b>",
            parse_mode="HTML", reply_markup=None
        )
        try:
            await bot.send_message(
                int(withdrawal["user_id"]),
                f"🎉 <b>درخواست برداشت شما تأیید و ارسال شد!</b>\n\n"
                f"💎 مبلغ واریزی: <code>{float(withdrawal['amount_to_send']):.4f} TON</code>\n"
                f"🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    elif success == "pending":
        await notify_wallet_issue(float(withdrawal["amount_to_send"]), str(result_msg), withdrawal_id)
        await set_withdrawal_status(
            withdrawal_id, "sent_pending_verification", last_error=str(result_msg)
        )
        await call.message.edit_text(
            base_text + "\n\n⏳ <b>وضعیت: از ولت سیستم ارسال شد؛ تأیید memo ولت مقصد در انتظار است.</b>\n"
            f"تگ پرداخت: <code>{withdrawal_memo}</code>\n"
            "تا تأیید نهایی، درخواست را دوباره ارسال نکنید.",
            parse_mode="HTML", reply_markup=get_pending_verification_keyboard(withdrawal_id)
        )
        try:
            await bot.send_message(
                int(withdrawal["user_id"]),
                "⏳ <b>تراکنش برداشت ارسال شد و در انتظار تأیید ولت مقصد است.</b>\n"
                f"🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>\n"
                "مبلغ رزرو شده و پس از پیدا شدن تراکنش با همین memo وضعیت نهایی اعلام می‌شود.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await notify_wallet_issue(float(withdrawal["amount_to_send"]), str(result_msg), withdrawal_id)
        await set_withdrawal_status(withdrawal_id, "pending", last_error=str(result_msg))
        await call.message.edit_text(
            base_text + "\n\n⚠️ <b>وضعیت: معلق — ارسال روی شبکه تأیید نشد.</b>\n"
            f"علت: <code>{html.escape(str(result_msg))}</code>\n"
            "می‌توانی دوباره بررسی/واریز را بزنید یا مبلغ را برگردانید.",
            parse_mode="HTML", reply_markup=get_withdrawal_keyboard(withdrawal_id)
        )
        try:
            await bot.send_message(
                int(withdrawal["user_id"]),
                "⏳ <b>درخواست برداشت شما هنوز معلق است.</b>\n"
                f"🏷️ تگ پرداخت: <code>{withdrawal_memo}</code>\n"
                "ارسال تراکنش تأیید نشد و مبلغ رزروشده محفوظ مانده است.",
                parse_mode="HTML"
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("wd_verify_"))
async def verify_pending_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_verify_", "", 1)
    withdrawal = await withdrawals_col.find_one({
        "withdrawal_id": withdrawal_id, "status": "sent_pending_verification"
    })
    if not withdrawal:
        current = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
        status = current.get("status", "نامشخص") if current else "پیدا نشد"
        await call.answer(f"این درخواست در انتظار تأیید نیست؛ وضعیت: {status}", show_alert=True)
        return

    await call.answer("🔄 در حال بررسی دوباره memo در ولت مقصد...", show_alert=False)
    _, sender_address = await get_system_wallet_balance()
    memo = withdrawal.get("memo") or f"VOID-WD-{withdrawal_id.upper()}"
    verified = await verify_payout_on_chain(
        sender_address, withdrawal["wallet_address"], float(withdrawal["amount_to_send"]), memo
    )
    base_text = call.message.text or call.message.caption or ""
    if verified:
        await set_withdrawal_status(
            withdrawal_id, "paid", paid_at=datetime.utcnow().isoformat(),
            verified_at=datetime.utcnow().isoformat()
        )
        await call.message.edit_text(
            base_text + "\n\n✅ <b>memo و واریز در ولت مقصد تأیید شد.</b>",
            parse_mode="HTML", reply_markup=None
        )
        try:
            await bot.send_message(
                int(withdrawal["user_id"]),
                f"✅ <b>برداشت شما تأیید شد.</b>\n🏷️ تگ پرداخت: <code>{memo}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await call.answer("هنوز تراکنشی با این memo پیدا نشد.", show_alert=True)
        await call.message.edit_text(
            base_text + "\n\n⏳ <b>هنوز memo در ولت مقصد پیدا نشد.</b>",
            parse_mode="HTML", reply_markup=get_pending_verification_keyboard(withdrawal_id)
        )


@dp.callback_query(F.data.startswith("wd_reject_menu_"))
async def reject_withdraw_menu(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_reject_menu_", "", 1)
    withdrawal = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal or withdrawal.get("status") != "pending":
        status = withdrawal.get("status", "نامشخص") if withdrawal else "پیدا نشد"
        await call.answer(f"این درخواست قابل رد نیست؛ وضعیت فعلی: {status}", show_alert=True)
        return

    await call.answer("نوع رد برداشت را انتخاب کنید.", show_alert=True)
    base_text = call.message.text or call.message.caption or ""
    await call.message.edit_text(
        base_text + "\n\n⚠️ <b>نوع رد برداشت را انتخاب کنید:</b>\n"
        "• بازگشت مبلغ به ولت کاربر\n"
        "• رد بدون بازگشت مبلغ",
        parse_mode="HTML", reply_markup=get_reject_withdrawal_keyboard(withdrawal_id)
    )


@dp.callback_query(F.data.regexp(r"^wd_reject_[0-9a-f]+$"))
async def reject_withdraw_legacy(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_reject_", "", 1)
    withdrawal = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal or withdrawal.get("status") != "pending":
        status = withdrawal.get("status", "نامشخص") if withdrawal else "پیدا نشد"
        await call.answer(f"این درخواست قابل رد نیست؛ وضعیت فعلی: {status}", show_alert=True)
        return

    await call.answer("نوع رد برداشت را انتخاب کنید.", show_alert=True)
    base_text = call.message.text or call.message.caption or ""
    await call.message.edit_text(
        base_text + "\n\n⚠️ <b>نوع رد برداشت را انتخاب کنید:</b>\n"
        "• بازگشت مبلغ به ولت کاربر\n"
        "• رد بدون بازگشت مبلغ",
        parse_mode="HTML", reply_markup=get_reject_withdrawal_keyboard(withdrawal_id)
    )


@dp.callback_query(F.data.startswith("wd_reject_refund_"))
async def reject_withdraw_refund(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_reject_refund_", "", 1)
    withdrawal = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal:
        await call.answer("❌ درخواست برداشت پیدا نشد.", show_alert=True)
        return

    refunded = await refund_withdrawal(withdrawal_id, "رد درخواست توسط ادمین و بازگشت مبلغ", allowed_statuses=("pending",))
    if not refunded:
        current = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
        status = current.get("status", "نامشخص") if current else "پیدا نشد"
        await call.answer(f"این درخواست قبلاً پردازش شده؛ وضعیت: {status}", show_alert=True)
        return

    await call.answer("✅ درخواست رد شد و مبلغ به کاربر برگشت.", show_alert=True)
    base_text = call.message.text or call.message.caption or ""
    await call.message.edit_text(
        base_text + "\n\n↩️ <b>وضعیت: رد شد و مبلغ به کیف‌پول کاربر برگشت داده شد.</b>",
        parse_mode="HTML", reply_markup=None
    )
    try:
        await bot.send_message(
            int(withdrawal["user_id"]),
            "↩️ <b>درخواست برداشت شما رد شد.</b>\nمبلغ رزروشده به کیف‌پول شما برگشت داده شد.",
            parse_mode="HTML"
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("wd_reject_no_refund_"))
async def reject_withdraw_no_refund(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    withdrawal_id = call.data.replace("wd_reject_no_refund_", "", 1)
    withdrawal = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
    if not withdrawal:
        await call.answer("❌ درخواست برداشت پیدا نشد.", show_alert=True)
        return

    rejected = await reject_withdrawal_without_refund(withdrawal_id, "رد درخواست توسط ادمین بدون بازگشت مبلغ")
    if not rejected:
        current = await withdrawals_col.find_one({"withdrawal_id": withdrawal_id})
        status = current.get("status", "نامشخص") if current else "پیدا نشد"
        await call.answer(f"این درخواست قبلاً پردازش شده؛ وضعیت: {status}", show_alert=True)
        return

    await call.answer("✅ درخواست بدون بازگشت مبلغ رد شد.", show_alert=True)
    base_text = call.message.text or call.message.caption or ""
    await call.message.edit_text(
        base_text + "\n\n🚫 <b>وضعیت: رد شد و مبلغ به کاربر بازگردانده نشد.</b>",
        parse_mode="HTML", reply_markup=None
    )
    try:
        await bot.send_message(
            int(withdrawal["user_id"]),
            "🚫 <b>درخواست برداشت شما رد شد.</b>\nمبلغ رزروشده طبق تصمیم ادمین به کیف‌پول شما بازگردانده نشد.",
            parse_mode="HTML"
        )
    except Exception:
        pass


# ==========================================
# گزینه‌های پروفایل و راهنما
# ==========================================
@dp.message(F.text == "🔗 دریافت لینک رفرال 🚀")
async def send_referral_link_menu(message: types.Message):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer("⚠️ <b>برای دسترسی ابتدا باید در کانال‌ها عضو شوید:</b>", parse_mode="HTML", reply_markup=get_join_channel_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    bot_info = await bot.get_me()
    
    general_ref_link = f"https://t.me/{bot_info.username}?start=ref_{u_id}"
    
    text = (
        f"🚀 <b>لینک دعوت اختصاصی شما:</b>\n\n"
        f"🔗 <code>{general_ref_link}</code>\n\n"
        f"💎 <b>پاداش دعوت:</b> به ازای هر کاربر جدید <b>{referral_reward} TON</b>\n"
        f"👥 <b>مجموع دعوت‌های معتبر شما:</b> {prof['referrals_count']} از {max_referrals} نفر\n\n"
            )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == "👤 پروفایل من")
async def show_profile(message: types.Message):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer("⚠️ <b>برای دسترسی ابتدا باید در کانال‌ها عضو شوید:</b>", parse_mode="HTML", reply_markup=get_join_channel_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    text = (
        f"👤 <b>پروفایل کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>آیدی عددی:</b> <code>{u_id}</code>\n"
        f"💎 <b>موجودی ولت:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 <b>تعداد رفرال‌ها:</b> {prof['referrals_count']} از {max_referrals} نفر\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📊 آمار و راهنما")
async def show_help(message: types.Message):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer("⚠️ <b>برای دسترسی ابتدا باید در کانال‌ها عضو شوید:</b>", parse_mode="HTML", reply_markup=get_join_channel_keyboard())
        return

    text = (
        f"ℹ️ <b>راهنمای ربات Void Giveaway:</b>\n\n"
        f"1️⃣ با دعوت هر کاربر جدید از طریق لینک رفرال خود <b>{referral_reward} TON</b> پاداش دریافت می‌کنید.\n"
        f"2️⃣ سقف مجاز دریافت رفرال برای هر کاربر برابر با <b>{max_referrals} نفر</b> می‌باشد.\n"
        f"3️⃣ پاداش‌ها مستقیماً وارد «کیف‌پول من» می‌شوند.\n"
        f"4️⃣ پس از رسیدن به حداقل کف برداشت (<code>{min_withdraw_amount} TON</code>) می‌توانید درخواست واریز ثبت کنید."
    )
    await message.answer(text, parse_mode="HTML")

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
        "👑 <b>داشبورد مدیریت ربات Void Giveaway (v4.6.0)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>موجودی واقعی ولت اصلی ربات:</b> {wallet_str}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>وضعیت ربات:</b> {'روشن ✅' if bot_active else 'خاموش/تعمیرات 🛑'}\n"
        f"⚡️ <b>سیستم واریز:</b> {'خودکار اتوماتیک 🚀' if auto_payout_enabled else 'دستی (تایید کانال) 📝'}\n"
        f"📢 <b>کانال‌های جوین اجباری ({len(required_channels)}):</b> {ch_list_str}\n"
        f"👥 <b>کاربران فعال فعلی:</b> <code>{total_users}</code> نفر\n"
        f"📜 <b>کل کاربران تاریخی:</b> <code>{total_all_time}</code> نفر\n"
        f"🚫 <b>کاربران بن شده:</b> <code>{banned_count}</code> نفر\n"
        f"💰 <b>مجموع موجودی ولت کاربران:</b> <code>{total_balance:.4f} TON</code>\n"
        f"🎁 <b>پاداش هر رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"👥 <b>سقف تعداد رفرال مجاز:</b> <code>{max_referrals}</code> نفر\n"
        f"⛽️ <b>گس‌فی شبکه TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "جهت مدیریت از دکمه‌های زیر استفاده کنید:"
    )
    
    await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_inline_keyboard())

# --- افزودن و حذف کانال جوین اجباری ---
@dp.callback_query(F.data == "admin_add_channel")
async def start_add_channel(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminAddChannelForm.channel)
    await call.message.edit_text("➕ <b>یوزرنیم کانال جدید جهت اضافه کردن به جوین اجباری را بفرستید (مثال: @mychannel):</b>", parse_mode="HTML")

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
    await message.answer(f"✅ کانال <code>{channel_id}</code> با موفقیت به قفل جوین اجباری اضافه شد!", parse_mode="HTML")

@dp.callback_query(F.data == "admin_remove_channel")
async def start_remove_channel(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    
    if not required_channels:
        await call.message.edit_text("⚠️ هیچ کانالی در لیست وجود ندارد.")
        return

    buttons = []
    for ch in required_channels:
        buttons.append([InlineKeyboardButton(text=f"❌ حذف {ch}", callback_data=f"remove_ch_{ch}")])
    buttons.append([InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin_stats")])
    
    await call.message.edit_text("➖ <b>جهت حذف کانال روی گزینه مورد نظر کلیک کنید:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

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

@dp.callback_query(F.data == "admin_toggle_auto_payout")
async def toggle_auto_payout_callback(call: types.CallbackQuery):
    global auto_payout_enabled
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    
    auto_payout_enabled = not auto_payout_enabled
    await save_data()
    status_msg = "⚡️ واریز خودکار فعال شد." if auto_payout_enabled else "📝 واریز به حالت دستی تغییر یافت."
    await call.answer(status_msg, show_alert=True)
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_stats")
async def show_stats_callback(call: types.CallbackQuery):
    await call.answer("🔄 اطلاعات و موجودی ولت به‌روزرسانی شد", show_alert=False)
    if not is_admin(call.from_user.id):
        return
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
    await message.answer(f"✅ کاربر <code>{target_id}</code> با موفقیت بن شد!", parse_mode="HTML")

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
        await message.answer(f"✅ کاربر <code>{target_id}</code> آن‌بن شد!", parse_mode="HTML")
    else:
        await message.answer("⚠️ این کاربر در لیست بن شده‌ها قرار ندارد.")

# --- ارسال پیام مستقیم ---
@dp.callback_query(F.data == "admin_direct_msg")
async def start_direct_message(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminDirectMessageForm.user_id)
    await call.message.edit_text("💬 <b>آیدی عددی (User ID) کاربر مورد نظر را بفرستید:</b>", parse_mode="HTML")

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
        await message.answer(f"✅ <b>پیام شما با موفقیت برای کاربر <code>{target_id}</code> ارسال شد!</b>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ <b>خطا در ارسال پیام:</b> {e}", parse_mode="HTML")

# --- جستجوی کاربر ---
@dp.callback_query(F.data == "admin_search_user")
async def start_search_user(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSearchUserForm.user_id)
    await call.message.edit_text("🔍 <b>آیدی عددی (User ID) کاربر مورد نظر را بفرستید:</b>", parse_mode="HTML")

@dp.message(AdminSearchUserForm.user_id)
async def process_search_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً یک آیدی عددی معتبر وارد کنید!")
        return

    target_id = int(message.text)
    await state.clear()

    if target_id not in user_data:
        await message.answer(f"❌ کاربر با آیدی <code>{target_id}</code> در دیتابیس ربات یافت نشد!", parse_mode="HTML")
        return

    target_prof = user_data[target_id]
    ban_status = "بله 🚫" if is_banned(target_id) else "خیر 🟢"
    
    referrals_list = []
    for u_id, u_info in user_data.items():
        if u_info.get("referred_by") == target_id:
            u_name = f"@{u_info['username']}" if u_info.get("username") else html.escape(u_info.get("first_name", "User"))
            referrals_list.append(f"• {u_name} (ID: <code>{u_id}</code>) - رفرال‌ها: {u_info.get('referrals_count', 0)}")

    ref_by_str = f"<code>{target_prof['referred_by']}</code>" if target_prof.get("referred_by") else "مستقیم (بدون دعوت‌کننده)"
    
    ref_list_str = "\n".join(referrals_list[:20]) if referrals_list else "<i>هیچ زیرمجموعه‌ای ندارد.</i>"
    if len(referrals_list) > 20:
        ref_list_str += f"\n<i>... و {len(referrals_list) - 20} کاربر دیگر</i>"

    user_info_text = (
        f"👤 <b>اطلاعات کاربر <code>{target_id}</code>:</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>نام:</b> {html.escape(target_prof.get('first_name', 'User'))}\n"
        f"🆔 <b>یوزرنیم:</b> @{target_prof.get('username', 'ندارد')}\n"
        f"🚫 <b>وضعیت بن:</b> {ban_status}\n"
        f"💰 <b>موجودی TON:</b> <code>{target_prof.get('balance', 0.0):.4f} TON</code>\n"
        f"👥 <b>تعداد کل رفرال‌ها:</b> <code>{target_prof.get('referrals_count', 0)}</code>/{max_referrals} نفر\n"
        f"🔗 <b>دعوت‌شده توسط:</b> {ref_by_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>لیست زیرمجموعه‌های این کاربر:</b>\n"
        f"{ref_list_str}"
    )

    await message.answer(user_info_text, parse_mode="HTML")

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
        min_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ حداقل کف برداشت به <code>{min_withdraw_amount} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

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
        max_withdraw_amount = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ حداکثر سقف برداشت به <code>{max_withdraw_amount} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

@dp.callback_query(F.data == "admin_set_ref_reward")
async def start_set_ref_reward(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetRefRewardForm.amount)
    await call.message.edit_text(f"💎 <b>مقدار پاداش جدید برای هر رفرال به TON را وارد کنید (فعلی: {referral_reward} TON):</b>", parse_mode="HTML")

@dp.message(AdminSetRefRewardForm.amount)
async def process_set_ref_reward(message: types.Message, state: FSMContext):
    global referral_reward
    try:
        amount = float(message.text.strip())
        referral_reward = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ پاداش هر رفرال با موفقیت به <code>{referral_reward} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

@dp.callback_query(F.data == "admin_set_max_ref")
async def start_set_max_ref(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMaxRefForm.amount)
    await call.message.edit_text(f"👥 <b>سقف مجاز تعداد رفرال برای هر کاربر را وارد کنید (فعلی: {max_referrals}):</b>", parse_mode="HTML")

@dp.message(AdminSetMaxRefForm.amount)
async def process_set_max_ref(message: types.Message, state: FSMContext):
    global max_referrals
    if not message.text.strip().isdigit():
        await message.answer("⚠️ لطفاً یک عدد صحیح معتبر وارد کنید!")
        return
    max_referrals = int(message.text.strip())
    await save_data()
    await state.clear()
    await message.answer(f"✅ سقف تعداد رفرال با موفقیت به <code>{max_referrals}</code> نفر تغییر یافت!", parse_mode="HTML")

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
        ton_gas_fee = amount
        await save_data()
        await state.clear()
        await message.answer(f"✅ گس‌فی شبکه TON به <code>{ton_gas_fee} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

@dp.callback_query(F.data == "admin_edit_balance")
async def start_edit_balance(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminManageUserForm.user_id)
    await call.message.edit_text("👤 <b>آیدی عددی (User ID) کاربر مورد نظر را بفرستید:</b>", parse_mode="HTML")

@dp.message(AdminManageUserForm.user_id)
async def process_edit_balance_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ آیدی عددی معتبر وارد کنید!")
        return
    await state.update_data(target_u_id=int(message.text))
    await state.set_state(AdminManageUserForm.amount)
    await message.answer("💎 مقدار TON را وارد کنید (مثال: 0.5 برای افزایش یا -0.5 برای کاهش):", parse_mode="HTML")

@dp.message(AdminManageUserForm.amount)
async def process_edit_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ مقدار عددی معتبر وارد کنید!")
        return

    data = await state.get_data()
    target_id = data.get("target_u_id")
    
    prof = get_user_profile(target_id)
    prof["balance"] = round(prof["balance"] + amount, 4)
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
    await call.message.edit_text("📢 <b>پیامی که می‌خواهید به تمام کاربران ارسال شود را بفرستید:</b>", parse_mode="HTML")

@dp.message(AdminBroadcastForm.message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    await state.clear()
    sent_count = 0
    fail_count = 0
    
    msg = await message.answer("⏳ در حال ارسال پیام به کاربران...")
    
    for u_id in list(user_data.keys()):
        try:
            await message.copy_to(chat_id=u_id)
            sent_count += 1
            await asyncio.sleep(0.04)
        except Exception:
            fail_count += 1

    await msg.edit_text(
        f"✅ <b>همه‌فرستی پایان یافت!</b>\n\n📥 موفق: {sent_count}\n❌ ناموفق: {fail_count}",
        parse_mode="HTML"
    )

# ==========================================
# اجرای اصلی برنامه
# ==========================================
async def main():
    await load_data()
    keep_alive()
    asyncio.create_task(wallet_balance_tracker_loop())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
