# ==========================================
# Void Giveaway Bot - Version 1.6.0
# (Real TON Network Payout Enabled)
# ==========================================

import asyncio
import os
import logging
import random
import html
import json
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

from tonsdk.contract.wallet import WalletVersionEnum, Wallets
from tonsdk.utils import bytes_to_b64str
from tonsdk.provider import ToncenterClient

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ Void Giveaway Bot (v1.6.0) is running!"

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
wheel_active = True

WHEEL_SKINS = [
    {"name": "0.01 TON 💎", "type": "ton", "weight": 90, "amount": 0.01},
    {"name": "Common Skin #1 🛡", "type": "skin", "weight": 2},
    {"name": "Common Skin #2 ⚔️", "type": "skin", "weight": 2},
    {"name": "Common Skin #3 🔫", "type": "skin", "weight": 2},
    {"name": "Common Skin #4 🏹", "type": "skin", "weight": 2},
    {"name": "Rare Skin 🔥👑", "type": "skin", "weight": 2}
]

# تابع واریز واقعی و ارسال به شبکه TON
async def send_ton_payout(destination_address: str, amount_ton: float):
    if not TON_MNEMONIC:
        return False, "کلید امنیتی ولت (TON_MNEMONIC) روی رندر تنظیم نشده است!"
    
    try:
        mnemonics = TON_MNEMONIC.strip().split()
        
        # ۱. ساخت ولت
        wallet, public_key, private_key, wallet_state = Wallets.from_mnemonics(
            mnemonics=mnemonics,
            version=WalletVersionEnum.v4r2,
            workchain=0
        )
        
        # ۲. اتصال به سرور Toncenter
        client = ToncenterClient(base_url='https://toncenter.com/api/v2/jsonRPC')
        
        # ۳. دریافت شماره Seqno از شبکه
        seqno = await asyncio.to_thread(client.wallet_seqno, wallet.address.to_string(True, True, True))
        if seqno is None:
            seqno = 0

        # ۴. ساخت تراکنش و کسر مبلغ (تبدیل به NanoTON)
        nano_amount = int(amount_ton * 10**9)
        query = wallet.create_transfer_message(
            to_addr=destination_address.strip(),
            amount=nano_amount,
            seqno=seqno,
            payload="Reward from Void Giveaway Bot 🎉"
        )
        
        # ۵. ارسال بایت‌های تراکنش امضا شده به بلاک‌چین TON
        boc_b64 = bytes_to_b64str(query['message'].to_boc(False))
        res = await asyncio.to_thread(client.send_boc, boc_b64)
        
        if res and res.get('@type') == 'ok':
            return True, "تراکنش با موفقیت به شبکه TON ارسال شد و کسر گردید! 🚀"
        else:
            return False, f"خطای شبکه TON: {res}"

    except Exception as e:
        logging.error(f"TON Payout Real Send Error: {e}")
        return False, str(e)

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
            "inventory": info.get("inventory", []),
            "last_wheel": info.get("last_wheel").isoformat() if info.get("last_wheel") else None
        }

    full_data = {
        "giveaways": serializable_gw,
        "users": serializable_users,
        "wheel_active": wheel_active
    }
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(full_data, f, ensure_ascii=False, indent=2)

def load_data():
    global active_giveaways, user_data, wheel_active
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
                    last_w = datetime.fromisoformat(info["last_wheel"]) if info.get("last_wheel") else None
                    user_data[u_id] = {
                        "inventory": info.get("inventory", []),
                        "last_wheel": last_w
                    }
                wheel_active = full_data.get("wheel_active", True)
        except Exception as e:
            logging.error(f"Error loading data: {e}")

class GiveawayForm(StatesGroup):
    title = State()
    prize = State()
    time_seconds = State()
    winners = State()
    channel = State()

class WithdrawForm(StatesGroup):
    username_info = State()

