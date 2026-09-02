# ==========================================
# Void Giveaway Bot - Version 4.1.1
# (Referral Only, Custom Gas Fee & Ref Reward Settings, Anti-Fake Ref, Advanced Admin)
# ==========================================

import asyncio
import os
import logging
import random
import html
import json
import re
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from pytoniq import LiteClient, WalletV5R1

logging.basicConfig(level=logging.INFO)
logging.getLogger("pytoniq").setLevel(logging.WARNING)
logging.getLogger("LiteClient").setLevel(logging.WARNING)

app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ Void Giveaway Bot (v4.1.1) is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6879499219]
WITHDRAW_CHANNEL = "@voidwithraw"
TON_MNEMONIC = os.environ.get("TON_MNEMONIC")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DATA_FILE = "giveaways.json"
active_giveaways = {}
user_data = {}
all_time_users = set()  # برای جلوگیری از باگ رفرال فیک (ذخیره دائمی user_id)
bot_active = True        # وضعیت روشن/خاموش بودن ربات
min_withdraw_amount = 0.1  # حداقل کفی برداشت TON
referral_reward = 0.048    # پاداش هر رفرال (قابل تنظیم از ادمین)
ton_gas_fee = 0.005        # مقدار گس‌فی شبکه TON (قابل تنظیم از ادمین)

# ==========================================
# ارسال پاداش شبکه TON
# ==========================================
async def send_ton_payout(destination_address: str, amount_ton: float):
    if not TON_MNEMONIC:
        return False, "کلید امنیتی ولت (TON_MNEMONIC) روی رندر تنظیم نشده است!"
    
    client = None
    try:
        client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)
        await client.connect()

        mnemonics = TON_MNEMONIC.strip().split()
        wallet = await WalletV5R1.from_mnemonic(client, mnemonics, network_global_id=-239)

        # تبدیل مبلغ نهایی ارسالی به نانوتون (NanoTON)
        amount_nano = int(amount_ton * 10**9)

        await wallet.transfer(
            destination=destination_address.strip(),
            amount=amount_nano,
            body="Payout from Void Giveaway Bot 🎉"
        )

        await client.close()
        return True, f"تراکنش با موفقیت انجام شد! (مبلغ واریزی: {amount_ton:.4f} TON) 🚀"

    except Exception as e:
        logging.error(f"pytoniq W5 Payout Error: {e}")
        if client and client.is_connected():
            await client.close()
        return False, str(e)

# ==========================================
# مدیریت ذخیره و بازیابی داده‌ها
# ==========================================
def save_data():
    serializable_gw = {}
    for msg_id, gw in active_giveaways.items():
        serializable_gw[str(msg_id)] = {
            "channel": gw["channel"],
            "title": gw["title"],
            "prize": gw["prize"],
            "winners_count": gw["winners_count"],
            "participants": {
                str(u_id): {
                    "username": info["username"],
                    "first_name": info["first_name"],
                    "referrals": info["referrals"]
                } for u_id, info in gw["participants"].items()
            },
            "end_time": gw["end_time"].isoformat(),
            "ended": gw["ended"]
        }
    
    serializable_users = {}
    for u_id, info in user_data.items():
        serializable_users[str(u_id)] = {
            "balance": info.get("balance", 0.0),
            "referrals_count": info.get("referrals_count", 0)
        }

    full_data = {
        "giveaways": serializable_gw,
        "users": serializable_users,
        "all_time_users": list(all_time_users),
        "bot_active": bot_active,
        "min_withdraw_amount": min_withdraw_amount,
        "referral_reward": referral_reward,
        "ton_gas_fee": ton_gas_fee
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

def load_data():
    global active_giveaways, user_data, all_time_users, bot_active, min_withdraw_amount, referral_reward, ton_gas_fee
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                full_data = json.load(f)
                gw_data = full_data.get("giveaways", {})
                for msg_id_str, gw in gw_data.items():
                    msg_id = int(msg_id_str)
                    participants = {}
                    for u_id_str, info in gw["participants"].items():
                        participants[int(u_id_str)] = {
                            "username": info.get("username"),
                            "first_name": info.get("first_name", "User"),
                            "referrals": info.get("referrals", 0)
                        }
                    active_giveaways[msg_id] = {
                        "channel": gw["channel"],
                        "title": gw["title"],
                        "prize": gw["prize"],
                        "winners_count": gw["winners_count"],
                        "participants": participants,
                        "end_time": datetime.fromisoformat(gw["end_time"]),
                        "ended": gw["ended"]
                    }
                
                u_data = full_data.get("users", {})
                for u_id_str, info in u_data.items():
                    u_id = int(u_id_str)
                    user_data[u_id] = {
                        "balance": round(info.get("balance", 0.0), 4),
                        "referrals_count": info.get("referrals_count", 0)
                    }
                
                all_time_users = set(full_data.get("all_time_users", []))
                bot_active = full_data.get("bot_active", True)
                min_withdraw_amount = full_data.get("min_withdraw_amount", 0.1)
                referral_reward = full_data.get("referral_reward", 0.048)
                ton_gas_fee = full_data.get("ton_gas_fee", 0.005)
        except Exception as e:
            logging.error(f"Error loading data: {e}")

# ==========================================
# FSM States
# ==========================================
class GiveawayForm(StatesGroup):
    title = State()
    prize = State()
    time_seconds = State()
    winners = State()
    channel = State()

class WithdrawForm(StatesGroup):
    amount = State()
    wallet_address = State()

class AdminBroadcastForm(StatesGroup):
    message = State()

class AdminManageUserForm(StatesGroup):
    user_id = State()
    amount = State()

class AdminSetMinWithdrawForm(StatesGroup):
    amount = State()

class AdminSetRefRewardForm(StatesGroup):
    amount = State()

class AdminSetGasFeeForm(StatesGroup):
    amount = State()

# ==========================================
# توابع كمكی و کیبوردها
# ==========================================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user_profile(user_id: int):
    if user_id not in user_data:
        user_data[user_id] = {
            "balance": 0.0,
            "referrals_count": 0
        }
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
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 ایجاد قرعه‌کشی جدید", callback_data="admin_new_gw"), InlineKeyboardButton(text="📊 لیست قرعه‌کشی‌ها", callback_data="admin_list_gw")],
            [InlineKeyboardButton(text="➕/➖ تغییر موجودی کاربر", callback_data="admin_edit_balance"), InlineKeyboardButton(text="⚙️ حداقل برداشت", callback_data="admin_set_min_wd")],
            [InlineKeyboardButton(text="💎 تنظیم پاداش رفرال", callback_data="admin_set_ref_reward"), InlineKeyboardButton(text="⛽️ تنظیم گس‌فی شبکه", callback_data="admin_set_gas_fee")],
            [InlineKeyboardButton(text=status_btn, callback_data="admin_toggle_bot"), InlineKeyboardButton(text="📢 همه‌فرستی (Broadcast)", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="🔄 بروزرسانی آمار", callback_data="admin_stats")]
        ]
    )

