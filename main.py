import asyncio
import os
import logging
from flask import Flask
from threading import Thread
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO)

# --- وب‌سرور برای حل مشکل پورت Render ---
app = Flask('')

@app.route('/')
def home():
    return "⚡ Void Giveaway Bot is online!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# --- تنظیمات ربات ---
TOKEN = os.environ.get("BOT_TOKEN", "8940706019:AAHEsHRP50Ryvpg8sLf2ovV7m6cBTTbXVtI")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# کیبورد جدید اختصاصی قرعه‌کشی
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎉 ساخت قرعه‌کشی جدید")],
        [KeyboardButton(text="⚙️ تنظیمات"), KeyboardButton(text="📊 قرعه‌کشی‌های من")]
    ],
    resize_keyboard=True
)

# هندلر دستور /start
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    welcome_text = (
        f"سلام {message.from_user.first_name} عزیز! 🎁\n\n"
        f"به ربات **Void Giveaway** خوش آمدید.\n"
        f"برای شروع و ساخت قرعه‌کشی جدید از دکمه زیر استفاده کنید 👇"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)

# هندلر کلیک روی ساخت قرعه‌کشی جدید
@dp.message(F.text == "🎉 ساخت قرعه‌کشی جدید")
@dp.message(F.text == "/newgiveaway")
async def new_giveaway_handler(message: types.Message):
    # منوی شیشه‌ای برای تنظیمات قرعه‌کشی
    giveaway_setup_menu = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 ۱. تنظیم عنوان و جایزه", callback_data="set_title")],
            [InlineKeyboardButton(text="⏳ ۲. تعیین زمان پایان", callback_data="set_time")],
            [InlineKeyboardButton(text="👥 ۳. تعداد برندگان", callback_data="set_winners")],
            [InlineKeyboardButton(text="📢 ۴. قفل جوین کانال (اجباری)", callback_data="set_channel")],
            [InlineKeyboardButton(text="🚀 انتشار قرعه‌کشی", callback_data="launch_giveaway")]
        ]
    )
    
    setup_text = (
        "⚙️ **پنل تنظیمات قرعه‌کشی جدید**\n\n"
        "لطفاً بخش‌های مورد نظر را تنظیم کنید و در نهایت روی **انتشار** بزنید:"
    )
    await message.answer(setup_text, parse_mode="Markdown", reply_markup=giveaway_setup_menu)

# هندلر تنظیمات عمومی
@dp.message(F.text == "⚙️ تنظیمات")
async def settings_handler(message: types.Message):
    await message.answer("🔧 این بخش برای تنظیمات کلی حساب و کانال‌های شماست.")

# هندلر قرعه‌کشی‌های من
@dp.message(F.text == "📊 قرعه‌کشی‌های من")
async def my_giveaways_handler(message: types.Message):
    await message.answer("📜 لیست قرعه‌کشی‌های فعال شما اینجا قرار می‌گیره.")

# پاسخ به کلیک روی دکمه‌های شیشه‌ای تنظیمات
@dp.callback_query(F.data.startswith("set_"))
async def setup_callbacks(call: types.CallbackQuery):
    action = call.data
    if action == "set_title":
        await call.message.answer("✏️ لطفاً **عنوان قرعه‌کشی** و توضیحات جایزه را ارسال کنید:")
    elif action == "set_time":
        await call.message.answer("⏱ مدت زمان قرعه‌کشی را مشخص کنید (مثلاً: 24 ساعت):")
    elif action == "set_winners":
        await call.message.answer("🔢 تعداد برندگان را وارد کنید (مثلاً: 1 یا 5):")
    elif action == "set_channel":
        await call.message.answer("📢 آیدی یا لینک کانالی که کاربر باید عضو شود را بفرستید:")
    
    await call.answer()

@dp.callback_query(F.data == "launch_giveaway")
async def launch_callback(call: types.CallbackQuery):
    await call.answer("🚀 قرعه‌کشی شما با موفقیت آماده و منتشر شد!", show_alert=True)

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