def get_main_keyboard(user_id: int):
    kb = [
        [KeyboardButton(text="🎡 گردونه شانس (۴۸ ساعته) ⚡️")],
        [KeyboardButton(text="🎒 انبار من (Inventory)"), KeyboardButton(text="👤 پروفایل من")]
    ]
    if is_admin(user_id):
        kb.insert(0, [KeyboardButton(text="🎁 استارت قرعه‌کشی جدید 🚀"), KeyboardButton(text="📊 لیست قرعه‌کشی‌ها ⚡️")])
        wheel_status_btn = "🛑 متوقف کردن گردونه" if wheel_active else "✅ فعال‌سازی گردونه"
        kb.append([KeyboardButton(text=wheel_status_btn)])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_user_profile(user_id: int):
    if user_id not in user_data:
        user_data[user_id] = {"inventory": [], "last_wheel": None}
    return user_data[user_id]

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    await state.clear()
    u_id = message.from_user.id
    get_user_profile(u_id)
    args = command.args

    if args and args.startswith("gw_"):
        try:
            parts = args.split("_ref_")
            msg_id = int(parts[0].replace("gw_", ""))
            referrer_id = int(parts[1])

            if msg_id in active_giveaways and not active_giveaways[msg_id]["ended"]:
                gw = active_giveaways[msg_id]
                user = message.from_user

                try:
                    member = await bot.get_chat_member(chat_id=gw["channel"], user_id=user.id)
                    if member.status in ["left", "kicked"]:
                        await message.answer(f"⚠️ برای ورود باید عضو کانال {gw['channel']} باشید!", parse_mode="HTML")
                        return
                except Exception:
                    pass

                if user.id not in gw["participants"]:
                    gw["participants"][user.id] = {
                        "username": user.username,
                        "first_name": user.first_name,
                        "referrals": 0
                    }
                    if referrer_id in gw["participants"] and referrer_id != user.id:
                        gw["participants"][referrer_id]["referrals"] += 1
                        try:
                            await bot.send_message(referrer_id, f"🔥 <b>یک نفر با لینکت وارد شد!</b>\n🎉 <b>+۱ شانس اضافه</b> برای قرعه‌کشی {gw['title']} ثبت شد!", parse_mode="HTML")
                        except Exception:
                            pass

                    save_data()
                    await update_post_text(gw["channel"], msg_id)
                    await message.answer(f"👑 با موفقیت وارد قرعه‌کشی <b>{gw['title']}</b> شدی!", parse_mode="HTML", reply_markup=get_main_keyboard(u_id))
                    return
                else:
                    await message.answer("💎 قبلاً ثبت‌نام کردی رفیق!", reply_markup=get_main_keyboard(u_id))
                    return
        except Exception as e:
            logging.error(f"Referral Start Error: {e}")

    await message.answer(
        f"⚡️ <b>به ربات بزرگ Void Giveaway خوش آمدی!</b>\n"
        f"📌 <b>نسخه ربات:</b> <code>v1.6.0</code> 💎\n\n"
        f"از منوی زیر می‌تونی توی گردونه شانس شرکت کنی یا انبار اسکینهات رو ببینی 👇",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(u_id)
    )

@dp.message(F.text.in_(["🛑 متوقف کردن گردونه", "✅ فعال‌سازی گردونه"]))
async def toggle_wheel(message: types.Message):
    global wheel_active
    if not is_admin(message.from_user.id):
        return
    
    wheel_active = not wheel_active
    save_data()
    status_msg = "🛑 گردونه شانس با موفقیت متوقف شد." if not wheel_active else "✅ گردونه شانس با موفقیت فعال شد."
    await message.answer(status_msg, reply_markup=get_main_keyboard(message.from_user.id))

@dp.message(F.text == "🎡 گردونه شانس (۴۸ ساعته) ⚡️")
async def spin_wheel_start(message: types.Message, state: FSMContext):
    await state.clear()
    u_id = message.from_user.id
    prof = get_user_profile(u_id)

    if not wheel_active:
        await message.answer("🛑 <b>گردونه شانس در حال حاضر توسط ادمین متوقف شده است.</b>", parse_mode="HTML")
        return

    now = datetime.now()
    if prof["last_wheel"]:
        next_spin = prof["last_wheel"] + timedelta(hours=48)
        if now < next_spin:
            diff = next_spin - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            await message.answer(
                f"⏳ <b>هنوز زمان چرخاندن مجدد نرسیده رفیق!</b>\n\n"
                f"⏱ <b>زمان باقی‌مانده:</b> {hours} ساعت و {minutes} دقیقه",
                parse_mode="HTML"
            )
            return

    prof["last_wheel"] = now
    
    prizes = [s["name"] for s in WHEEL_SKINS]
    weights = [s["weight"] for s in WHEEL_SKINS]
    won_prize = random.choices(prizes, weights=weights, k=1)[0]
    
    await message.answer("🎡 <b>در حال چرخاندن گردونه شانس...</b> 🎰", parse_mode="HTML")
    await asyncio.sleep(2)

    await state.update_data(pending_skin=won_prize)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📤 برداشت فوری", callback_data="claim_now")],
            [InlineKeyboardButton(text="🎒 ذخیره در انبار (Inventory)", callback_data="claim_store")]
        ]
    )
    
    await message.answer(
        f"🎉 <b>تبریک! شما برنده جایزه زیر شدید:</b>\n\n"
        f"🎁 <b>{won_prize}</b>\n\n"
        f"حالا می‌خوای چکار کنی؟ انتخاب کن 👇",
        parse_mode="HTML",
        reply_markup=kb
    )
    save_data()

