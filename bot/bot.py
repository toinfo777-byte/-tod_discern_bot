# bot/bot.py
# ======== Minimal multi-bot starter (aiogram v3.7+) ========
import os
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram import F
from aiogram.client.default import DefaultBotProperties  # <-- важно!

# ---- демо-задание ----
TASK = {
    "text": "Читающие чаще носят очки. Очки улучшают интеллект. Что это?",
    "options": ["Причина", "Следствие", "Корреляция"],
    "answer": "Корреляция",
}

def build_inline(options: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"opt:{i}")] 
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

async def run_single_bot(token: str):
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML")  # <-- вместо parse_mode="HTML"
    )
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(CommandStart())
    async def start(m: Message):
        await m.answer("Бот на связи ✅")

    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot)

@dp.message(F.text == "/quiz")
async def quiz_handler(m: Message):
    await m.answer(
        TASK["text"], 
        reply_markup=build_inline(TASK["options"])
    )

@dp.callback_query(F.data.startswith("opt:"))
async def answer_handler(cq):
    idx = int(cq.data.split(":")[1])
    chosen = TASK["options"][idx]
    correct = TASK["answer"]
    if chosen == correct:
        await cq.message.edit_text("✅ Верно: " + chosen)
    else:
        await cq.message.edit_text("❌ Неверно. Правильный ответ: " + correct)


async def main():
    tokens = [os.getenv("BOT_TOKEN", "").strip(), os.getenv("BOT_TOKEN2", "").strip()]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise RuntimeError("Нет ни одного токена. Задай BOT_TOKEN и/или BOT_TOKEN2 в Variables.")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    asyncio.run(main())
