import asyncio
import os
import logging
import random
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

active_giveaways = {}

class GiveawayForm(StatesGroup):
    title = State()
    prize = State()
    time_seconds = State()
    winners = State()
    channel = State()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="🎉 ساخت قرعه‌کشی جدید")]],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("سلام! به ربات **Void Giveaway** خوش آمدید.\nبرای ساخت قرعه‌کشی روی دکمه زیر بزنید 👇", parse_mode="Markdown", reply_markup=main_keyboard)

@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    await state.set_state(GiveawayForm.title)
    await message.answer("📝 **مرحله ۱:** لطفاً **عنوان قرعه‌کشی** را وارد کنید:")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.prize)
    await message.answer(f"✅ عنوان ذخیره شد: **{message.text}**\n\n🎁 **مرحله ۲:** لطفاً **نوع یا متن جایزه** را وارد کنید:")

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
    await message.answer(f"✅ جایزه ذخیره شد: **{message.text}**\n\n⏳ **مرحله ۳:** مدت زمان قرعه‌کشی را انتخاب کنید:", reply_markup=time_keyboard)

@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text("✅ زمان ذخیره شد.\n\n👥 **مرحله ۴:** تعداد برندگان را وارد کنید (عدد):")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید!")
        return
    await state.update_data(winners=int(message.text))
    await state.set_state(GiveawayForm.channel)
    await message.answer("📢 **مرحله ۵:** آیدی کانال را بفرستید (مثال: `@Voidchanneloffical`):\n⚠️ ربات باید در کانال ادمین باشد.")

@dp.message(GiveawayForm.channel)
async def process_channel(message: types.Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith("@"):
        channel_id = "@" + channel_id

    try:
        bot_member = await bot.get_chat_member(chat_id=channel_id, user_id=bot.id)
        if bot_member.status not in ["administrator", "creator"]:
            await message.answer("❌ ربات در این کانال ادمین نیست!")
            return
    except Exception:
        await message.answer("❌ کانال پیدا نشد یا ربات دسترسی ندارد.")
        return

    await state.update_data(channel=channel_id)
    data = await state.get_data()
    
    confirm_text = (
        "🎯 **تایید نهایی اطلاعات قرعه‌کشی:**\n\n"
        f"📌 **عنوان:** {data['title']}\n"
        f"🎁 **جایزه:** {data['prize']}\n"
        f"⏳ **زمان:** {data['time_seconds']} ثانیه\n"
        f"👥 **تعداد برنده:** {data['winners']} نفر\n"
        f"📢 **کانال:** {data['channel']}\n"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار قرعه‌کشی", callback_data="confirm_launch")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="cancel_launch")]
        ]
    )
    await message.answer(confirm_text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query(F.data == "confirm_launch")
async def launch_giveaway(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    channel_id = data['channel']
    total_seconds = data['time_seconds']
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    
    giveaway_text = (
        f"🎉 **{data['title']}** 🎉\n\n"
        f"🎁 **جایزه:** {data['prize']}\n"
        f"👥 **تعداد برندگان:** {data['winners']} نفر\n"
        f"⏱ **زمان باقی‌مانده:** {total_seconds} ثانیه\n\n"
        f"👥 **شرکت‌کنندگان (0):**\n"
        f"_هنوز کسی شرکت نکرده است._\n\n"
        f"👇 برای شرکت روی دکمه زیر کلیک کنید:"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0)", callback_data="join_gw")]]
    )
    
    sent_msg = await bot.send_message(chat_id=channel_id, text=giveaway_text, parse_mode="Markdown", reply_markup=channel_keyboard)
    
    active_giveaways[sent_msg.message_id] = {
        "channel": channel_id,
        "title": data['title'],
        "prize": data['prize'],
        "winners_count": data['winners'],
        "participants": [],
        "end_time": end_time,
        "ended": False
    }
    
    await call.message.edit_text(f"✅ قرعه‌کشی با موفقیت در {channel_id} منتشر شد!")
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
        users_str = "_هنوز کسی شرکت نکرده است._"
    else:
        formatted_users = []
        for idx, u in enumerate(participants_list, 1):
            name = f"@{u.username}" if u.username else f"[{u.first_name}](tg://user?id={u.id})"
            formatted_users.append(f"{idx}. {name}")
        users_str = "\n".join(formatted_users)
    
    count = len(participants_list)
    updated_text = (
        f"🎉 **{gw['title']}** 🎉\n\n"
        f"🎁 **جایزه:** {gw['prize']}\n"
        f"👥 **تعداد برندگان:** {gw['winners_count']} نفر\n"
        f"⏱ **زمان باقی‌مانده:** {time_str}\n\n"
        f"👥 **شرکت‌کنندگان ({count}):**\n{users_str}\n\n"
        f"👇 برای شرکت روی دکمه زیر کلیک کنید:"
    )
    
    channel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count})", callback_data="join_gw")]]
    )
    
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=updated_text, parse_mode="Markdown", reply_markup=channel_kb)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            logging.error(f"Telegram Edit Error: {e}")
    except Exception as e:
        logging.error(f"General Edit Error: {e}")

async def run_giveaway_timer(chat_id, message_id):
    while message_id in active_giveaways:
        gw = active_giveaways[message_id]
        if gw["ended"]:
            break
            
        remaining = (gw["end_time"] - datetime.now()).total_seconds()
        
        if remaining <= 0:
            gw["ended"] = True
            participants = gw["participants"]
            winners_count = gw["winners_count"]
            
            if not participants:
                final_text = (
                    f"🏁 **قرعه‌کشی به پایان رسید!** 🏁\n\n"
                    f"📌 **عنوان:** {gw['title']}\n"
                    f"🎁 **جایزه:** {gw['prize']}\n\n"
                    f"❌ هیچ شرکتی‌کننده‌ای ثبت‌نام نکرد."
                )
            else:
                chosen_winners = random.sample(participants, min(winners_count, len(participants)))
                winners_list = []
                for user in chosen_winners:
                    if user.username:
                        winners_list.append(f"🏆 @{user.username}")
                    else:
                        winners_list.append(f"🏆 [{user.first_name}](tg://user?id={user.id})")
                
                winners_str = "\n".join(winners_list)
                final_text = (
                    f"🏁 **قرعه‌کشی به پایان رسید!** 🏁\n\n"
                    f"📌 **عنوان:** {gw['title']}\n"
                    f"🎁 **جایزه:** {gw['prize']}\n\n"
                    f"✨ **برنده / برندگان خوش‌شانس:**\n{winners_str}"
                )
            
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=final_text, parse_mode="Markdown", reply_markup=None)
            except Exception as e:
                logging.error(f"Error updating winner message: {e}")
            
            del active_giveaways[message_id]
            break
        else:
            await update_post_text(chat_id, message_id)
            
        await asyncio.sleep(2)

@dp.callback_query(F.data == "cancel_launch")
async def cancel_launch(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ ساخت قرعه‌کشی لغو شد.")

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
