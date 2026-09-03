# ==========================================
# Void Bot - Version 5.0.0 (Fast & Optimized)
# (Multi Forced-Join Channels, Referral Max Limit, Wallet Live Channel Sync, MongoDB Integrated)
# ==========================================

import asyncio
import os
import logging
import html
import re
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

logging.basicConfig(level=logging.INFO)
logging.getLogger("pytoniq").setLevel(logging.WARNING)
logging.getLogger("LiteClient").setLevel(logging.WARNING)

# ==========================================
# Flask Web Server (Keep-Alive)
# ==========================================
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ Void Bot (v5.0.0) is running fast & smooth!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ==========================================
# Configs & Environment
# ==========================================
TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6879499219]
WITHDRAW_CHANNEL = "@voidwithraw"
WALLET_STATUS_CHANNEL = "@Voidchanneloffical"  # کانالی که موجودی ولت هر ۳ دقیقه در آن آپدیت می‌شود
TON_MNEMONIC = os.environ.get("TON_MNEMONIC")

MONGO_URI = os.environ.get("MONGO_URI", "")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client['void_bot_db']

users_col = db['users']
settings_col = db['settings']

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ==========================================
# Global Variables
# ==========================================
user_data = {}
all_time_users = set()
banned_users = set()
required_channels = ["@Voidchanneloffical"]  # لیست کانال‌های جوین اجباری

bot_active = True
auto_payout_enabled = False
min_withdraw_amount = 0.1
max_withdraw_amount = 10.0
referral_reward = 0.048
max_referrals = 50  # حداکثر تعداد رفرال مجاز برای هر کاربر
ton_gas_fee = 0.005
wallet_status_msg_id = None  # آیدی پیام موجودی ولت در کانال

# ==========================================
# Helper Functions & Wallet Utilities
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

        await client.close()
        return balance_ton, wallet.address.to_str(is_user_friendly=True, is_bounceable=False)
    except Exception as e:
        logging.error(f"Error fetching wallet balance: {e}")
        if client and client.is_connected():
            await client.close()
        return None, str(e)

