# bot/bot.py
# ==========================
# Multi-bot stable launcher (aiogram v3)
# ==========================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# Загружаем токены
load_dotenv()
TOKENS: List[str] = []
for key in ("BOT_TOKEN", "BOT_TOKEN2"):
    t = os.getenv(key, "").strip()
    if t:
        TOKENS.append(t)

if not TOKENS:
    raise RuntimeError("Нет токенов: добавь BOT_TOKEN и BOT_TOKEN2 в Variables")

# Мини-обработчики
async def run_single_bot(token: str, name: str):
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(CommandStart())
    async def start_cmd(m: Message):
        await m.answer("Бот на связи ✅\nГотов проверить себя на различение?",
                       reply_markup=None)

    # тестовая команда для проверки уникальности
    @dp.message(F.text == "/who")
    async def whoami(m: Message):
        me = await bot.me()
        await m.answer(f"Я — @{me.username}")

    me = await bot.me()
    logging.info(f"Запуск бота @{me.username} (id={me.id}) — {name}")
    await dp.start_polling(bot)

async def main():
    # Запускаем все боты параллельно
    tasks = []
    for idx, t in enumerate(TOKENS, start=1):
        tasks.append(run_single_bot(t, f"Bot{idx}"))
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
