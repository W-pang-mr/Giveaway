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
    return "⚡ Planet Bot is online and healthy!"

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

# کیبورد اصلی ربات
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚀 وضعیت ربات"), KeyboardButton(text="📋 راهنما")],
        [KeyboardButton(text="📞 ارتباط با ما")]
    ],
    resize_keyboard=True
)

# دکمه‌های شیشه‌ای (Inline)
inline_menu = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🌐 وب‌سایت", url="https://render.com")],
        [InlineKeyboardButton(text="✨ حمایت از ما", callback_data="support")]
    ]
)

# هندلر دستور /start
@dp.message(CommandStart())
async def start_handler(message: types.Message):
    user_name = message.from_user.first_name
    welcome_text = (
        f"سلام {user_name} عزیز! 👋\n\n"
        f"🌟 به **Planet Bot** خوش آمدید.\n"
        f"ربات با موفقیت فعال شد و آماده خدمت‌رسانی است.\n\n"
        f"از منوی زیر می‌توانید بخش مورد نظر خود را انتخاب کنید 👇"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=main_keyboard)

# هندلر دکمه وضعیت
@dp.message(F.text == "🚀 وضعیت ربات")
async def status_handler(message: types.Message):
    status_text = (
        "🟢 **وضعیت سیستم:**\n\n"
        "• سرور: Render (Cloud)\n"
        "• وضعیت ربات: آنلاین ⚡\n"
        "• سرعت پاسخ‌گویی: عالی 🚀"
    )
    await message.answer(status_text, parse_mode="Markdown", reply_markup=inline_menu)

# هندلر دکمه راهنما
@dp.message(F.text == "📋 راهنما")
async def help_handler(message: types.Message):
    help_text = (
        "📚 **راهنمای استفاده:**\n\n"
        "برای کار با ربات می‌توانید از دکمه‌های پایین صفحه استفاده کنید.\n"
        "در صورت بروز هرگونه مشکل، از بخش ارتباط با ما استفاده کنید."
    )
    await message.answer(help_text, parse_mode="Markdown")

# هندلر دکمه ارتباط با ما
@dp.message(F.text == "📞 ارتباط با ما")
async def contact_handler(message: types.Message):
    await message.answer("📬 برای ارتباط با پشتیبانی می‌توانید پیام خود را همینجا ارسال کنید.")

# هندلر کلیک روی دکمه شیشه‌ای حمایت
@dp.callback_query(F.data == "support")
async def support_callback(call: types.CallbackQuery):
    await call.answer("ممنون از حمایت شما! ❤️", show_alert=True)

async def main():
    keep_alive()
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