async def check_user_subscription(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    for channel in required_channels:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["creator", "administrator", "member"]:
                return False
        except Exception as e:
            logging.error(f"Subscription Check Error for {channel}: {e}")
            continue
    return True

def get_join_channels_keyboard():
    buttons = []
    for idx, ch in enumerate(required_channels, start=1):
        clean_ch = ch.replace("@", "")
        buttons.append([InlineKeyboardButton(text=f"📢 عضویت در کانال {idx}", url=f"https://t.me/{clean_ch}")])
    buttons.append([InlineKeyboardButton(text="✅ بررسی عضویت / ورود", callback_data="check_join_btn")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def send_ton_payout(destination_address: str, amount_ton: float):
    if not TON_MNEMONIC:
        return False, "کلید امنیتی ولت (TON_MNEMONIC) تنظیم نشده است!"
    
    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()

        mnemonics = TON_MNEMONIC.strip().split()
        wallet = await WalletV5R1.from_mnemonic(client, mnemonics, network_global_id=-239)
        amount_nano = int(amount_ton * 10**9)

        await wallet.transfer(
            destination=destination_address.strip(),
            amount=amount_nano,
            body="Payout from Void Bot 🚀"
        )
        await client.close()
        return True, f"تراکنش انجام شد! (مبلغ: {amount_ton:.4f} TON)"
    except Exception as e:
        logging.error(f"Payout Error: {e}")
        if client and client.is_connected():
            await client.close()
        return False, str(e)

# ==========================================
# MongoDB Sync
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
            "wallet_status_msg_id": wallet_status_msg_id
        }
        await settings_col.update_one({"setting_id": "global_config"}, {"$set": settings_doc}, upsert=True)
    except Exception as e:
        logging.error(f"Error saving to MongoDB: {e}")

async def load_data():
    global user_data, all_time_users, banned_users, required_channels, bot_active, auto_payout_enabled
    global min_withdraw_amount, max_withdraw_amount, referral_reward, max_referrals, ton_gas_fee, wallet_status_msg_id
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
            wallet_status_msg_id = settings_doc.get("wallet_status_msg_id", None)

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
        logging.error(f"Error loading from MongoDB: {e}")

# ==========================================
# Task: Update Channel Balance Every 3 Mins
# ==========================================
async def update_wallet_balance_channel_task():
    global wallet_status_msg_id
    while True:
        try:
            balance, wallet_addr = await get_system_wallet_balance()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if balance is not None:
                text = (
                    f"⚡️ <b>موجودی زنده کیف‌پول سیستم</b> ⚡️\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💎 <b>موجودی فعلی:</b> <code>{balance:.4f} TON</code>\n"
                    f"💳 <b>آدرس ولت:</b>\n<code>{wallet_addr}</code>\n\n"
                    f"⏰ <b>آخرین بروزرسانی:</b> {now_str}\n"
                    f"🔄 <i>این پیام هر ۳ دقیقه به‌روزرسانی می‌شود.</i>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━"
                )
            else:
                text = f"⚠️ <b>خطا در استعلام موجودی ولت:</b> {wallet_addr}\n⏰ {now_str}"

            if wallet_status_msg_id:
                try:
                    await bot.edit_message_text(chat_id=WALLET_STATUS_CHANNEL, message_id=wallet_status_msg_id, text=text, parse_mode="HTML")
                except TelegramBadRequest as e:
                    if "message is not modified" not in str(e):
                        msg = await bot.send_message(chat_id=WALLET_STATUS_CHANNEL, text=text, parse_mode="HTML")
                        wallet_status_msg_id = msg.message_id
                        await save_data()
            else:
                msg = await bot.send_message(chat_id=WALLET_STATUS_CHANNEL, text=text, parse_mode="HTML")
                wallet_status_msg_id = msg.message_id
                await save_data()

        except Exception as e:
            logging.error(f"Wallet channel sync error: {e}")

        await asyncio.sleep(180)  # هر ۱۸۰ ثانیه (۳ دقیقه)

# ==========================================
# FSM States
# ==========================================
class WithdrawForm(StatesGroup):
    amount = State()
    wallet_address = State()

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

class AdminAddChannelForm(StatesGroup):
    channel = State()

class AdminRemoveChannelForm(StatesGroup):
    channel = State()

class AdminSetConfigForm(StatesGroup):
    value = State()

# ==========================================
# General Helpers
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
            [InlineKeyboardButton(text="📢 مدیریت کانال‌های اجباری", callback_data="admin_manage_channels")],
            [InlineKeyboardButton(text="👥 جستجوی کاربر", callback_data="admin_search_user"), InlineKeyboardButton(text="➕/➖ تغییر موجودی", callback_data="admin_edit_balance")],
            [InlineKeyboardButton(text="💬 ارسال پیام مستقیم", callback_data="admin_direct_msg")],
            [InlineKeyboardButton(text="🚫 بن کردن کاربر", callback_data="admin_ban_user"), InlineKeyboardButton(text="🟢 آن‌بن کاربر", callback_data="admin_unban_user")],
            [InlineKeyboardButton(text="🎯 تنظیم حداکثر رفرال", callback_data="admin_set_max_ref"), InlineKeyboardButton(text="💎 تنظیم پاداش رفرال", callback_data="admin_set_ref_reward")],
            [InlineKeyboardButton(text="⚙️ حداقل برداشت", callback_data="admin_set_min_wd"), InlineKeyboardButton(text="🔝 حداکثر برداشت", callback_data="admin_set_max_wd")],
            [InlineKeyboardButton(text="⛽️ تنظیم گس‌فی شبکه", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text=auto_btn, callback_data="admin_toggle_auto_payout")],
            [InlineKeyboardButton(text=status_btn, callback_data="admin_toggle_bot"), InlineKeyboardButton(text="📢 همه‌فرستی (Broadcast)", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="🔄 بروزرسانی آمار", callback_data="admin_stats")]
        ]
    )

