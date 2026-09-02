# ==========================================
# Void Giveaway Bot - Version 1.2.0 (Admin Locked + Referral)
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

logging.basicConfig(level=logging.INFO)

# --- وب‌سرور جهت زنده نگه داشتن پورت در Render ---
app = Flask(__name__)

@app.route('/')
def home():
    return "⚡ Void Giveaway Bot (v1.2.0) is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN")

# 🔒 لیست آیدی‌های عددی ادمین‌های مجاز
ADMIN_IDS = [6879499219]

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DATA_FILE = "giveaways.json"
active_giveaways = {}

# --- ذخیره و بازیابی داده‌ها ---
def save_data():
    serializable = {}
    for msg_id, gw in active_giveaways.items():
        serializable[str(msg_id)] = {
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
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2)

def load_data():
    global active_giveaways
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for msg_id_str, gw in data.items():
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
        except Exception as e:
            logging.error(f"Error loading data: {e}")

class GiveawayForm(StatesGroup):
    title = State()
    prize = State()
    time_seconds = State()
    winners = State()
    channel = State()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎁 استارت قرعه‌کشی جدید 🚀")],
        [KeyboardButton(text="📊 لیست قرعه‌کشی‌های داغ ⚡️")]
    ],
    resize_keyboard=True
)

# بررسی ادمین بودن کاربر
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject, state: FSMContext):
    await state.clear()
    args = command.args

    # لینک رفرال ورود به ربات (gw_MSGID_ref_USERID)
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
                        await message.answer(f"⚠️ <b>رفیق برای ثبت زیرمجموعه:</b>\nاول باید جوین کانال {gw['channel']} بشی، بعد دوباره روی لینک بزنی! ⚡️", parse_mode="HTML")
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
                            await bot.send_message(referrer_id, f"🔥 <b>دمت گرم! یک نفر با لینکت وارد شد!</b>\n🎉 قرعه‌کشی: <b>{gw['title']}</b>\n⚡️ 💥 <b>+۱ شانس اضافه</b> برات ثبت شد!", parse_mode="HTML")
                        except Exception:
                            pass

                    save_data()
                    await update_post_text(gw["channel"], msg_id)
                    await message.answer(f"👑 <b>بمب!</b> با موفقیت از طریق لینک دعوت وارد قرعه‌کشی بزرگ <b>{gw['title']}</b> شدی! 🎁✨", parse_mode="HTML")
                    return
                else:
                    await message.answer("💎 💎 قبلاً توی این قرعه‌کشی ثبت‌نام کردی رفیق! منتظر اعلام برنده باش ⚡️", parse_mode="HTML")
                    return
        except Exception as e:
            logging.error(f"Referral Start Error: {e}")

    if is_admin(message.from_user.id):
        await message.answer(
            "👑 <b>سلام سلطان! به پنل فرماندهی Void Giveaway خوش آمدی!</b> 🔥\n"
            "📌 <b>نسخه ربات:</b> <code>v1.2.0</code> ⚡️\n\n"
            "امروز قراره چه بمبی بترکونیم؟ از دکمه‌های زیر انتخاب کن 👇",
            parse_mode="HTML",
            reply_markup=main_keyboard
        )
    else:
        await message.answer(
            "⚡️ <b>به ربات بزرگ قرعه‌کشی Void Giveaway خوش آمدی!</b> ✨\n"
            "📌 <b>نسخه ربات:</b> <code>v1.2.0</code> 💎\n\n"
            "🔥 اینجا مرکز جوایز خفن، اسکین‌ها و کریپتوئه! منتظر قرعه‌کشی‌های بعدی کانال باش 🎁",
            parse_mode="HTML"
        )

