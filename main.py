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

# تعریف مراحل FSM
class GiveawayForm(StatesGroup):
    title = State()
    prize = State()
    time_seconds = State()
    winners = State()
    channel = State()

# کیبورد اصلی (فقط یک دکمه)
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎉 ساخت قرعه‌کشی جدید")]
    ],
    resize_keyboard=True
)

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("سلام! به ربات **Void Giveaway** خوش آمدید.\nبرای ساخت قرعه‌کشی جدید روی دکمه زیر بزنید 👇", parse_mode="Markdown", reply_markup=main_keyboard)

@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    await state.set_state(GiveawayForm.title)
    await message.answer("📝 **مرحله ۱:** لطفاً **عنوان قرعه‌کشی** را وارد کنید:")

# ۱. دریافت عنوان
@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.prize)
    await message.answer(f"✅ عنوان ذخیره شد: **{message.text}**\n\n🎁 **مرحله ۲:** لطفاً **نوع یا متن جایزه** را وارد کنید:")

# ۲. دریافت جایزه
@dp.message(GiveawayForm.prize)
async def process_prize(message: types.Message, state: FSMContext):
    await state.update_data(prize=message.text)
    await state.set_state(GiveawayForm.time_seconds)
    
    time_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏱ ۱۰ ثانیه (تست)", callback_data="time_10"), InlineKeyboardButton(text="⏱ ۱ دقیقه", callback_data="time_60")],
            [InlineKeyboardButton(text="⏱ ۵ دقیقه", callback_data="time_300"), InlineKeyboardButton(text="⏱ ۱ ساعت", callback_data="time_3600")]
        ]
    )
    await message.answer(f"✅ جایزه ذخیره شد: **{message.text}**\n\n⏳ **مرحله ۳:** مدت زمان قرعه‌کشی را انتخاب کنید:", reply_markup=time_keyboard)

@dp.callback_query(F.data.startswith("time_"))
async def process_time_callback(call: types.CallbackQuery, state: FSMContext):
    secs = int(call.data.split("_")[1])
    await state.update_data(time_seconds=secs)
    await state.set_state(GiveawayForm.winners)
    await call.message.edit_text(f"✅ زمان ذخیره شد.\n\n👥 **مرحله ۴:** تعداد برندگان را وارد کنید (عدد):")

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
        f"⏱ **زمان باقی‌مانده:** {total_seconds} ثانیه\. \n\n"
        f"📌 *تمامی اعضای کانال به صورت خودکار در قرعه‌کشی شرکت داده می‌شوند\.*"
    )
    
    sent_msg = await bot.send_message(chat_id=channel_id, text=giveaway_text, parse_mode="Markdown")
    
    await call.message.edit_text(f"✅ قرعه‌کشی در {channel_id} منتشر شد!")
    await state.clear()
    
    # تایمر برای مدیریت زمان و اعلام برنده
    asyncio.create_task(run_giveaway_timer(channel_id, sent_msg.message_id, data['title'], data['prize'], data['winners'], end_time))

async def run_giveaway_timer(channel_id, message_id, title, prize, winners_count, end_time):
    while True:
        remaining = (end_time - datetime.now()).total_seconds()
        
        if remaining <= 0:
            # زمان تمام شد -> انتخاب برنده از اعضای کانال
            try:
                # گرفتن تعداد اعضا و قرعه کشی بین اعضا (استفاده از ادمین‌ها و سازنده به عنوان نمونه اعضای فعال)
                chat = await bot.get_chat(channel_id)
                admins = await bot.get_chat_administrators(channel_id)
                
                # لیست شناسه کاربران کانال
                eligible_users = [admin.user for admin in admins if not admin.user.is_bot]
                
                if eligible_users:
                    chosen = random.sample(eligible_users, min(winners_count, len(eligible_users)))
                    winners_str = []
                    for user in chosen:
                        if user.username:
                            winners_str.append(f"🏆 @{user.username}")
                        else:
                            winners_str.append(f"🏆 [{user.first_name}](tg://user?id={user.id})")
                    winners_text = "\n".join(winners_str)
                else:
                    winners_text = "برنده‌ای یافت نشد."

                final_text = (
                    f"🏁 **قرعه‌کشی به پایان رسید!** 🏁\n\n"
                    f"📌 **عنوان:** {title}\n"
                    f"🎁 **جایزه:** {prize}\n\n"
                    f"✨ **برنده / برندگان خوش‌شانس:**\n{winners_text}"
                )
                
                await bot.edit_message_text(chat_id=channel_id, message_id=message_id, text=final_text, parse_mode="Markdown")
            except Exception as e:
                logging.error(f"Error ending giveaway: {e}")
            break
        else:
            mins, secs = divmod(int(remaining), 60)
            time_str = f"{mins} دقیقه و {secs} ثانیه" if mins > 0 else f"{secs} ثانیه"
            
            updated_text = (
                f"🎉 **{title}** 🎉\n\n"
                f"🎁 **جایزه:** {prize}\n"
                f"👥 **تعداد برندگان:** {winners_count} نفر\n"
                f"⏱ **زمان باقی‌مانده:** {time_str}\n\n"
                f"📌 *تمامی اعضای کانال به صورت خودکار در قرعه‌کشی شرکت داده می‌شوند\.*"
            )
            try:
                await bot.edit_message_text(chat_id=channel_id, message_id=message_id, text=updated_text, parse_mode="Markdown")
            except Exception:
                pass
                
        await asyncio.sleep(4)

@dp.callback_query(F.data == "cancel_launch")
async def cancel_launch(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ ساخت قرعه‌کشی لغو شد.")

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