# ==========================================
# دستور /start و سیستم آنتی-فیک رفرال
# ==========================================
@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    await state.clear()
    u_id = message.from_user.id

    if not bot_active and not is_admin(u_id):
        await message.answer("🛑 <b>ربات در حال حاضر جهت به‌روزرسانی موقتاً خاموش می‌باشد.</b>", parse_mode="HTML")
        return

    prof = get_user_profile(u_id)
    args = command.args

    is_new_user = u_id not in all_time_users
    if is_new_user:
        all_time_users.add(u_id)

    if args and is_new_user:
        # ۱. رفرال مستقیم ربات
        if args.startswith("ref_"):
            try:
                referrer_id = int(args.replace("ref_", ""))
                if referrer_id != u_id and referrer_id in user_data:
                    ref_prof = get_user_profile(referrer_id)
                    ref_prof["balance"] = round(ref_prof["balance"] + referral_reward, 4)
                    ref_prof["referrals_count"] += 1
                    save_data()

                    try:
                        await bot.send_message(
                            referrer_id,
                            f"🎉 <b>یک کاربر جدید با لینک شما وارد ربات شد!</b>\n"
                            f"💎 <b>+{referral_reward} TON</b> مستقیماً به ولت شما اضافه شد!",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
            except Exception as e:
                logging.error(f"Direct Referral Error: {e}")

        # ۲. رفرال اختصاصی قرعه‌کشی‌ها
        elif args.startswith("gw_"):
            try:
                parts = args.split("_ref_")
                msg_id = int(parts[0].replace("gw_", ""))
                referrer_id = int(parts[1])

                if msg_id in active_giveaways and not active_giveaways[msg_id]["ended"]:
                    gw = active_giveaways[msg_id]
                    user = message.from_user

                    if user.id not in gw["participants"]:
                        gw["participants"][user.id] = {
                            "username": user.username,
                            "first_name": user.first_name,
                            "referrals": 0
                        }
                        if referrer_id in gw["participants"] and referrer_id != user.id:
                            gw["participants"][referrer_id]["referrals"] += 1
                            ref_prof = get_user_profile(referrer_id)
                            ref_prof["balance"] = round(ref_prof["balance"] + referral_reward, 4)
                            ref_prof["referrals_count"] += 1
                            try:
                                await bot.send_message(
                                    referrer_id,
                                    f"🔥 <b>یک نفر با لینکت وارد قرعه‌کشی {gw['title']} شد!</b>\n"
                                    f"🎉 <b>+۱ شانس اضافه</b> + <b>+{referral_reward} TON</b> به ولت شما اضافه شد!",
                                    parse_mode="HTML"
                                )
                            except Exception:
                                pass

                        save_data()
                        await update_post_text(gw["channel"], msg_id)
                        await message.answer(
                            f"👑 با موفقیت وارد قرعه‌کشی <b>{gw['title']}</b> شدی!",
                            parse_mode="HTML",
                            reply_markup=get_main_keyboard(u_id)
                        )
                        return
            except Exception as e:
                logging.error(f"GW Referral Start Error: {e}")

    save_data()
    await message.answer(
        f"⚡️ <b>به ربات Void Giveaway خوش آمدید!</b>\n"
        f"📌 <b>نسخه ربات:</b> <code>v4.1.1 (Auto Gas Deduction)</code> 💎\n\n"
        f"🎁 <b>به ازای هر رفرال معتبر {referral_reward} TON مستقیماً به کیف‌پول شما اضافه می‌شود!</b>\n\n"
        f"از منوی زیر جهت مدیریت موجودی، برداشت و دریافت لینک دعوت استفاده کنید 👇",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(u_id)
    )

# ==========================================
# سیستم کیف‌پول و برداشت مستقیم
# ==========================================
@dp.message(F.text == "💎 کیف‌پول من (Wallet)")
async def show_wallet(message: types.Message):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    
    text = (
        f"💎 <b>کیف‌پول کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>موجودی کل:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 <b>تعداد کل رفرال‌ها:</b> <code>{prof['referrals_count']}</code> نفر\n"
        f"🔹 <b>درآمد هر رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل کف برداشت:</b> <code>{min_withdraw_amount} TON</code>\n"
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
    await call.answer()
    u_id = call.from_user.id
    prof = get_user_profile(u_id)
    
    if prof["balance"] < min_withdraw_amount:
        await call.answer(
            f"❌ موجودی شما کافی نیست! حداقل مقدار برداشت {min_withdraw_amount} TON می‌باشد.",
            show_alert=True
        )
        return

    await state.set_state(WithdrawForm.amount)
    await call.message.answer(
        f"💰 <b>موجودی قابل برداشت شما:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n\n"
        f"لطفاً مقداری که قصد برداشت دارید را به عدد وارد کنید (مثال: {prof['balance']:.4f}):",
        parse_mode="HTML"
    )

# --- اصلاحیه اصلی: محاسبه دقیق گس‌فی و کسر از موجودی ---
@dp.message(WithdrawForm.amount)
async def process_withdraw_amount(message: types.Message, state: FSMContext):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    
    try:
        req_amount = float(message.text.strip())
    except ValueError:
        await message.answer("⚠️ لطفاً یک عدد معتبر وارد کنید!")
        return

    if req_amount < min_withdraw_amount:
        await message.answer(f"⚠️ حداقل مقدار برداشت <code>{min_withdraw_amount} TON</code> می‌باشد!")
        return

    if req_amount > prof["balance"]:
        await message.answer("⚠️ مقدار درخواستی بیشتر از موجودی ولت شما است!")
        return

    # منطق جدید محاسباتی:
    # ۱. اگر کاربر کسر گس‌فی از باقیمانده حسابش ممکن نباشه (مثلاً کل موجودی رو زده باشه)
    # گس‌فی از اصل مبلغ درخواستی کسر میشه.
    if (prof["balance"] - req_amount) < ton_gas_fee:
        amount_to_send = req_amount - ton_gas_fee
        deduct_from_balance = prof["balance"]  # کل موجودی صفر میشه
    else:
        # ۲. اگر بعد از برداشت، موجودی کافی برای پرداخت گس‌فی باقی بمونه
        amount_to_send = req_amount
        deduct_from_balance = req_amount + ton_gas_fee

    if amount_to_send <= 0:
        await message.answer(f"❌ مبلغ درخواستی پس از کسر گس‌فی ({ton_gas_fee} TON) نامعتبر است!")
        return

    await state.update_data(
        requested_amount=req_amount,
        amount_to_send=round(amount_to_send, 4),
        deduct_from_balance=round(deduct_from_balance, 4)
    )
    await state.set_state(WithdrawForm.wallet_address)
    
    await message.answer(
        f"📊 <b>جزئیات تراکنش شما:</b>\n"
        f"🔹 <b>مبلغ درخواستی:</b> <code>{req_amount:.4f} TON</code>\n"
        f"⛽️ <b>گس‌فی کسر شده:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🚀 <b>خالص دریافتی شما:</b> <code>{amount_to_send:.4f} TON</code>\n\n"
        f"📝 لطفاً <b>آدرس ولت TON (مانند EQ... یا UQ...)</b> خود را جهت واریز بفرستید:",
        parse_mode="HTML"
    )

@dp.message(WithdrawForm.wallet_address)
async def process_withdraw_address(message: types.Message, state: FSMContext):
    wallet_addr = message.text.strip()
    data = await state.get_data()
    
    amount_to_send = data.get("amount_to_send")
    deduct_from_balance = data.get("deduct_from_balance")
    
    user = message.from_user
    prof = get_user_profile(user.id)

    if deduct_from_balance > prof["balance"]:
        await message.answer("❌ خطا: موجودی حساب شما تغییر کرده است.")
        await state.clear()
        return

    # کسر دقیق کل مبلغ (اصل + گس‌فی) از حساب کاربر در ربات
    prof["balance"] = round(prof["balance"] - deduct_from_balance, 4)
    save_data()

    user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a>'
    
    withdraw_text = (
        f"🔔 <b>درخواست برداشت جدید TON!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>کاربر:</b> {user_mention} (ID: <code>{user.id}</code>)\n"
        f"💎 <b>خالص واریزی به ولت:</b> <code>{amount_to_send} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه:</b> <code>{ton_gas_fee} TON</code>\n"
        f"💰 <b>کل کسر شده از حساب:</b> <code>{deduct_from_balance} TON</code>\n\n"
        f"📝 <b>آدرس ولت:</b>\n<code>{html.escape(wallet_addr)}</code>\n\n"
        f"⏰ <b>زمان ثبت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    admin_action_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ واریز اتوماتیک TON", callback_data=f"wd_approve_{user.id}_{amount_to_send}_{deduct_from_balance}"),
                InlineKeyboardButton(text="❌ رد و بازگشت به ولت", callback_data=f"wd_reject_{user.id}_{deduct_from_balance}")
            ]
        ]
    )
    
    try:
        await bot.send_message(chat_id=WITHDRAW_CHANNEL, text=withdraw_text, parse_mode="HTML", reply_markup=admin_action_kb)
    except Exception as e:
        logging.error(f"Withdraw channel send error: {e}")
        # بازگشت وجه در صورت خطا
        prof["balance"] = round(prof["balance"] + deduct_from_balance, 4)
        save_data()
        await message.answer("❌ خطایی در ارسال درخواست به کانال پشتیبانی رخ داد.")
        await state.clear()
        return

    await state.clear()
    await message.answer(
        f"✅ <b>درخواست برداشت با موفقیت ثبت شد!</b>\n\n"
        f"🚀 <b>مبلغ خالص واریزی:</b> <code>{amount_to_send} TON</code>\n"
        f"اطلاعات به کانال پشتیبانی ارسال شد و به زودی پردازش می‌گردد 🔥",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user.id)
    )