@dp.callback_query(F.data == "claim_store")
async def claim_store_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skin = data.get("pending_skin")
    u_id = call.from_user.id
    
    if not skin:
        await call.answer("❌ خطایی رخ داد.", show_alert=True)
        return
        
    prof = get_user_profile(u_id)
    prof["inventory"].append(skin)
    save_data()
    await state.clear()
    
    await call.message.edit_text(
        f"✅ <b>جایزه {skin} با موفقیت در انبار (Inventory) ذخیره شد!</b>\n"
        f"هر زمان خواستی می‌تونی از منوی انبار درخواست برداشت بدی 🔥",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "claim_now")
async def claim_now_handler(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    skin = data.get("pending_skin")
    if not skin:
        await call.answer("❌ خطایی رخ داد.", show_alert=True)
        return
        
    await state.set_state(WithdrawForm.username_info)
    await state.update_data(withdraw_skin=skin, from_inventory_index=None)
    
    hint_text = "آدرس ولت TON (مثل EQ... یا UQ...)" if "TON" in skin else "آیدی تلگرام / یوزرنیم گیم"
    
    await call.message.edit_text(
        f"📤 <b>درخواست برداشت:</b> {skin}\n\n"
        f"لطفاً <b>{hint_text}</b> رو ارسال کن:",
        parse_mode="HTML"
    )

@dp.message(WithdrawForm.username_info)
async def process_withdraw_info(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    data = await state.get_data()
    skin = data.get("withdraw_skin")
    inv_index = data.get("from_inventory_index")
    user = message.from_user

    user_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.first_name)}</a>'
    
    withdraw_text = (
        f"🔔 <b>درخواست برداشت جدید!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>کاربر:</b> {user_mention} (ID: <code>{user.id}</code>)\n"
        f"🎁 <b>جایزه:</b> {skin}\n\n"
        f"📝 <b>مشخصات/آدرس ارسالی:</b>\n<code>{html.escape(user_input)}</code>\n\n"
        f"⏰ <b>زمان ثبت:</b> {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    admin_action_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ واریز شد (تایید)", callback_data=f"wd_approve_{user.id}_{html.escape(skin)}"),
                InlineKeyboardButton(text="❌ رد شد (فیک)", callback_data=f"wd_reject_{user.id}_{html.escape(skin)}")
            ]
        ]
    )
    
    try:
        await bot.send_message(chat_id=WITHDRAW_CHANNEL, text=withdraw_text, parse_mode="HTML", reply_markup=admin_action_kb)
    except Exception as e:
        logging.error(f"Withdraw channel send error: {e}")
        await message.answer("❌ خطایی در ارسال درخواست به کانال ادمین رخ داد.")
        return

    if inv_index is not None:
        prof = get_user_profile(user.id)
        if 0 <= inv_index < len(prof["inventory"]):
            prof["inventory"].pop(inv_index)
            save_data()

    await state.clear()
    await message.answer(
        f"✅ <b>درخواست برداشت {skin} با موفقیت ثبت شد!</b>\n\n"
        f"اطلاعات به کانال پشتیبانی ارسال شد و پس از بررسی واریز می‌شود 🔥",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(user.id)
    )

