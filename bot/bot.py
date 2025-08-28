# bot/bot.py
# ========= Minimal multi-bot starter (aiogram v3) =========
import os
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import F
from aiogram.types import Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

async def run_single_bot(token: str):
    bot = Bot(token=token, parse_mode="HTML")
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(CommandStart())
    async def start(m: Message):
        await m.answer("Бот на связи ✅")

    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot)

async def main():
    # Читаем оба токена, убираем пустые
    tokens = [os.getenv("BOT_TOKEN", "").strip(), os.getenv("BOT_TOKEN2", "").strip()]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise RuntimeError("Нет ни одного токена. Задай BOT_TOKEN и/или BOT_TOKEN2 в Variables.")

    # Запускаем всех параллельно
    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    asyncio.run(main())