# ==========================================
# Referral Processing (Fast & Simplified)
# ==========================================
async def process_referral_logic(user: types.User, args: str):
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
                    
                    await users_col.update_one({"user_id": referrer_id}, {"$set": user_data[referrer_id]}, upsert=True)
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 <b>یک کاربر جدید با لینک شما وارد ربات شد!</b>\n"
                            f"💎 <b>+{referral_reward} TON</b> مستقیماً به کیف‌پول شما اضافه شد!",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                else:
                    try:
                        await bot.send_message(
                            referrer_id,
                            f"⚠️ <b>یک کاربر با لینک شما وارد شد اما سقف رفرال شما ({max_referrals} نفر) پر شده است!</b>",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logging.error(f"Referral Error: {e}")

    await save_data()

# ==========================================
# Start Handlers
# ==========================================
@dp.callback_query(F.data == "check_join_btn")
async def check_join_btn_callback(call: types.CallbackQuery, state: FSMContext):
    u_id = call.from_user.id
    
    if is_banned(u_id):
        await call.answer("🚫 حساب شما مسدود است.", show_alert=True)
        return

    if not bot_active and not is_admin(u_id):
        await call.answer("🛑 ربات خاموش می‌باشد.", show_alert=True)
        return

    is_subscribed = await check_user_subscription(u_id)
    if is_subscribed:
        await call.answer("✅ عضویت شما تایید شد!", show_alert=True)
        
        state_data = await state.get_data()
        pending_args = state_data.get("pending_ref_args", None)

        await process_referral_logic(call.from_user, pending_args)
        await state.clear()

        await call.message.delete()
        await call.message.answer(
            f"⚡️ <b>به ربات Void خوش آمدید!</b>\n"
            f"📌 <b>نسخه:</b> <code>v5.0.0</code> 💎\n\n"
            f"🎁 <b>پاداش دعوت هر کاربر: {referral_reward} TON</b>\n\n"
            f"از منوی زیر استفاده کنید 👇",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(u_id)
        )
    else:
        await call.answer("❌ هنوز در تمام کانال‌ها عضو نشده‌اید!", show_alert=True)

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    u_id = message.from_user.id

    if is_banned(u_id):
        await message.answer("🚫 <b>حساب کاربری شما مسدود می‌باشد.</b>", parse_mode="HTML")
        return

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    args = command.args
    if args:
        await state.update_data(pending_ref_args=args)

    is_subscribed = await check_user_subscription(u_id)
    if not is_subscribed:
        await message.answer(
            f"⚠️ <b>جهت استفاده از ربات لطفاً در کانال‌های زیر عضو شوید:</b>",
            parse_mode="HTML",
            reply_markup=get_join_channels_keyboard()
        )
        return

    await process_referral_logic(message.from_user, args)
    await state.clear()

    await message.answer(
        f"⚡️ <b>به ربات Void خوش آمدید!</b>\n"
        f"📌 <b>نسخه:</b> <code>v5.0.0</code> 💎\n\n"
        f"🎁 <b>پاداش دعوت هر کاربر: {referral_reward} TON</b>\n\n"
        f"از منوی زیر استفاده کنید 👇",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(u_id)
    )

# ==========================================
# User Wallet & Withdraw System
# ==========================================
@dp.message(F.text == "💎 کیف‌پول من (Wallet)")
async def show_wallet(message: types.Message):
    u_id = message.from_user.id

    if is_banned(u_id) or (!bot_active and not is_admin(u_id)):
        return

    if not await check_user_subscription(u_id):
        await message.answer("⚠️ لطفاً ابتدا در کانال‌ها عضو شوید:", reply_markup=get_join_channels_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    text = (
        f"💎 <b>کیف‌پول کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>موجودی کل:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 <b>تعداد کل رفرال‌ها:</b> <code>{prof['referrals_count']} / {max_referrals}</code> نفر\n"
        f"🔹 <b>درآمد هر رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📤 ثبت درخواست برداشت (Withdraw)", callback_data="start_withdraw")]])
    await message.answer(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "start_withdraw")