# --- مدیریت قرعه‌کشی‌های فعال (فقط ادمین) ---
@dp.message(F.text == "📊 لیست قرعه‌کشی‌های داغ ⚡️")
@dp.message(F.text == "📋 قرعه‌کشی‌های فعال")
async def show_active_giveaways(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ دسترسی محدود! فقط ادمین‌های اصلی مجاز هستند.")
        return

    active_items = {k: v for k, v in active_giveaways.items() if not v["ended"]}
    if not active_items:
        await message.answer("📊 <b>در حال حاضر هیچ قرعه‌کشی فعالی روی هوا نیست!</b>", parse_mode="HTML")
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
            f"👥 <b>ارتش شرکت‌کنندگان:</b> {len(gw['participants'])} نفر\n"
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
        await call.message.edit_text("💥 <b>بمب منفجر شد!</b> قرعه‌کشی به اتمام رسید و برندگان توی کانال اعلام شدند 🏆", parse_mode="HTML")
    else:
        await call.answer("⚠️ این قرعه‌کشی قبلاً تمام شده یا وجود ندارد.", show_alert=True)

# --- فرآیند ساخت قرعه‌کشی (فقط ادمین) ---
@dp.message(F.text == "🎁 استارت قرعه‌کشی جدید 🚀")
@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ دسترسی محدود! فقط ادمین‌های اصلی مجاز هستند.")
        return

    await state.set_state(GiveawayForm.title)
    await message.answer("📌 <b>مرحله ۱ از ۵:</b>\nلطفاً یک <b>عنوان پرانرژی و جذاب</b> برای قرعه‌کشی وارد کن 🔥:", parse_mode="HTML")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.prize)
    await message.answer(f"✅ عنوان ثبت شد: <b>{html.escape(message.text)}</b>\n\n🎁 <b>مرحله ۲ از ۵:</b>\nحالا دقیقاً بگید <b>جایزه خفن</b> این قرعه‌کشی چیه؟ 💎", parse_mode="HTML")

@dp.message(GiveawayForm.prize)
async def process_prize(message: types.Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(GiveawayForm.time_seconds)
    
    time_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ ۱ دقیقه (تست)", callback_data="time_60"), InlineKeyboardButton(text="⏱ ۵ دقیقه", callback_data="time_300")],
            [InlineKeyboardButton(text="⏱ ۱۰ دقیقه", callback_data="time_600"), InlineKeyboardButton(text="⏱ ۱ ساعت ⚡️", callback_data="time_3600")]
        ]
    )
    await message.answer(f"✅ جایزه ثبت شد: <b>{html.escape(message.text)}</b>\n\n⏳ <b>مرحله ۳ از ۵:</b>\nمدت زمان هیجان چقدر باشه؟ انتخاب کن 👇", parse_mode="HTML", reply_markup=time_keyboard)

