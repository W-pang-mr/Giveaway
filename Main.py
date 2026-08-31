import os
import random
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ذخیره شرکت‌کنندگان در حافظه
participants = []

class GiveawayForm(StatesGroup):
    target_channel = State()
    description = State()
    required_channels = State()
    duration = State()

# شروع ساخت قرعه‌کشی (فقط ادمین)
@dp.message(Command("new_giveaway"))
async def start_giveaway(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("۱. آیدی کانالی که قرعه‌کشی توش قرار می‌گیره رو بفرست (مثلاً @MyChannel):")
    await state.set_state(GiveawayForm.target_channel)

@dp.message(GiveawayForm.target_channel)
async def set_channel(message: types.Message, state: FSMContext):
    await state.update_data(target_channel=message.text.strip())
    await message.answer("۲. توضیحات و جوایز قرعه‌کشی رو بنویس:")
    await state.set_state(GiveawayForm.description)

@dp.message(GiveawayForm.description)
async def set_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("۳. آیدی کانال‌های جوین اجباری رو با فاصله بفرست (مثلاً: @chan1 @chan2):")
    await state.set_state(GiveawayForm.required_channels)

@dp.message(GiveawayForm.required_channels)
async def set_req_channels(message: types.Message, state: FSMContext):
    channels = message.text.strip().split()
    await state.update_data(required_channels=channels)
    
    # دکمه‌های تایمر سریع
    timer_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⏱ ۵ دقیقه", callback_data="time_5m"),
            InlineKeyboardButton(text="⏱ ۱۰ دقیقه", callback_data="time_10m")
        ],
        [
            InlineKeyboardButton(text="⏱ ۱ ساعت", callback_data="time_1h"),
            InlineKeyboardButton(text="⏱ ۲ ساعت", callback_data="time_2h"),
            InlineKeyboardButton(text="⏱ ۵ ساعت", callback_data="time_5h")
        ]
    ])
    
    await message.answer("۴. زمان پایان قرعه‌کشی رو انتخاب کن:", reply_markup=timer_kb)
    await state.set_state(GiveawayForm.duration)

# تایید زمان، ارسال پست به کانال و زمان‌بندی قرعه‌کشی
@dp.callback_query(GiveawayForm.duration, F.data.startswith("time_"))
async def publish_giveaway(callback: types.CallbackQuery, state: FSMContext):
    time_key = callback.data.replace("time_", "")
    minutes_map = {"5m": 5, "10m": 10, "1h": 60, "2h": 120, "5h": 300}
    duration_minutes = minutes_map.get(time_key, 5)
    
    data = await state.get_data()
    req_channels = data['required_channels']
    target_channel = data['target_channel']
    
    req_channels_str = "\n".join(req_channels)
    post_text = (
        f"🎉 **قرعه‌کشی جدید!**\n\n"
        f"📝 **توضیحات و جوایز:**\n{data['description']}\n\n"
        f"📢 **شرط شرکت (جوین در کانال‌های زیر):**\n{req_channels_str}\n\n"
        f"⏳ **زمان باقی‌مانده:** {duration_minutes} دقیقه"
    )
    
    join_btn = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 شرکت در قرعه‌کشی", callback_data=f"enter_{','.join(req_channels)}")]
    ])
    
    try:
        sent_msg = await bot.send_message(chat_id=target_channel, text=post_text, reply_markup=join_btn, parse_mode="Markdown")
        await callback.message.edit_text("✅ پست قرعه‌کشی در کانال منتشر شد و تایمر فعال گردید!")
        await state.clear()
        
        # اجرا تایمر در پس‌زمینه برای قرعه‌کشی خودکار
        asyncio.create_task(run_giveaway_timer(duration_minutes * 60, target_channel, sent_msg.message_id))
    except Exception as e:
        await callback.message.edit_text(f"❌ خطا! مطمئن شو ربات توی کانال ادمینه:\n{e}")

# کلیک روی دکمه شرکت و بررسی عضویت در کانال‌ها
@dp.callback_query(F.data.startswith("enter_"))
async def enter_giveaway(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    req_channels = callback.data.replace("enter_", "").split(",")
    
    # بررسی عضویت کاربر در تک‌تک کانال‌ها
    for ch in req_channels:
        try:
            member = await bot.get_chat_member(chat_id=ch.strip(), user_id=user_id)
            if member.status in ["left", "kicked"]:
                await callback.answer(f"❌ شما هنوز عضو کانال {ch} نشده‌اید!", show_alert=True)
                return
        except Exception:
            await callback.answer(f"❌ ربات نتوانست عضویت شما را در {ch} بررسی کند. (آیا ربات در آنجا ادمین است؟)", show_alert=True)
            return

    if user_id in participants:
        await callback.answer("شما قبلاً در این قرعه‌کشی ثبت‌نام کرده‌اید! 😉", show_alert=True)
    else:
        participants.append(user_id)
        await callback.answer("✅ ثبت‌نام شما با موفقیت انجام شد!", show_alert=True)

# تابع زمان‌بندی و انتخاب برنده
async def run_giveaway_timer(seconds, channel_id, message_id):
    await asyncio.sleep(seconds)
    
    if not participants:
        await bot.send_message(chat_id=channel_id, reply_to_message_id=message_id, text="⏳ زمان قرعه‌کشی تمام شد ولی کسی شرکت نکرد! ❌")
        return

    winner_id = random.choice(participants)
    try:
        winner_user = await bot.get_chat(winner_id)
        winner_name = winner_user.first_name
        winner_mention = f"[{winner_name}](tg://user?id={winner_id})"
    except Exception:
        winner_mention = f"کاربر با آیدی `{winner_id}`"

    await bot.send_message(
        chat_id=channel_id,
        reply_to_message_id=message_id,
        text=f"🎉 **قرعه‌کشی به پایان رسید!**\n\n🏆 **برنده خوش‌شانس:** {winner_mention}\nمبارکت باشه! 🥳",
        parse_mode="Markdown"
    )
    participants.clear()

async def main():
    print("ربات قرعه‌کشی روشن شد...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
