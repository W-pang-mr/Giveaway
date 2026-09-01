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
from aiogram.filters import CommandStart
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
    return "⚡ Void Giveaway Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

TOKEN = os.environ.get("BOT_TOKEN", "8940706019:AAHEsHRP50Ryvpg8sLf2ovV7m6cBTTbXVtI")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

DATA_FILE = "giveaways.json"
active_giveaways = {}

# --- توابع ذخیره و بازیابی داده‌ها در فایل برای جلوگیری از پاک شدن موقع آپدیت ---
def save_data():
    serializable = {}
    for msg_id, gw in active_giveaways.items():
        serializable[str(msg_id)] = {
            "channel": gw["channel"],
            "title": gw["title"],
            "prize": gw["prize"],
            "winners_count": gw["winners_count"],
            "participants": [{"id": u.id, "username": u.username, "first_name": u.first_name} for u in gw["participants"]],
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
                    participants = [
                        types.User(id=u["id"], is_bot=False, first_name=u["first_name"], username=u.get("username"))
                        for u in gw["participants"]
                    ]
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
        [KeyboardButton(text="🎉 ساخت قرعه‌کشی جدید")],
        [KeyboardButton(text="📋 قرعه‌کشی‌های فعال")]
    ],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("سلام! به ربات <b>Void Giveaway</b> خوش آمدید.\nبرای ساخت یا مدیریت قرعه‌کشی‌ها از دکمه‌های زیر استفاده کنید 👇", parse_mode="HTML", reply_markup=main_keyboard)

# --- مدیریت قرعه‌کشی‌های فعال ---
@dp.message(F.text == "📋 قرعه‌کشی‌های فعال")
async def show_active_giveaways(message: types.Message):
    active_items = {k: v for k, v in active_giveaways.items() if not v["ended"]}
    if not active_items:
        await message.answer("❌ در حال حاضر هیچ قرعه‌کشی فعالی وجود ندارد.", parse_mode="HTML")
        return

    for msg_id, gw in active_items.items():
        remaining = max(0, int((gw["end_time"] - datetime.now()).total_seconds()))
        mins, secs = divmod(remaining, 60)
        time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
        
        info_text = (
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n"
            f"📢 <b>کانال:</b> {gw['channel']}\n"
            f"👥 <b>شرکت کنندگان:</b> {len(gw['participants'])} نفر\n"
            f"⏱ <b>زمان باقی‌مانده:</b> {time_str}\n"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🛑 اتمام فوری قرعه‌کشی", callback_data=f"stop_gw_{msg_id}")]]
        )
        await message.answer(info_text, parse_mode="HTML", reply_markup=keyboard)

@dp.callback_query(F.data.startswith("stop_gw_"))
async def stop_giveaway_manual(call: types.CallbackQuery):
    msg_id = int(call.data.split("_")[2])
    if msg_id in active_giveaways and not active_giveaways[msg_id]["ended"]:
        await finish_giveaway(active_giveaways[msg_id]["channel"], msg_id)
        await call.message.edit_text("✅ قرعه‌کشی با موفقیت به پایان رسید و برندگان در کانال اعلام شدند.", parse_mode="HTML")
    else:
        await call.answer("❌ این قرعه‌کشی قبلاً تمام شده یا وجود ندارد.", show_alert=True)

# --- فرآیند ساخت قرعه‌کشی ---
@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    await state.set_state(GiveawayForm.title)
    await message.answer("📝 <b>مرحله ۱:</b> لطفاً <b>عنوان قرعه‌کشی</b> را وارد کنید:", parse_mode="HTML")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.prize)
    await message.answer(f"✅ عنوان ذخیره شد: <b>{html.escape(message.text)}</b>\n\n🎁 <b>مرحله ۲:</b> لطفاً <b>نوع یا متن جایزه</b> را وارد کنید:", parse_mode="HTML")

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
    await message.answer(f"✅ جایزه ذخیره شد: <b>{html.escape(message.text)}</b>\n\n⏳ <b>مرحله ۳:</b> مدت زمان قرعه‌کشی را انتخاب کنید:", parse_mode="HTML", reply_markup=time_keyboard)

@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text("✅ زمان ذخیره شد.\n\n👥 <b>مرحله ۴:</b> تعداد برندگان را وارد کنید (عدد):", parse_mode="HTML")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید!", parse_mode="HTML")
        return
    await state.update_data(winners=int(message.text))
    await state.set_state(GiveawayForm.channel)
    await message.answer("📢 <b>مرحله ۵:</b> آیدی کانال را بفرستید (مثال: <code>@Voidchanneloffical</code>):\n⚠️ ربات باید در کانال ادمین باشد.", parse_mode="HTML")