async def start_withdraw_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    u_id = call.from_user.id

    prof = get_user_profile(u_id, call.from_user)
    if prof["balance"] < min_withdraw_amount:
        await call.answer(f"❌ حداقل مقدار برداشت {min_withdraw_amount} TON می‌باشد.", show_alert=True)
        return

    await state.set_state(WithdrawForm.amount)
    await call.message.answer(f"💰 لطفاً مقدار مورد نظر جهت برداشت را به عدد وارد کنید:", parse_mode="HTML")

@dp.message(WithdrawForm.amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    u_id = message.from_user.id
    prof = get_user_profile(u_id, message.from_user)
    
    try:
        req_amount = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر وارد کنید!")
        return

    if req_amount < min_withdraw_amount or req_amount > max_withdraw_amount or req_amount > prof["balance"]:
        await message.answer("⚠️ مقدار وارد شده نامعتبر یا بیشتر از حد مجاز است!")
        return

    amount_to_send = req_amount - ton_gas_fee
    if amount_to_send <= 0:
        await message.answer(f"❌ مبلغ درخواستی باید بیشتر از کارمزد شبکه ({ton_gas_fee} TON) باشد!")
        return

    await state.update_data(requested_amount=req_amount, amount_to_send=round(amount_to_send, 4), deduct_from_balance=round(req_amount, 4))
    await state.set_state(WithdrawForm.wallet_address)
    await message.answer(f"📝 لطفاً <b>آدرس ولت TON (مانند EQ... یا UQ...)</b> خود را ارسال کنید:", parse_mode="HTML")

@dp.message(WithdrawForm.wallet_address)
async def process_withdraw_address(message: types.Message, state: FSMContext):
    u_id = message.from_user.id
    wallet_addr = message.text.strip()
    data = await state.get_data()
    
    amount_to_send = data.get("amount_to_send")
    deduct_from_balance = data.get("deduct_from_balance")
    prof = get_user_profile(u_id, message.from_user)

    if deduct_from_balance > prof["balance"]:
        await message.answer("❌ خطا در موجودی.")
        await state.clear()
        return

    prof["balance"] = round(prof["balance"] - deduct_from_balance, 4)
    await save_data()

    user_mention = f"@{message.from_user.username}" if message.from_user.username else html.escape(message.from_user.first_name)

    if auto_payout_enabled:
        await message.answer("⏳ در حال پردازش و واریز خودکار...", parse_mode="HTML")
        success, result_msg = await send_ton_payout(wallet_addr, amount_to_send)
        if success:
            await message.answer(f"🎉 <b>واریز خودکار انجام شد!</b>\nمبلغ: <code>{amount_to_send} TON</code>", parse_mode="HTML")
        else:
            prof["balance"] = round(prof["balance"] + deduct_from_balance, 4)
            await save_data()
            await message.answer(f"❌ خطا در واریز: {result_msg}\nمبلغ به حساب بازگشت.", parse_mode="HTML")
        await state.clear()
        return

    withdraw_text = (
        f"🔔 <b>درخواست برداشت جدید TON!</b>\n"
        f"👤 کاربر: {user_mention} (ID: <code>{u_id}</code>)\n"
        f"💎 خالص واریزی: <code>{amount_to_send} TON</code>\n"
        f"📝 آدرس ولت:\n<code>{html.escape(wallet_addr)}</code>"
    )
    admin_action_kb = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ واریز اتوماتیک", callback_data=f"wd_approve_{u_id}_{amount_to_send}"),
            InlineKeyboardButton(text="❌ رد درخواست", callback_data=f"wd_reject_{u_id}_{deduct_from_balance}")
        ]]
    )
    try:
        await bot.send_message(chat_id=WITHDRAW_CHANNEL, text=withdraw_text, parse_mode="HTML", reply_markup=admin_action_kb)
    except Exception as e:
        logging.error(f"Withdraw channel error: {e}")

    await state.clear()
    await message.answer("✅ <b>درخواست برداشت شما ثبت و به پشتیبانی ارسال شد.</b>", parse_mode="HTML", reply_markup=get_main_keyboard(u_id))

