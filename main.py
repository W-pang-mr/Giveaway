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

logging.basicConfig(level=logging.INFO)

# --- وب‌سرور جهت نگه داشتن پورت در Render ---
app = Flask('')

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

# دیتابیس فعال قرعه‌کشی‌ها
active_giveaways = {}

class GiveawayForm(StatesGroup):
    title = State()
    time_seconds = State()
    winners = State()
    channel = State()

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎉 ساخت قرعه‌کشی جدید")],
        [KeyboardButton(text="⚙️ تنظیمات"), KeyboardButton(text="📊 قرعه‌کشی‌های من")]
    ],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("سلام! به ربات **Void Giveaway** خوش آمدید. برای شروع روی دکمه زیر بزنید 👇", parse_mode="Markdown", reply_markup=main_keyboard)

@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    await state.set_state(GiveawayForm.title)
    await message.answer("✏️ **مرحله ۱:** لطفاً عنوان و جایزه قرعه‌کشی را وارد کنید:")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.time_seconds)
    
    # دکمه‌های آماده انتخاب زمان
    time_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ ۱ دقیقه", callback_data="time_60"), InlineKeyboardButton(text="⏱ ۵ دقیقه", callback_data="time_300")],
            [InlineKeyboardButton(text="⏱ ۱۰ دقیقه", callback_data="time_600"), InlineKeyboardButton(text="⏱ ۱ ساعت", callback_data="time_3600")]
        ]
    )
    await message.answer("⏳ **مرحله ۲:** مدت زمان قرعه‌کشی را انتخاب کنید یا خودتان به ثانیه بفرستید:", reply_markup=time_keyboard)

# دریافت زمان از طریق دکمه‌های شیشه‌ای
@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text(f"✅ زمان انتخاب شد.\n\n👥 **مرحله ۳:** تعداد برندگان را وارد کنید (عدد):")

# دریافت زمان به صورت دستی
@dp.message(GiveawayForm.time_seconds)
async def process_time_text(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً یک عدد (ثانیه) معتبر وارد کنید یا از دکمه‌ها استفاده کنید.")
        return
    await state.update_data(time_seconds=int(message.text))
    await state.set_state(GiveawayForm.winners)
    await message.answer("👥 **مرحله ۳:** تعداد برندگان را وارد کنید (عدد):")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید!")
        return
    await state.update_data(winners=int(message.text))
    await state.set_state(GiveawayForm.channel)
    await message.answer("📢 **مرحله ۴:** آیدی کانال را بفرستید (مثال: `@Voidchanneloffical`):\n⚠️ ربات باید در کانال ادمین باشد.")

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
        f"📌 **جایزه:** {data['title']}\n"
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

# انتشار در کانال و استارت تایمر
@dp.callback_query(F.data == "confirm_launch")
async def launch_giveaway(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    channel_id = data['channel']
    total_seconds = data['time_seconds']
    
    end_time = datetime.now() + timedelta(seconds=total_seconds)
    
    giveaway_text = (
        f"🎉 **قرعه‌کشی جدید!** 🎉\n\n"
        f"🎁 **جایزه:** {data['title']}\n"
        f"👥 **تعداد برندگان:** {data['winners']} نفر\n"
        f"⏱ **زمان باقی‌مانده:** {total_seconds} ثانیه\n\n"
        f"👇 برای شرکت در قرعه‌کشی روی دکمه زیر کلیک کنید:"
    )
    
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0)", callback_data="join_gw")]
        ]
    )
    
    sent_msg = await bot.send_message(chat_id=channel_id, text=giveaway_text, parse_mode="Markdown", reply_markup=channel_keyboard)
    
    active_giveaways[sent_msg.message_id] = {
        "channel": channel_id,
        "title": data['title'],
        "winners_count": data['winners'],
        "participants": set(),
        "end_time": end_time,
        "ended": False
    }
    
    await call.message.edit_text(f"✅ قرعه‌کشی با موفقیت در {channel_id} منتشر شد و ثانیه‌شمار آغاز گشت!")
    await state.clear()
    
    # اجرای تایمر در پس‌زمینه برای آپدیت ثانیه‌شمار و پایان قرعه‌کشی
    asyncio.create_task(run_timer(sent_msg.chat.id, sent_msg.message_id))