@dp.callback_query(F.data.startswith("wd_approve_"))
async def approve_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    parts = call.data.split("_")
    target_user_id = int(parts[2])
    skin_name = "_".join(parts[3:])

    if "TON" in skin_name:
        msg_lines = call.message.text.split("\n")
        dest_addr = ""
        for idx, line in enumerate(msg_lines):
            if "مشخصات/آدرس ارسالی:" in line and idx + 1 < len(msg_lines):
                dest_addr = msg_lines[idx+1].strip()
                break

        # واریز واقعی مبلغ
        success, result_msg = await send_ton_payout(dest_addr, 0.01)
        if success:
            updated_text = call.message.text + "\n\n✅ <b>وضعیت: واریز واقعی کریپتویی در شبکه TON انجام شد! 💎</b>"
            await call.message.edit_text(updated_text, parse_mode="HTML", reply_markup=None)
            await call.answer("✅ 0.01 TON به‌صورت واقعی از ولت کسر و ارسال شد!", show_alert=True)
        else:
            await call.answer(f"❌ خطا در واریز شبکه: {result_msg}", show_alert=True)
            return
    else:
        updated_text = call.message.text + "\n\n✅ <b>وضعیت: واریز شد (تایید شد)</b>"
        await call.message.edit_text(updated_text, parse_mode="HTML", reply_markup=None)
        await call.answer("✅ برداشت اسکین تایید شد.", show_alert=True)

    try:
        await bot.send_message(
            target_user_id,
            f"🎉 <b>درخواست برداشت شما تایید شد!</b>\n\n🎁 جایزه <b>{skin_name}</b> با موفقیت منتقل شد. مبارکت باشه! 🔥",
            parse_mode="HTML"
        )
    except Exception:
        pass