# ==========================================
# Withdraw Approvals (Admin)
# ==========================================
@dp.callback_query(F.data.startswith("wd_approve_"))
async def approve_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split("_")
    target_user_id = int(parts[2])
    amount_to_send = float(parts[3])

    match = re.search(r'(EQ[a-zA-Z0-9_-]{46}|UQ[a-zA-Z0-9_-]{46})', call.message.text or "")
    if not match:
        await call.answer("❌ آدرس ولت یافت نشد!", show_alert=True)
        return

    success, result_msg = await send_ton_payout(match.group(1), amount_to_send)
    if success:
        await call.message.edit_text(call.message.text + f"\n\n✅ <b>واریز {amount_to_send} TON با موفقیت انجام شد!</b>", parse_mode="HTML")
        try:
            await bot.send_message(target_user_id, f"🎉 <b>درخواست برداشت {amount_to_send} TON واریز شد!</b>", parse_mode="HTML")
        except Exception:
            pass
    else:
        await call.answer(f"❌ خطا: {result_msg}", show_alert=True)

@dp.callback_query(F.data.startswith("wd_reject_"))
async def reject_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    parts = call.data.split("_")
    target_user_id = int(parts[2])
    deduct_amount = float(parts[3])

    if target_user_id in user_data:
        user_data[target_user_id]["balance"] = round(user_data[target_user_id]["balance"] + deduct_amount, 4)
        await save_data()

    await call.message.edit_text(call.message.text + "\n\n❌ <b>رد شد و مبلغ به کیف‌پول بازگشت.</b>", parse_mode="HTML")
    try:
        await bot.send_message(target_user_id, "❌ <b>درخواست برداشت شما رد شد و مبلغ بازگردانده شد.</b>", parse_mode="HTML")
    except Exception:
        pass

# ==========================================
# User Referral & Profile Buttons
# ==========================================
@dp.message(F.text == "🔗 دریافت لینک رفرال 🚀")
async def send_referral_link_menu(message: types.Message):
    u_id = message.from_user.id
    if is_banned(u_id) or (!bot_active and not is_admin(u_id)):
        return
    if not await check_user_subscription(u_id):
        await message.answer("⚠️ لطفاً ابتدا در کانال‌ها عضو شوید:", reply_markup=get_join_channels_keyboard())
        return

    prof = get_user_profile(u_id, message.from_user)
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{u_id}"
    
    text = (
        f"🚀 <b>لینک دعوت اختصاصی شما:</b>\n\n"
        f"🔗 <code>{ref_link}</code>\n\n"
        f"💎 <b>پاداش دعوت:</b> هر کاربر <b>{referral_reward} TON</b>\n"
        f"👥 <b>تعداد دعوت‌های شما:</b> {prof['referrals_count']} / {max_referrals}"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == "👤 پروفایل من")