@dp.message(GiveawayForm.channel)
async def process_channel(message: types.Message, state: FSMContext):
    raw_channel = message.text.strip()
    
    if "t.me/" in raw_channel:
        raw_channel = raw_channel.split("t.me/")[-1].replace("/", "")
    if not raw_channel.startswith("@"):
        channel_id = "@" + raw_channel
    else:
        channel_id = raw_channel

    try:
        chat = await bot.get_chat(channel_id)
        member = await bot.get_chat_member(chat_id=chat.id, user_id=bot.id)
        if member.status not in ["administrator", "creator"]:
            await message.answer("❌ ربات هنوز در این کانال **ادمین** نیست! ابتدا ربات را ادمین کانال کنید و دوباره آیدی را بفرستید.", parse_mode="HTML")
            return
    except Exception as e:
        logging.error(f"Channel Check Error: {e}")
        await message.answer("❌ کانال پیدا نشد یا ربات دسترسی ندارد!\nتست کنید که ربات حتماً ادمین کانال شده باشد.", parse_mode="HTML")
        return

    await state.update_data(channel=channel_id)
    data = await state.get_data()
    
    confirm_text = (
        "🎯 <b>تایید نهایی اطلاعات قرعه‌کشی:</b>\n\n"
        f"📌 <b>عنوان:</b> {html.escape(data['title'])}\n"
        f"🎁 <b>جایزه:</b> {html.escape(data['prize'])}\n"
        f"⏳ <b>زمان:</b> {data['time_seconds']} ثانیه\n"
        f"👥 <b>تعداد برنده:</b> {data['winners']} نفر\n"
        f"📢 <b>کانال:</b> {data['channel']}\n"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار قرعه‌کشی", callback_data="confirm_launch")],
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
        f"🎉 <b>{title}</b> 🎉\n\n"
        f"🎁 <b>جایزه:</b> {prize}\n"
        f"👥 <b>تعداد برندگان:</b> {data['winners']} نفر\n"
        f"⏱ <b>زمان باقی‌مانده:</b> {total_seconds} ثانیه\n\n"
        f"👥 <b>شرکت‌کنندگان (0):</b>\n"
        f"<i>هنوز کسی شرکت نکرده است.</i>\n\n"
        f"👇 برای شرکت روی دکمه زیر کلیک کنید:"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0)", callback_data="join_gw")]]
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
        "participants": [],
        "end_time": end_time,
        "ended": False
    }
    save_data()
    
    await call.message.edit_text(f"✅ قرعه‌کشی با موفقیت در {channel_id} منتشر شد!", parse_mode="HTML")
    await state.clear()
    
    asyncio.create_task(run_giveaway_timer(sent_msg.chat.id, sent_msg.message_id))

@dp.callback_query(F.data == "join_gw")
async def join_giveaway(call: types.CallbackQuery):
    msg_id = call.message.message_id
    user = call.from_user
    
    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("❌ این قرعه‌کشی به پایان رسیده است!", show_alert=True)
        return
        
    gw = active_giveaways[msg_id]
    
    try:
        member = await bot.get_chat_member(chat_id=gw["channel"], user_id=user.id)
        if member.status in ["left", "kicked"]:
            await call.answer("❌ برای شرکت باید عضو کانال باشید!", show_alert=True)
            return
    except Exception:
        pass

    user_ids = [u.id for u in gw["participants"]]
    if user.id in user_ids:
        await call.answer("شما قبلاً ثبت‌نام شده‌اید! ✅", show_alert=True)
        return

    gw["participants"].append(user)
    save_data()
    await call.answer("🎉 با موفقیت ثبت‌نام شدید!", show_alert=True)
    
    await update_post_text(call.message.chat.id, msg_id)

async def update_post_text(chat_id, message_id):
    if message_id not in active_giveaways:
        return
        
    gw = active_giveaways[message_id]
    remaining = max(0, int((gw["end_time"] - datetime.now()).total_seconds()))
    mins, secs = divmod(remaining, 60)
    time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
    
    participants_list = gw["participants"]
    if not participants_list:
        users_str = "<i>هنوز کسی شرکت نکرده است.</i>"
    else:
        formatted_users = []
        for idx, u in enumerate(participants_list, 1):
            if u.username:
                name = f"@{u.username}"
            else:
                first = html.escape(u.first_name)
                name = f'<a href="tg://user?id={u.id}">{first}</a>'
            formatted_users.append(f"{idx}. {name}")
        users_str = "\n".join(formatted_users)
    
    count = len(participants_list)
    updated_text = (
        f"🎉 <b>{gw['title']}</b> 🎉\n\n"
        f"🎁 <b>جایزه:</b> {gw['prize']}\n"
        f"👥 <b>تعداد برندگان:</b> {gw['winners_count']} نفر\n"
        f"⏱ <b>زمان باقی‌مانده:</b> {time_str}\n\n"
        f"👥 <b>شرکت‌کنندگان ({count}):</b>\n{users_str}\n\n"
        f"👇 برای شرکت روی دکمه زیر کلیک کنید:"
    )
    
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count})", callback_data="join_gw")]]
    )
    
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=updated_text, parse_mode="HTML", reply_markup=channel_kb)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logging.error(f"General Edit Error: {e}")

# تابع اختصاصی پایان دادن به قرعه‌کشی (چه زمان تمام شود چه دستی)
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
            f"❌ هیچ شرکتی‌کننده‌ای ثبت‌نام نکرد."
        )
    else:
        chosen_winners = random.sample(participants, min(winners_count, len(participants)))
        winners_list = []
        for user in chosen_winners:
            if user.username:
                winners_list.append(f"🏆 @{user.username}")
            else:
                first = html.escape(user.first_name)
                winners_list.append(f'🏆 <a href="tg://user?id={user.id}">{first}</a>')
        
        winners_str = "\n".join(winners_list)
        final_text = (
            f"🏁 <b>قرعه‌کشی به پایان رسید!</b> 🏁\n\n"
            f"📌 <b>عنوان:</b> {gw['title']}\n"
            f"🎁 <b>جایزه:</b> {gw['prize']}\n\n"
            f"✨ <b>برنده / برندگان خوش‌شانس:</b>\n{winners_str}"
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
    load_data()  # بارگذاری مجدد قرعه‌کشی‌های قبلی پس از ری‌استارت ربات
    
    # راه‌اندازی مجدد تایمر برای قرعه‌کشی‌های نیمه‌کاره
    for msg_id, gw in list(active_giveaways.items()):
        if not gw["ended"]:
            asyncio.create_task(run_giveaway_timer(gw["channel"], msg_id))

    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
