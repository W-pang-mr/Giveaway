import asyncio
import os
import logging
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

# --- تنظیمات ربات و FSM ---
TOKEN = os.environ.get("BOT_TOKEN", "8940706019:AAHEsHRP50Ryvpg8sLf2ovV7m6cBTTbXVtI")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# دیتابیس ساده برای ثبت شرکت‌کنندگان هر قرعه‌کشی
giveaways_db = {}

class GiveawayForm(StatesGroup):
    title = State()
    time = State()
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
    welcome_text = (
        f"سلام {message.from_user.first_name} عزیز! 🎁\n\n"
        f"به ربات **Void Giveaway** خوش آمدید.\n"
        f"برای شروع قرعه‌کشی جدید روی دکمه زیر بزنید 👇"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)

@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def start_giveaway(message: types.Message, state: FSMContext):
    await state.set_state(GiveawayForm.title)
    await message.answer("✏️ **مرحله ۱:** لطفاً عنوان یا جایزه قرعه‌کشی را وارد کنید:")

@dp.message(GiveawayForm.title)
async def process_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(GiveawayForm.time)
    await message.answer(f"✅ عنوان ذخیره شد: **{message.text}**\n\n⏳ **مرحله ۲:** مدت زمان را وارد کنید (مثلاً: 24 ساعت):")

@dp.message(GiveawayForm.time)
async def process_time(message: types.Message, state: FSMContext):
    await state.update_data(time=message.text)
    await state.set_state(GiveawayForm.winners)
    await message.answer(f"✅ زمان ذخیره شد: **{message.text}**\n\n👥 **مرحله ۳:** تعداد برندگان را وارد کنید (عدد):")

@dp.message(GiveawayForm.winners)
async def process_winners(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("❌ لطفاً یک عدد معتبر وارد کنید!")
        return
    await state.update_data(winners=message.text)
    await state.set_state(GiveawayForm.channel)
    await message.answer(
        f"✅ تعداد برندگان ذخیره شد: **{message.text} نفر**\n\n"
        f"📢 **مرحله ۴:** آیدی کانال قفل جوین را بفرستید (مثال: `@Voidchanneloffical`):\n"
        f"⚠️ *نکته:* ربات باید در کانال شما ادمین باشد!"
    )

@dp.message(GiveawayForm.channel)
async def process_channel(message: types.Message, state: FSMContext):
    channel_id = message.text.strip()
    if not channel_id.startswith("@"):
        channel_id = "@" + channel_id

    try:
        bot_member = await bot.get_chat_member(chat_id=channel_id, user_id=bot.id)
        if bot_member.status not in ["administrator", "creator"]:
            await message.answer(f"❌ ربات در کانال {channel_id} ادمین نیست! لطفاً ابتدا ربات را ادمین کانال کنید.")
            return

        user_member = await bot.get_chat_member(chat_id=channel_id, user_id=message.from_user.id)
        if user_member.status not in ["administrator", "creator"]:
            await message.answer(f"❌ شما ادمین یا مالک کانال {channel_id} نیستید!")
            return

    except Exception:
        await message.answer("❌ کانال پیدا نشد یا ربات دسترسی ندارد. آیدی را چک کرده و مطمئن شوید ربات ادمین است.")
        return

    await state.update_data(channel=channel_id)
    data = await state.get_data()
    
    confirm_text = (
        "🎯 **اطلاعات قرعه‌کشی با موفقیت تنظیم شد:**\n\n"
        f"📌 **عنوان:** {data['title']}\n"
        f"⏳ **زمان:** {data['time']}\n"
        f"👥 **تعداد برنده:** {data['winners']} نفر\n"
        f"📢 **کانال:** {data['channel']}\n\n"
        "آیا برای انتشار در کانال تایید می‌کنید؟"
    )
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 انتشار قرعه‌کشی", callback_data="confirm_launch")],
            [InlineKeyboardButton(text="❌ لغو", callback_data="cancel_launch")]
        ]
    )
    
    await message.answer(confirm_text, parse_mode="Markdown", reply_markup=keyboard)

# --- انتشار پست قرعه کشی در کانال ---
@dp.callback_query(F.data == "confirm_launch")
async def launch_giveaway(call: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    channel_id = data['channel']
    
    giveaway_text = (
        f"🎉 **قرعه‌کشی جدید!** 🎉\n\n"
        f"🎁 **جایزه:** {data['title']}\n"
        f"👥 **تعداد برندگان:** {data['winners']} نفر\n"
        f"⏳ **مهلت:** {data['time']}\n\n"
        f"👇 برای شرکت در قرعه‌کشی روی دکمه زیر کلیک کنید:"
    )
    
    # دکمه شیشه‌ای برای کانال
    channel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی (0)", callback_data="join_giveaway")]
        ]
    )
    
    try:
        # ارسال پیام به کانال
        sent_msg = await bot.send_message(chat_id=channel_id, text=giveaway_text, parse_mode="Markdown", reply_markup=channel_keyboard)
        
        # ذخیره شناسه قرعه‌کشی
        giveaways_db[sent_msg.message_id] = {
            "participants": set(),
            "channel": channel_id,
            "title": data['title']
        }
        
        await call.message.edit_text(f"✅ **قرعه‌کشی با موفقیت در کانال {channel_id} پست شد!**")
    except Exception as e:
        await call.message.edit_text(f"❌ خطا در ارسال پست به کانال: {e}")
        
    await state.clear()

# --- کلیک کاربر روی دکمه شرکت در قرعه کشی در کانال ---
@dp.callback_query(F.data == "join_giveaway")
async def join_giveaway_callback(call: types.CallbackQuery):
    msg_id = call.message.message_id
    user_id = call.from_user.id
    
    if msg_id not in giveaways_db:
        giveaways_db[msg_id] = {"participants": set()}
        
    participants = giveaways_db[msg_id]["participants"]
    
    if user_id in participants:
        await call.answer("شما قبلاً در این قرعه‌کشی ثبت‌نام کرده‌اید! ❌", show_alert=True)
    else:
        participants.add(user_id)
        count = len(participants)
        
        # آپدیت تعداد افراد روی دکمه
        new_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"🎁 شرکت در قرعه‌کشی ({count})", callback_data="join_giveaway")]
            ]
        )
        await call.message.edit_reply_markup(reply_markup=new_keyboard)
        await call.answer("شما با موفقیت وارد قرعه‌کشی شدید! 🎉", show_alert=True)

@dp.callback_query(F.data == "cancel_launch")
async def cancel_giveaway(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ ساخت قرعه‌کشی لغو شد.")

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