async def show_profile(message: types.Message):
    u_id = message.from_user.id
    if is_banned(u_id) or (!bot_active and not is_admin(u_id)):
        return
    prof = get_user_profile(u_id, message.from_user)
    text = (
        f"👤 <b>پروفایل کاربری شما:</b>\n"
        f"🆔 آیدی: <code>{u_id}</code>\n"
        f"💎 موجودی: <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 رفرال‌ها: {prof['referrals_count']} / {max_referrals}"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📊 آمار و راهنما")
async def show_help(message: types.Message):
    text = (
        f"ℹ️ <b>راهنمای ربات:</b>\n\n"
        f"1️⃣ با دعوت کاربران از لینک رفرال خود <b>{referral_reward} TON</b> دریافت کنید.\n"
        f"2️⃣ حداقل سقف برداشت <b>{min_withdraw_amount} TON</b> می‌باشد."
    )
    await message.answer(text, parse_mode="HTML")

# ==========================================
# Admin Panel & Channel Management
# ==========================================
@dp.message(F.text == "⚙️ پنل مدیریت ادمین 👑")
async def open_admin_panel(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    
    sys_balance, wallet_addr = await get_system_wallet_balance()
    wallet_str = f"<code>{sys_balance:.4f} TON</code>" if sys_balance is not None else f"⚠️ {wallet_addr}"

    admin_text = (
        f"👑 <b>پنل مدیریت ربات (v5.0.0)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>موجودی ولت اصلی:</b> {wallet_str}\n"
        f"🤖 <b>وضعیت ربات:</b> {'روشن ✅' if bot_active else 'خاموش 🛑'}\n"
        f"⚡️ <b>واریز خودکار:</b> {'فعال 🚀' if auto_payout_enabled else 'غیرفعال 📝'}\n"
        f"📢 <b>تعداد کانال‌های جوین اجباری:</b> {len(required_channels)}\n"
        f"👥 <b>کل کاربران:</b> <code>{len(user_data)}</code> نفر\n"
        f"🎯 <b>حداکثر رفرال مجاز:</b> <code>{max_referrals}</code>\n"
        f"🎁 <b>پاداش رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"🔻 <b>حداقل برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔝 <b>حداکثر برداشت:</b> <code>{max_withdraw_amount} TON</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_inline_keyboard())

# --- مدیریت کانال‌های جوین اجباری ---
@dp.callback_query(F.data == "admin_manage_channels")
async def manage_channels_menu(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    channels_str = "\n".join([f"• {ch}" for ch in required_channels]) if required_channels else "<i>هیچ کانالی تنظیم نشده است.</i>"
    text = f"📢 <b>کانال‌های جوین اجباری فعلی:</b>\n\n{channels_str}"
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ اضافه کردن کانال", callback_data="admin_add_channel")],
            [InlineKeyboardButton(text="➖ حذف کانال", callback_data="admin_remove_channel")],
            [InlineKeyboardButton(text="🔙 بازگشت به پنل", callback_data="admin_stats")]
        ]
    )
    await call.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data == "admin_add_channel")
async def start_add_channel(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminAddChannelForm.channel)
    await call.message.edit_text("➕ <b>یوزرنیم کانال جدید را وارد کنید (مثال: Voidchanneloffical@):</b>", parse_mode="HTML")

@dp.message(AdminAddChannelForm.channel)
async def process_add_channel(message: types.Message, state: FSMContext):
    ch = message.text.strip()
    if not ch.startswith("@"):
        ch = f"@{ch}"
    if ch not in required_channels:
        required_channels.append(ch)
        await save_data()
        await message.answer(f"✅ کانال <code>{ch}</code> با موفقیت اضافه شد!", parse_mode="HTML")
    else:
        await message.answer("⚠️ این کانال قبلاً اضافه شده است.")
    await state.clear()

@dp.callback_query(F.data == "admin_remove_channel")
async def start_remove_channel(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminRemoveChannelForm.channel)
    await call.message.edit_text("➖ <b>یوزرنیم کانالی که می‌خواهید حذف کنید را ارسال کنید:</b>", parse_mode="HTML")