@dp.callback_query(F.data == "join_gw")
async def join_giveaway(call: types.CallbackQuery):
    msg_id = call.message.message_id
    user_id = call.from_user.id
    
    if msg_id not in active_giveaways or active_giveaways[msg_id]["ended"]:
        await call.answer("❌ این قرعه‌کشی به پایان رسیده است!", show_alert=True)
        return
        
    gw = active_giveaways[msg_id]
    
    # بررسی عضویت کاربر در کانال قفل جوین
    try:
        member = await bot.get_chat_member(chat_id=gw["channel"], user_id=user_id)
        if member.status in ["left", "kicked"]:
            await call.answer("❌ برای شرکت در قرعه‌کشی باید حتماً عضو کانال باشید!", show_alert=True)
            return
    except Exception:
        pass

    participants = gw["participants"]
    if user_id in participants:
        await call.answer("شما قبلاً در قرعه‌کشی ثبت‌نام کرده‌اید! ✅", show_alert=True)
    else:
        participants.add(user_id)
        count = len(participants)
        new_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count})", callback_data="join_gw")]]
        )
        await call.message.edit_reply_markup(reply_markup=new_kb)
        await call.answer("🎉 با موفقیت در قرعه‌کشی ثبت‌نام شدید!", show_alert=True)

# تابع مدیریت ثانیه‌شمار و اعلام برنده نهایی
async def run_timer(chat_id, message_id):
    while message_id in active_giveaways:
        gw = active_giveaways[message_id]
        if gw["ended"]:
            break
            
        remaining = (gw["end_time"] - datetime.now()).total_seconds()
        
        if remaining <= 0:
            gw["ended"] = True
            # پایان قرعه‌کشی و انتخاب برنده
            participants = list(gw["participants"])
            winners_count = gw["winners_count"]
            
            result_text = f"🏁 **قرعه‌کشی به پایان رسید!**\n🎁 **جایزه:** {gw['title']}\n👥 **تعداد کل شرکت‌کنندگان:** {len(participants)} نفر\n\n"
            
            if len(participants) == 0:
                result_text += "❌ به دلیل عدم شرکت کاربران، برنده‌ای انتخاب نشد."
            else:
                chosen_winners = random.sample(participants, min(winners_count, len(participants)))
                winner_mentions = []
                for w_id in chosen_winners:
                    try:
                        user_info = await bot.get_chat(w_id)
                        name = user_info.first_name
                        username = f"(@{user_info.username})" if user_info.username else f"(ID: {w_id})"
                        winner_mentions.append(f"🏆 [{name}](tg://user?id={w_id}) {username}")
                    except Exception:
                        winner_mentions.append(f"🏆 کاربر با آیدی `_{w_id}_`")
                
                result_text += "✨ **برندگان خوش‌شانس این دوره:**\n" + "\n".join(winner_mentions)
            
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=result_text, parse_mode="Markdown", reply_markup=None)
            except Exception:
                pass
            break
        else:
            mins, secs = divmod(int(remaining), 60)
            time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
            
            # آپدیت متن ثانیه‌شمار در کانال هر ۵ ثانیه
            try:
                count = len(gw["participants"])
                updated_text = (
                    f"🎉 **قرعه‌کشی جدید!** 🎉\n\n"
                    f"🎁 **جایزه:** {gw['title']}\n"
                    f"👥 **تعداد برندگان:** {gw['winners_count']} نفر\n"
                    f"⏱ **زمان باقی‌مانده:** {time_str}\n\n"
                    f"👇 برای شرکت در قرعه‌کشی روی دکمه زیر کلیک کنید:"
                )
                channel_kb = InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count})", callback_data="join_gw")]]
                )
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=updated_text, parse_mode="Markdown", reply_markup=channel_kb)
            except Exception:
                pass
                
        await asyncio.sleep(5)

@dp.callback_query(F.data == "cancel_launch")
async def cancel_launch(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ لغو شد.")

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