@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text("✅ زمانبندی ثبت شد.\n\n👑 <b>مرحله ۴ از ۵:</b>\nقراره چند نفر <b>برنده خوش‌شانس</b> داشته باشیم؟ (عدد بفرست):", parse_mode="HTML")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("⚠️ لطفاً فقط عدد انگلیسی وارد کن رفیق!", parse_mode="HTML")
        return
    await state.update_data(winners=int(message.text))
    await state.set_state(GiveawayForm.channel)
    await message.answer("📢 <b>مرحله ۵ از ۵ (نهایی):</b>\nآیدی کانالی که قراره این بمب توش بترکه رو بفرست (مثال: <code>@Voidchanneloffical</code>):\n⚠️ <i>حواست باشه ربات توی کانال ادمین باشه!</i>", parse_mode="HTML")

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
            await message.answer("❌ ربات هنوز توی این کانال **ادمین** نشده! اول ادمینش کن، بعد دوباره آیدی رو بفرست.", parse_mode="HTML")
            return
    except Exception as e:
        logging.error(f"Channel Check Error: {e}")
        await message.answer("❌ کانال پیدا نشد یا ربات دسترسی نداره!\nتست کن که ربات حتماً ادمین کانال باشه.", parse_mode="HTML")
        return

    await state.update_data(channel=channel_id)
    data = await state.get_data()
    
    confirm_text = (
        "🚀 <b>پیش‌نمایش و تایید نهایی قرعه‌کشی:</b>\n"
        "━━━━━ CONFIG ━━━━━\n"
        f"📌 <b>عنوان:</b> {html.escape(data['title'])}\n"
        f"🎁 <b>جایزه:</b> {html.escape(data['prize'])}\n"
        f"⏳ <b>زمان:</b> {data['time_seconds']} ثانیه\n"
        f"🏆 <b>تعداد برندگان:</b> {data['winners']} نفر\n"
        f"📢 <b>کانال مقصد:</b> {data['channel']}\n"
        "━━━━━━━━━━━━━━━━━\n"
        "همه چی ردیفه بفرستم روی هوا؟ 🔥"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار و شلیک به کانال 🔥", callback_data="confirm_launch")],
            [InlineKeyboardButton(text="❌ کنسل کردن", callback_data="cancel_launch")]
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
        f"🏆 <b>تعداد برندگان خوش‌شانس:</b> {data['winners']} نفر\n"
        f"⏱ <b>تایمر معکوس:</b> {total_seconds} ثانیه ⏳\n\n"
        f"👥 <b>ارتش شرکت‌کنندگان (0):</b>\n"
        f"<i>هنوز هیچ مبارزی وارد میدان نشده!</i> 🚀\n\n"
        f"👇 <b>همین الان شانس خودت رو امتحان کن یا با دعوت بقیه شانست رو چند برابر کن!</b>"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 ورود به قرعه‌کشی (0) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 دریافت لینک دعوت (شانس 💥)", callback_data="get_ref")]
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
    
    await call.message.edit_text(f"💥 <b>بمب با موفقیت توی {channel_id} شلیک شد!</b> 🚀", parse_mode="HTML")
    await state.clear()
    
    asyncio.create_task(run_giveaway_timer(sent_msg.chat.id, sent_msg.message_id))

@dp.callback_query(F.data == "join_gw")
async def join_giveaway(call: types.CallbackQuery):
    msg_id = call.message.message_id
    user = call.from_user
    
    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("🛑 مهلت شرکت در این قرعه‌کشی تمام شده!", show_alert=True)
        return
        
    gw = active_giveaways[msg_id]
    
    try:
        member = await bot.get_chat_member(chat_id=gw["channel"], user_id=user.id)
        if member.status in ["left", "kicked"]:
            await call.answer("❌ اول باید جوین کانال بشی رفیق!", show_alert=True)
            return
    except Exception:
        pass

    if user.id in gw["participants"]:
        await call.answer("💎 شانس تو قبلاً ثبت شده رفیق! منتظر قرعه‌کشی باش.", show_alert=True)
        return

    gw["participants"][user.id] = {
        "username": user.username,
        "first_name": user.first_name,
        "referrals": 0
    }
    save_data()
    await call.answer("🎉 ایول! با موفقیت توی قرعه‌کشی ثبت‌نام شدی 🔥", show_alert=True)
    
    await update_post_text(call.message.chat.id, msg_id)

@dp.callback_query(F.data == "get_ref")
async def get_referral_link(call: types.CallbackQuery):
    msg_id = call.message.message_id
    user = call.from_user

    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("🛑 این قرعه‌کشی تمام شده است!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=gw_{msg_id}_ref_{user.id}"
    
    gw = active_giveaways[msg_id]
    user_ref_count = gw["participants"].get(user.id, {}).get("referrals", 0)

    try:
        await bot.send_message(
            chat_id=user.id,
            text=(
                f"👑 <b>لینک اختصاصی افزایش شانس شما:</b>\n"
                f"📌 <b>قرعه‌کشی:</b> {gw['title']}\n\n"
                f"🔗 <code>{ref_link}</code>\n\n"
                f"📊 <b>زیرمجموعه‌های تا الان:</b> {user_ref_count} نفر 🔥\n"
                f"✨ این لینک رو برا رفقات بفرست؛ هر نفر جوین بشه <b>+۱ شانس اختصاصی</b> می‌گیری!"
            ),
            parse_mode="HTML"
        )
        await call.answer("📩 لینک اختصاصی به پیوی ارسال شد!", show_alert=True)
    except Exception:
        await call.answer("⚠️ اول باید ربات رو توی پیوی استارت کنی تا لینک برات ارسال بشه!", show_alert=True)

async def update_post_text(chat_id, message_id):
    if message_id not in active_giveaways:
        return
        
    gw = active_giveaways[message_id]
    remaining = max(0, int((gw["end_time"] - datetime.now()).total_seconds()))
    mins, secs = divmod(remaining, 60)
    time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
    
    participants_dict = gw["participants"]
    if not participants_dict:
        users_str = "<i>هنوز هیچ مبارزی وارد میدان نشده!</i> 🚀"
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
        f"🏆 <b>تعداد برندگان خوش‌شانس:</b> {gw['winners_count']} نفر\n"
        f"⏱ <b>تایمر معکوس:</b> {time_str} ⏳\n\n"
        f"👥 <b>ارتش شرکت‌کنندگان ({count}):</b>\n{users_str}\n\n"
        f"👇 <b>همین الان شانس خودت رو امتحان کن یا با دعوت بقیه شانست رو چند برابر کن!</b>"
    )
    
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 ورود به قرعه‌کشی ({count}) ⚡️", callback_data="join_gw")],
            [InlineKeyboardButton(text="🔗 دریافت لینک دعوت (شانس 💥)", callback_data="get_ref")]
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
            f"🏁 <b>قرعه‌کشی به پایان رسید!</b> 🏁\n\n"
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
            f"👑 <b>قرعه‌کشی به پایان رسید و بمب انفجار جوایز رخ داد!</b> 👑\n"
            f"✨ ──────────────── ✨\n\n"
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n\n"
            f"🎉 <b>برنده / برندگان خوش‌شانس و الماس این دوره:</b>\n{winners_str}\n\n"
            f"🔥 <i>مبارکتون باشه! منتظر قرعه‌کشی‌های خفن بعدی باشید...</i> ✨"
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