@dp.callback_query(F.data.startswith("wd_reject_"))
async def reject_withdraw(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 شما ادمین نیستید!", show_alert=True)
        return

    parts = call.data.split("_")
    target_user_id = int(parts[2])
    skin_name = "_".join(parts[3:])

    updated_text = call.message.text + "\n\n❌ <b>وضعیت: رد شد (اطلاعات فیک/نادرست)</b>"
    await call.message.edit_text(updated_text, parse_mode="HTML", reply_markup=None)
    await call.answer("❌ درخواست رد شد.", show_alert=True)

    try:
        await bot.send_message(
            target_user_id,
            f"❌ <b>درخواست برداشت {skin_name} رد شد!</b>\n\nعلت: اطلاعات ارسالی نادرست یا فیک تشخیص داده شد.",
            parse_mode="HTML"
        )
    except Exception:
        pass

@dp.message(F.text == "🎒 انبار من (Inventory)")
async def show_inventory(message: types.Message):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    inv = prof["inventory"]

    if not inv:
        await message.answer("🎒 <b>انبار شما خالی است!</b>\nبا چرخاندن گردونه شانس می‌تونی برنده بشی و اینجا ذخیره کنی 🔥", parse_mode="HTML")
        return

    text = "🎒 <b>موجودی انبار شما:</b>\nبرای برداشت روی دکمه مربوطه کلیک کنید:\n\n"
    kb_list = []
    for idx, item in enumerate(inv):
        text += f"<b>{idx+1}.</b> {item}\n"
        kb_list.append([InlineKeyboardButton(text=f"📤 برداشت {item}", callback_data=f"withdraw_inv_{idx}")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=kb_list)
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("withdraw_inv_"))
async def withdraw_from_inv(call: types.CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_")[2])
    u_id = call.from_user.id
    prof = get_user_profile(u_id)
    
    if idx >= len(prof["inventory"]):
        await call.answer("❌ یافت نشد.", show_alert=True)
        return
        
    skin = prof["inventory"][idx]
    await state.set_state(WithdrawForm.username_info)
    await state.update_data(withdraw_skin=skin, from_inventory_index=idx)
    
    hint_text = "آدرس ولت TON (مثل EQ... یا UQ...)" if "TON" in skin else "آیدی تلگرام / یوزرنیم گیم"
    
    await call.message.edit_text(
        f"📤 <b>درخواست برداشت از انبار:</b> {skin}\n\n"
        f"لطفاً <b>{hint_text}</b> رو ارسال کن:",
        parse_mode="HTML"
    )

@dp.message(F.text == "👤 پروفایل من")
async def show_profile(message: types.Message):
    u_id = message.from_user.id
    prof = get_user_profile(u_id)
    inv_count = len(prof["inventory"])
    
    last_w = prof["last_wheel"].strftime("%Y-%m-%d %H:%M") if prof["last_wheel"] else "تا کنون استفاده نشده"
    
    text = (
        f"👤 <b>پروفایل کاربری شما:</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>آیدی عددی:</b> <code>{u_id}</code>\n"
        f"🎒 <b>تعداد آیتم‌های موجود در انبار:</b> {inv_count} عدد\n"
        f"⏱ <b>آخرین بار چرخاندن گردونه:</b> {last_w}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text.in_(["📊 لیست قرعه‌کشی‌ها ⚡️", "📊 لیست قرعه‌کشی‌های داغ ⚡️", "📋 قرعه‌کشی‌های فعال"]))
async def show_active_giveaways(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    active_items = {k: v for k, v in active_giveaways.items() if not v["ended"]}
    if not active_items:
        await message.answer("📊 <b>در حال حاضر هیچ قرعه‌کشی فعالی وجود ندارد.</b>", parse_mode="HTML")
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
        await message.answer(info_text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("stop_gw_"))
async def stop_giveaway_manual(call: types.CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("🛑 دسترسی غیرمجاز!", show_alert=True)
        return

    msg_id = int(call.data.split("_")[2])
    if msg_id in active_giveaways and not active_giveaways[msg_id]["ended"]:
        await finish_giveaway(active_giveaways[msg_id]["channel"], msg_id)
        await call.message.edit_text("💥 قرعه‌کشی به اتمام رسید و برندگان اعلام شدند.", parse_mode="HTML")
    else:
        await call.answer("⚠️ این قرعه‌کشی قبلاً تمام شده است.", show_alert=True)

@dp.message(F.text.in_(["🎁 استارت قرعه‌کشی جدید 🚀", "🎉 ساخت قرعه‌کشی جدید", "/newgiveaway"]))
async def start_giveaway(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return

    await state.set_state(GiveawayForm.title)
    await message.answer("📌 <b>عنوان قرعه‌کشی را وارد کنید:</b>", parse_mode="HTML")

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
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text("👥 <b>تعداد برندگان را وارد کنید (عدد):</b>", parse_mode="HTML")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً عدد وارد کنید!", parse_mode="HTML")
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
        "🚀 <b>تایید نهایی انتشار قرعه‌کشی:</b>\n\n"
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
        f"👥 <b>شرکت‌کنندگان (0):</b>\n"
        f"<i>هنوز کسی شرکت نکرده است.</i> 🚀\n\n"
        f"👇 جهت شرکت یا دریافت لینک دعوت از دکمه‌های زیر استفاده کنید:"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 لینک دعوت (افزایش شانس 💥)", callback_data="get_ref")]
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
    
    await call.message.edit_text(f"💥 قرعه‌کشی با موفقیت در {channel_id} منتشر شد!", parse_mode="HTML")
    await state.clear()
    
    asyncio.create_task(run_giveaway_timer(sent_msg.chat.id, sent_msg.message_id))

@dp.callback_query(F.data == "join_gw")
async def join_giveaway(call: types.CallbackQuery):
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
    msg_id = call.message.message_id
    user = call.from_user

    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("🛑 قرعه‌کشی تمام شده است!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=gw_{msg_id}_ref_{user.id}"
    
    gw = active_giveaways[msg_id]
    user_ref_count = gw["participants"].get(user.id, {}).get("referrals", 0)

    try:
        await bot.send_message(
            chat_id=user.id,
            text=(
                f"👑 <b>لینک اختصاصی دعوت شما:</b>\n"
                f"📌 <b>قرعه‌کشی:</b> {gw['title']}\n\n"
                f"🔗 <code>{ref_link}</code>\n\n"
                f"📊 <b>تعداد دعوت‌ها:</b> {user_ref_count} نفر\n"
                f"✨ هر نفر که با این لینک عضو شود، ۱ شانس اضافه دریافت می‌کنید!"
            ),
            parse_mode="HTML"
        )
        await call.answer("📩 لینک دعوت به پیوی ارسال شد!", show_alert=True)
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
            
            if u_info["username"]:
                name = f"@{u_info['username']}"
            else:
                first = html.escape(u_info["first_name"])
                name = f'<a href="tg://user?id={u_id}">{first}</a>'
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
        f"👇 جهت شرکت یا دریافت لینک دعوت از دکمه‌های زیر استفاده کنید:"
    )
    
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count}) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 لینک دعوت (افزایش شانس 💥)", callback_data="get_ref")]
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
            f"❌ متاسفانه هیچ شرکتی‌کننده‌ای ثبت‌نام نکرد."
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
            if u_info["username"]:
                winners_list.append(f"🏆 @{u_info['username']}")
            else:
                first = html.escape(u_info["first_name"])
                winners_list.append(f'🏆 <a href="tg://user?id={u_id}">{first}</a>')
        
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
    await state.clear()
    await call.message.edit_text("❌ ساخت قرعه‌کشی لغو شد.", parse_mode="HTML")

async def main():
    load_data()
    for msg_id, gw in list(active_giveaways.items()):
        if not gw["ended"]:
            asyncio.create_task(run_giveaway_timer(gw["channel"], msg_id))

    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