# ==========================================
# تایید یا رد برداشت توسط ادمین
# ==========================================
@dp.callback_query(F.data.startswith("wd_approve_"))
async def approve_withdraw(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    parts = call.data.split("_")
    target_user_id = int(parts[2])
    amount_to_send = float(parts[3])

    message_text = call.message.text or call.message.caption or ""
    match = re.search(r'(EQ[a-zA-Z0-9_-]{46}|UQ[a-zA-Z0-9_-]{46})', message_text)
    
    if not match:
        await call.answer("❌ آدرس ولت معتبری در متن پیام یافت نشد!", show_alert=True)
        return

    dest_addr = match.group(1)
    await call.answer("⏳ در حال ارسال تراکنش به شبکه TON...", show_alert=False)

    success, result_msg = await send_ton_payout(dest_addr, amount_to_send)
    if success:
        updated_text = call.message.text + f"\n\n✅ <b>وضعیت: واریز {amount_to_send} TON با موفقیت انجام شد! 💎</b>"
        await call.message.edit_text(updated_text, parse_mode="HTML", reply_markup=None)
        await call.answer(f"✅ {amount_to_send} TON با موفقیت منتقل شد!", show_alert=True)
        try:
            await bot.send_message(
                target_user_id,
                f"🎉 <b>درخواست برداشت شما تایید و واریز شد!</b>\n\n🎁 مقدار <b>{amount_to_send} TON</b> به ولت شما منتقل گردید.",
                parse_mode="HTML"
            )
        except Exception:
            pass
    else:
        await call.answer(f"❌ خطا در واریز: {result_msg}", show_alert=True)

@dp.callback_query(F.data.startswith("wd_reject_"))
async def reject_withdraw(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    parts = call.data.split("_")
    target_user_id = int(parts[2])
    deduct_from_balance = float(parts[3])

    prof = get_user_profile(target_user_id)
    prof["balance"] = round(prof["balance"] + deduct_from_balance, 4)
    save_data()

    updated_text = call.message.text + "\n\n❌ <b>وضعیت: رد شد (مبلغ به کیف‌پول کاربر بازگشت داده شد)</b>"
    await call.message.edit_text(updated_text, parse_mode="HTML", reply_markup=None)
    await call.answer("❌ درخواست رد شد.", show_alert=True)

    try:
        await bot.send_message(
            target_user_id,
            f"❌ <b>درخواست برداشت رد شد!</b>\n\nمبلغ <code>{deduct_from_balance} TON</code> مجدداً به موجودی کیف‌پول شما در ربات بازگشت داده شد.",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ==========================================
# دریافت لینک رفرال
# ==========================================
@dp.message(F.text == "🔗 دریافت لینک رفرال 🚀")
async def send_referral_link_menu(message: types.Message):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    bot_info = await bot.get_me()
    
    general_ref_link = f"https://t.me/{bot_info.username}?start=ref_{u_id}"
    
    text = (
        f"🚀 <b>لینک دعوت اختصاصی شما:</b>\n\n"
        f"🔗 <code>{general_ref_link}</code>\n\n"
        f"💎 <b>پاداش دعوت:</b> به ازای هر کاربر جدید <b>{referral_reward} TON</b>\n"
        f"👥 <b>مجموع دعوت‌های معتبر شما:</b> {prof['referrals_count']} نفر\n\n"
        f"📌 لینک را برای دوستان خود بفرستید تا بلافاصله تونکوین کسب کنید!"
    )
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == "👤 پروفایل من")
async def show_profile(message: types.Message):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    
    text = (
        f"👤 <b>پروفایل کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>آیدی عددی:</b> <code>{u_id}</code>\n"
        f"💎 <b>موجودی ولت:</b> <code>{prof['balance']:.4f} TON</code>\n"
        f"👥 <b>تعداد رفرال‌ها:</b> {prof['referrals_count']} نفر\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text == "📊 آمار و راهنما")
async def show_help(message: types.Message):
    text = (
        f"ℹ️ <b>راهنمای ربات Void Giveaway:</b>\n\n"
        f"1️⃣ با دعوت هر کاربر جدید از طریق لینک رفرال خود <b>{referral_reward} TON</b> پاداش دریافت می‌کنید.\n"
        f"2️⃣ پاداش‌ها مستقیماً وارد «کیف‌پول من» می‌شوند.\n"
        f"3️⃣ پس از رسیدن به حداقل کف برداشت (<code>{min_withdraw_amount} TON</code>) می‌توانید درخواست واریز ثبت کنید.\n"
        f"4️⃣ همچنین می‌توانید در قرعه‌کشی‌های کانال شرکت کنید."
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
    active_gw = sum(1 for gw in active_giveaways.values() if not gw["ended"])
    total_balance = sum(u.get("balance", 0.0) for u in user_data.values())
    
    admin_text = (
        "👑 <b>داشبورد مدیریت ربات Void Giveaway (v4.1.1)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 <b>وضعیت ربات:</b> {'روشن ✅' if bot_active else 'خاموش/تعمیرات 🛑'}\n"
        f"👥 <b>کاربران فعال فعلی:</b> <code>{total_users}</code> نفر\n"
        f"📜 <b>کل کاربران تاریخی:</b> <code>{total_all_time}</code> نفر\n"
        f"💎 <b>مجموع موجودی ولت کاربران:</b> <code>{total_balance:.4f} TON</code>\n"
        f"🎁 <b>پاداش هر رفرال:</b> <code>{referral_reward} TON</code>\n"
        f"⛽️ <b>گس‌فی شبکه TON:</b> <code>{ton_gas_fee} TON</code>\n"
        f"🔻 <b>حداقل کف برداشت فعلی:</b> <code>{min_withdraw_amount} TON</code>\n"
        f"🔥 <b>قرعه‌کشی‌های فعال:</b> <code>{active_gw}</code> عدد\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "جهت مدیریت از دکمه‌های زیر استفاده کنید:"
    )
    
    await message.answer(admin_text, parse_mode="HTML", reply_markup=get_admin_inline_keyboard())

@dp.callback_query(F.data == "admin_toggle_bot")
async def toggle_bot_callback(call: types.CallbackQuery):
    global bot_active
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    
    bot_active = not bot_active
    save_data()
    status_msg = "🛑 ربات خاموش شد." if not bot_active else "✅ ربات روشن شد."
    await call.answer(status_msg, show_alert=True)
    await open_admin_panel(call.message)

@dp.callback_query(F.data == "admin_stats")
async def show_stats_callback(call: types.CallbackQuery):
    await call.answer("🔄 اطلاعات به‌روزرسانی شد", show_alert=False)
    if not is_admin(call.from_user.id):
        return
    await open_admin_panel(call.message)

# --- تنظیم حداقل برداشت ---
@dp.callback_query(F.data == "admin_set_min_wd")
async def start_set_min_wd(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSetMinWithdrawForm.amount)
    await call.message.edit_text("⚙️ <b>حداقل مقدار جدید برای برداشت TON را وارد کنید (مثال: 0.2):</b>", parse_mode="HTML")

@dp.message(AdminSetMinWithdrawForm.amount)
async def process_set_min_wd(message: types.Message, state: FSMContext):
    global min_withdraw_amount
    try:
        amount = float(message.text.strip())
        min_withdraw_amount = amount
        save_data()
        await state.clear()
        await message.answer(f"✅ حداقل کف برداشت به <code>{min_withdraw_amount} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

# --- تنظیم پاداش هر رفرال ---
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
        save_data()
        await state.clear()
        await message.answer(f"✅ پاداش هر رفرال با موفقیت به <code>{referral_reward} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

# --- تنظیم گس‌فی شبکه TON ---
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
        save_data()
        await state.clear()
        await message.answer(f"✅ گس‌فی شبکه TON به <code>{ton_gas_fee} TON</code> تغییر یافت!", parse_mode="HTML")
    except ValueError:
        await message.answer("⚠️ لطفاً عدد معتبر وارد کنید!")

# --- تغییر موجودی کاربر ---
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
    save_data()
    await state.clear()

    await message.answer(
        f"✅ موجودی کاربر <code>{target_id}</code> به‌روزرسانی شد.\n💰 موجودی جدید: <code>{prof['balance']} TON</code>",
        parse_mode="HTML"
    )

# --- همه‌فرستی ---
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
# سیستم قرعه‌کشی‌ها
# ==========================================
@dp.callback_query(F.data == "admin_list_gw")
async def show_active_giveaways_callback(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return

    active_items = {k: v for k, v in active_giveaways.items() if not v["ended"]}
    if not active_items:
        await call.message.edit_text("📊 <b>قرعه‌کشی فعالی وجود ندارد.</b>", parse_mode="HTML", reply_markup=get_admin_inline_keyboard())
        return

    for msg_id, gw in active_items.items():
        remaining = max(0, int((gw["end_time"] - datetime.now()).total_seconds()))
        mins, secs = divmod(remaining, 60)
        time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
        
        info_text = (
            f"⚡️ <b>قرعه‌کشی فعال:</b>\n"
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n"
            f"📢 <b>کانال:</b> {gw['channel']}\n"
            f"👥 <b>شرکت‌کنندگان:</b> {len(gw['participants'])} نفر\n"
            f"⏱ <b>تایمر معکوس:</b> {time_str} ⏳\n"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🛑 اتمام فوری و اعلام برندگان 🏆", callback_data=f"stop_gw_{msg_id}")]]
        )
        await call.message.answer(info_text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("stop_gw_"))
async def stop_giveaway_manual(call: types.CallbackQuery):
    await call.answer()
    if not is_admin(call.from_user.id):
        return

    msg_id = int(call.data.split("_")[2])
    if msg_id in active_giveaways and not active_giveaways[msg_id]["ended"]:
        await finish_giveaway(active_giveaways[msg_id]["channel"], msg_id)
        await call.message.edit_text("💥 قرعه‌کشی اتمام یافت.", parse_mode="HTML")

@dp.callback_query(F.data == "admin_new_gw")
async def start_giveaway_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not is_admin(call.from_user.id):
        return
    await state.set_state(GiveawayForm.title)
    await call.message.edit_text("📌 <b>عنوان قرعه‌کشی را وارد کنید:</b>", parse_mode="HTML")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.prize)
    await message.answer(f"✅ عنوان: <b>{html.escape(message.text)}</b>\n\n🎁 <b>نوع جایزه را وارد کنید:</b>", parse_mode="HTML")

@dp.message(GiveawayForm.prize)
async def process_prize(message: types.Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(GiveawayForm.time_seconds)
    time_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ ۱ دقیقه", callback_data="time_60"), InlineKeyboardButton(text="⏱ ۵ دقیقه", callback_data="time_300")],
            [InlineKeyboardButton(text="⏱ ۱۰ دقیقه", callback_data="time_600"), InlineKeyboardButton(text="⏱ ۱ ساعت", callback_data="time_3600")]
        ]
    )
    await message.answer(f"✅ جایزه: <b>{html.escape(message.text)}</b>\n\n⏳ <b>مدت زمان را انتخاب کنید:</b>", parse_mode="HTML", reply_markup=time_keyboard)

@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text("👥 <b>تعداد برندگان را وارد کنید:</b>", parse_mode="HTML")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ عدد وارد کنید!")
        return
    await state.update_data(winners=int(message.text))
    await state.set_state(GiveawayForm.channel)
    await message.answer("📢 <b>آیدی کانال را بفرستید (مثال: @Voidchanneloffical):</b>", parse_mode="HTML")

@dp.message(GiveawayForm.channel)
async def process_channel(message: types.Message, state: FSMContext):
    raw_channel = message.text.strip()
    if "t.me/" in raw_channel:
        raw_channel = raw_channel.split("t.me/")[-1].replace("/", "")
    channel_id = raw_channel if raw_channel.startswith("@") else "@" + raw_channel

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(chat_id=chat.id, user_id=bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer("❌ ربات در این کانال ادمین نیست!", parse_mode="HTML")
            return
    except Exception:
        await message.answer("❌ کانال پیدا نشد یا ربات دسترسی ندارد!", parse_mode="HTML")
        return

    await state.update_data(channel=channel_id)
    data = await state.get_data()
    
    confirm_text = (
        "🚀 <b>تایید انتشار قرعه‌کشی:</b>\n\n"
        f"📌 <b>عنوان:</b> {html.escape(data['title'])}\n"
        f"🎁 <b>جایزه:</b> {html.escape(data['prize'])}\n"
        f"⏳ <b>زمان:</b> {data['time_seconds']} ثانیه\n"
        f"🏆 <b>تعداد برنده:</b> {data['winners']} نفر\n"
        f"📢 <b>کانال:</b> {data['channel']}\n"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار در کانال", callback_data="confirm_launch")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="cancel_launch")]
        ]
    )
    await message.answer(confirm_text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data == "confirm_launch")
async def launch_giveaway(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    channel_id = data['channel']
    total_seconds = data['time_seconds']
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    
    title = html.escape(data['title'])
    prize = html.escape(data['prize'])
    
    giveaway_text = (
        f"🔥 <b>{title}</b> 🔥\n"
        f"✨ ─── GIVEAWAY ─── ✨\n\n"
        f"🎁 <b>جایزه ویژه:</b> {prize}\n"
        f"🏆 <b>تعداد برندگان:</b> {data['winners']} نفر\n"
        f"⏱ <b>زمان باقی‌مانده:</b> {total_seconds} ثانیه ⏳\n\n"
        f"👥 <b>شرکت‌کنندگان (0):</b>\n<i>هنوز کسی شرکت نکرده است.</i> 🚀\n\n"
        f"👇 جهت شرکت از دکمه زیر استفاده کنید:"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 لینک دعوت (افزایش شانس + TON)", callback_data="get_ref")]
        ]
    )
    
    try:
        sent_msg = await bot.send_message(chat_id=channel_id, text=giveaway_text, parse_mode="HTML", reply_markup=channel_keyboard)
    except Exception as e:
        await call.message.edit_text(f"❌ خطای ارسال به کانال: {e}")
        return

    active_giveaways[sent_msg.message_id] = {
        "channel": channel_id,
        "title": title,
        "prize": prize,
        "winners_count": data['winners'],
        "participants": {},
        "end_time": end_time,
        "ended": False
    }
    save_data()
    await call.message.edit_text(f"💥 قرعه‌کشی منتشر شد!", parse_mode="HTML")
    await state.clear()
    asyncio.create_task(run_giveaway_timer(sent_msg.chat.id, sent_msg.message_id))

@dp.callback_query(F.data == "join_gw")
async def join_giveaway(call: types.CallbackQuery):
    await call.answer()
    msg_id = call.message.message_id
    user = call.from_user
    
    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("🛑 مهلت قرعه‌کشی تمام شده است!", show_alert=True)
        return
        
    gw = active_giveaways[msg_id]
    try:
        member = await bot.get_chat_member(chat_id=gw["channel"], user_id=user.id)
        if member.status in ["left", "kicked"]:
            await call.answer("❌ ابتدا باید عضو کانال شوید!", show_alert=True)
            return
    except Exception:
        pass

    if user.id in gw["participants"]:
        await call.answer("💎 شما قبلاً ثبت‌نام کرده‌اید!", show_alert=True)
        return

    gw["participants"][user.id] = {
        "username": user.username,
        "first_name": user.first_name,
        "referrals": 0
    }
    save_data()
    await call.answer("🎉 با موفقیت ثبت‌نام شدید!", show_alert=True)
    await update_post_text(call.message.chat.id, msg_id)

@dp.callback_query(F.data == "get_ref")
async def get_referral_link(call: types.CallbackQuery):
    await call.answer()
    msg_id = call.message.message_id
    user = call.from_user

    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("🛑 قرعه‌کشی تمام شده است!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=gw_{msg_id}_ref_{user.id}"
    gw = active_giveaways[msg_id]

    try:
        await bot.send_message(
            chat_id=user.id,
            text=(
                f"👑 <b>لینک اختصاصی دعوت شما:</b>\n"
                f"📌 <b>قرعه‌کشی:</b> {gw['title']}\n\n"
                f"🔗 <code>{ref_link}</code>\n\n"
                f"✨ با دعوت هر کاربر: <b>+۱ شانس اضافه</b> در قرعه‌کشی + <b>{referral_reward} TON</b> هدیه دریافت می‌کنید!"
            ),
            parse_mode="HTML"
        )
        await call.answer("📩 لینک به پیوی ارسال شد!", show_alert=True)
    except Exception:
        await call.answer("⚠️ ابتدا ربات را در پیوی استارت کنید!", show_alert=True)

async def update_post_text(chat_id, message_id):
    if message_id not in active_giveaways:
        return
        
    gw = active_giveaways[message_id]
    remaining = max(0, int((gw["end_time"] - datetime.now()).total_seconds()))
    mins, secs = divmod(remaining, 60)
    time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
    
    participants_dict = gw["participants"]
    if not participants_dict:
        users_str = "<i>هنوز کسی شرکت نکرده است.</i> 🚀"
    else:
        formatted_users = []
        for idx, (u_id, u_info) in enumerate(participants_dict.items(), 1):
            refs = u_info.get("referrals", 0)
            ref_badge = f" (💥 +{refs} شانس)" if refs > 0 else ""
            name = f"@{u_info['username']}" if u_info["username"] else html.escape(u_info["first_name"])
            formatted_users.append(f"<b>{idx}.</b> {name}{ref_badge}")
        users_str = "\n".join(formatted_users)
    
    count = len(participants_dict)
    updated_text = (
        f"🔥 <b>{gw['title']}</b> 🔥\n"
        f"✨ ─── GIVEAWAY ─── ✨\n\n"
        f"🎁 <b>جایزه ویژه:</b> {gw['prize']}\n"
        f"🏆 <b>تعداد برندگان:</b> {gw['winners_count']} نفر\n"
        f"⏱ <b>زمان باقی‌مانده:</b> {time_str} ⏳\n\n"
        f"👥 <b>شرکت‌کنندگان ({count}):</b>\n{users_str}\n\n"
        f"👇 جهت شرکت از دکمه‌های زیر استفاده کنید:"
    )
    
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count}) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 لینک دعوت (افزایش شانس + TON)", callback_data="get_ref")]
        ]
    )
    
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=updated_text, parse_mode="HTML", reply_markup=channel_kb)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logging.error(f"General Edit Error: {e}")

async def finish_giveaway(chat_id, message_id):
    if message_id not in active_giveaways or active_giveaways[message_id]["ended"]:
        return

    gw = active_giveaways[message_id]
    gw["ended"] = True
    participants = gw["participants"]
    winners_count = gw["winners_count"]
    
    if not participants:
        final_text = (
            f"🏁 <b>قرعه‌کشی به پایان رسید!</b>\n\n"
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n\n"
            f"❌ هیچ شرکت‌کننده‌ای ثبت‌نام نکرد."
        )
    else:
        pool = []
        for u_id, u_info in participants.items():
            entries = 1 + u_info.get("referrals", 0)
            pool.extend([u_id] * entries)
            
        unique_winners = set()
        while len(unique_winners) < min(winners_count, len(participants)):
            chosen_id = random.choice(pool)
            unique_winners.add(chosen_id)
            
        winners_list = []
        for u_id in unique_winners:
            u_info = participants[u_id]
            name = f"🏆 @{u_info['username']}" if u_info["username"] else f'🏆 <a href="tg://user?id={u_id}">{html.escape(u_info["first_name"])}</a>'
            winners_list.append(name)
        
        winners_str = "\n".join(winners_list)
        final_text = (
            f"👑 <b>قرعه‌کشی به پایان رسید!</b> 👑\n\n"
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n\n"
            f"🎉 <b>برندگان خوش‌شانس:</b>\n{winners_str}\n"
        )
    
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, parse_mode="HTML", reply_markup=None)
    except Exception as e:
        logging.error(f"Error updating winner message: {e}")
    
    save_data()

async def run_giveaway_timer(chat_id, message_id):
    while message_id in active_giveaways:
        gw = active_giveaways[message_id]
        if gw["ended"]:
            break
            
        remaining = (gw["end_time"] - datetime.now()).total_seconds()
        if remaining <= 0:
            await finish_giveaway(chat_id, message_id)
            break
        else:
            await update_post_text(chat_id, message_id)
            
        await asyncio.sleep(10)

@dp.callback_query(F.data == "cancel_launch")
async def cancel_launch(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await call.message.edit_text("❌ لغو شد.", parse_mode="HTML")

async def main():
    load_data()
    for msg_id, gw in list(active_giveaways.items()):
        if not gw["ended"]:
            asyncio.create_task(run_giveaway_timer(gw["channel"], msg_id))

    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