@dp.message(AdminRemoveChannelForm.channel)
async def process_remove_channel(message: types.Message, state: FSMContext):
    ch = message.text.strip()
    if not ch.startswith("@"):
        ch = f"@{ch}"
    if ch in required_channels:
        required_channels.remove(ch)
        await save_data()
        await message.answer(f"✅ کانال <code>{ch}</code> حذف شد!", parse_mode="HTML")
    else:
        await message.answer("⚠️ کانال مورد نظر یافت نشد.")
    await state.clear()

# --- تنظیمات ادمین ---
@dp.callback_query(F.data == "admin_set_max_ref")
async def start_set_max_ref(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetConfigForm.value)
    await state.update_data(config_type="max_referrals")
    await call.message.edit_text(f"🎯 <b>حداکثر رفرال مجاز برای هر کاربر را وارد کنید (فعلی: {max_referrals}):</b>", parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_ref_reward")
async def start_set_ref_reward(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetConfigForm.value)
    await state.update_data(config_type="referral_reward")
    await call.message.edit_text(f"💎 <b>مقدار پاداش رفرال به TON را وارد کنید (فعلی: {referral_reward}):</b>", parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_min_wd")
async def start_set_min_wd(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetConfigForm.value)
    await state.update_data(config_type="min_withdraw")
    await call.message.edit_text(f"⚙️ <b>حداقل مقدار برداشت را وارد کنید (فعلی: {min_withdraw_amount}):</b>", parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_max_wd")
async def start_set_max_wd(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetConfigForm.value)
    await state.update_data(config_type="max_withdraw")
    await call.message.edit_text(f"🔝 <b>حداکثر مقدار برداشت را وارد کنید (فعلی: {max_withdraw_amount}):</b>", parse_mode="HTML")

@dp.callback_query(F.data == "admin_set_gas_fee")
async def start_set_gas_fee(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetConfigForm.value)
    await state.update_data(config_type="gas_fee")
    await call.message.edit_text(f"⛽️ <b>گس‌فی شبکه TON را وارد کنید (فعلی: {ton_gas_fee}):</b>", parse_mode="HTML")

@dp.message(AdminSetConfigForm.value)
async def process_set_config_val(message: types.Message, state: FSMContext):
    global max_referrals, referral_reward, min_withdraw_amount, max_withdraw_amount, ton_gas_fee
    data = await state.get_data()
    c_type = data.get("config_type")

    try:
        val = float(message.text.strip())
        if c_type == "max_referrals":
            max_referrals = int(val)
        elif c_type == "referral_reward":
            referral_reward = val
        elif c_type == "min_withdraw":
            min_withdraw_amount = val
        elif c_type == "max_withdraw":
            max_withdraw_amount = val
        elif c_type == "gas_fee":
            ton_gas_fee = val

        await save_data()
        await message.answer("✅ <b>تنظیمات با موفقیت بروزرسانی شد!</b>", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر وارد کنید!")
    await state.clear()

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot_callback(call: types.CallbackQuery):
    global bot_active
    if not is_admin(call.from_user.id):
        return
    bot_active = not bot_active
    await save_data()
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_toggle_auto_payout")
async def toggle_auto_payout_callback(call: types.CallbackQuery):
    global auto_payout_enabled
    if not is_admin(call.from_user.id):
        return
    auto_payout_enabled = not auto_payout_enabled
    await save_data()
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_stats")
async def show_stats_callback(call: types.CallbackQuery):
    await call.answer("🔄 به‌روزرسانی شد")
    if is_admin(call.from_user.id):
        await open_admin_panel(call.message)

# --- همه‌فرستی، پیام مستقیم و جستجو ---
@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminManageUserForm.amount)
    await call.message.edit_text("📢 <b>پیام همه‌فرستی را ارسال کنید:</b>", parse_mode="HTML")

@dp.callback_query(F.data == "admin_direct_msg")
async def start_direct_message(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminDirectMessageForm.user_id)
    await call.message.edit_text("💬 <b>آیدی عددی کاربر را بفرستید:</b>", parse_mode="HTML")

@dp.message(AdminDirectMessageForm.user_id)
async def process_direct_msg_user(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        await state.update_data(target_u_id=int(message.text))
        await state.set_state(AdminDirectMessageForm.message)
        await message.answer("📝 <b>پیام خود را جهت ارسال وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminDirectMessageForm.message)
async def process_direct_msg_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_id = data.get("target_u_id")
    await state.clear()
    try:
        await message.copy_to(chat_id=target_id)
        await message.answer(f"✅ پیام برای <code>{target_id}</code> ارسال شد!", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ خطا: {e}")

@dp.callback_query(F.data == "admin_search_user")
async def start_search_user(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSearchUserForm.user_id)
    await call.message.edit_text("🔍 <b>آیدی عددی کاربر را وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminSearchUserForm.user_id)
async def process_search_user(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        return
    target_id = int(message.text)
    await state.clear()

    if target_id not in user_data:
        await message.answer("❌ کاربر یافت نشد.")
        return

    target_prof = user_data[target_id]
    info_text = (
        f"👤 <b>اطلاعات کاربر <code>{target_id}</code>:</b>\n"
        f"💰 موجودی: <code>{target_prof.get('balance', 0.0):.4f} TON</code>\n"
        f"👥 تعداد رفرال: <code>{target_prof.get('referrals_count', 0)} / {max_referrals}</code>"
    )
    await message.answer(info_text, parse_mode="HTML")

@dp.callback_query(F.data == "admin_ban_user")
async def start_ban_user(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminBanUserForm.user_id)
    await call.message.edit_text("🚫 <b>آیدی عددی کاربر جهت بن را وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminBanUserForm.user_id)
async def process_ban_user(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        target_id = int(message.text)
        banned_users.add(target_id)
        await save_data()
        await message.answer(f"✅ کاربر <code>{target_id}</code> بن شد.", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "admin_unban_user")
async def start_unban_user(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminUnbanUserForm.user_id)
    await call.message.edit_text("🟢 <b>آیدی عددی کاربر جهت آن‌بن را وارد کنید:</b>", parse_mode="HTML")

@dp.message(AdminUnbanUserForm.user_id)
async def process_unban_user(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        target_id = int(message.text)
        if target_id in banned_users:
            banned_users.remove(target_id)
            await save_data()
            await message.answer(f"✅ کاربر <code>{target_id}</code> آن‌بن شد.", parse_mode="HTML")
    await state.clear()

@dp.callback_query(F.data == "admin_edit_balance")
async def start_edit_balance(call: types.CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminManageUserForm.user_id)
    await call.message.edit_text("👤 <b>آیدی عددی کاربر را بفرستید:</b>", parse_mode="HTML")

@dp.message(AdminManageUserForm.user_id)
async def process_edit_balance_user(message: types.Message, state: FSMContext):
    if message.text.isdigit():
        await state.update_data(target_u_id=int(message.text))
        await state.set_state(AdminManageUserForm.amount)
        await message.answer("💎 مقدار TON تغییر را وارد کنید (مثال: 0.5 یا -0.5):", parse_mode="HTML")

@dp.message(AdminManageUserForm.amount)
async def process_edit_balance_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.strip())
        data = await state.get_data()
        target_id = data.get("target_u_id")
        
        prof = get_user_profile(target_id)
        prof["balance"] = round(prof["balance"] + amount, 4)
        await save_data()
        await message.answer(f"✅ موجودی جدید: <code>{prof['balance']} TON</code>", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ عدد معتبر وارد کنید!")
    await state.clear()

# ==========================================
# Main Startup Function
# ==========================================
async def main():
    keep_alive()
    await load_data()
    
    # شروع تاسک پس‌زمینه بروزرسانی ۳ دقیقه‌ای ولت در کانال
    asyncio.create_task(update_wallet_balance_channel_task())
    
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
