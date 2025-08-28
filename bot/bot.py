# bot/bot.py
# ===============================
# Multi-bot (aiogram v3) + inline-only UI (no system keyboard popup)
# ===============================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# ---------------- setup ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
load_dotenv()

# токены (1 или 2). Порядок важен — ниже им свяжем наборы вопросов
TOKENS: List[str] = []
for key in ("BOT_TOKEN", "BOT_TOKEN2"):
    t = os.getenv(key, "").strip()
    if t:
        TOKENS.append(t)

if not TOKENS:
    raise RuntimeError("Нет ни одного токена. Задайте BOT_TOKEN (и при желании BOT_TOKEN2).")

# Общее хранилище для всех ботов
storage = MemoryStorage()

# Версия сборки (для логов)
__BOT_VERSION__ = "kb-1.7-final"

# ---------------- данные ----------------
# Наборы задач лежат в отдельных файлах
from .tasks import TASKS_A
from .tasks_b import TASKS_B

@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int
    badge: Optional[str] = None
    explain: Optional[str] = None

# Привязка: какой бот обслуживает какой набор задач.
# 1-й токен → набор А, 2-й токен → набор B.
TASK_MAP: List[List[dict]] = [TASKS_A, TASKS_B]

# ---------------- утилиты ----------------
def build_inline(options: List[str]) -> InlineKeyboardMarkup:
    # Только inline-кнопки — системную клавиатуру не трогаем
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"opt:{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def build_cta() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти мини-тест", callback_data="cta:start")],
    ])

def build_again() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="cta:again")],
    ])

def normalize(s: str) -> str:
    return " ".join(s.split()).strip().casefold()

# ---------------- ядро бота ----------------
async def run_single_bot(token: str, tasks_pack: List[dict]) -> None:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=storage)

    # Локальные «константы» для данного инстанса
    TASKS: List[Task] = [Task(**t) for t in tasks_pack]
    TOTAL = len(TASKS)

    @dp.message(CommandStart())
    async def on_start(m: Message, state: FSMContext):
        await state.clear()
        await state.update_data(idx=0, correct=0, streak=0)
        await m.answer("Бот на связи ✅")
        await m.answer("Готов проверить себя на различение?", reply_markup=build_cta())

    @dp.callback_query(F.data == "cta:start")
    async def start_quiz(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await send_task(cb.message, state)

    @dp.callback_query(F.data == "cta:again")
    async def again(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        await state.clear()
        await state.update_data(idx=0, correct=0, streak=0)
        await send_task(cb.message, state)

    async def send_task(m: Message, state: FSMContext):
        data = await state.get_data()
        i = int(data.get("idx", 0))
        if i >= TOTAL:
            # Конец
            correct = int(data.get("correct", 0))
            await m.answer(
                f"Готово! Итог: <b>{correct}/{TOTAL}</b>\n\n"
                "Если понравилось — можно пройти ещё раз или позвать друга 😉",
                reply_markup=build_again()
            )
            return

        task = TASKS[i]
        kb = build_inline(task.options)
        await m.answer(f"Задание {i+1}/{TOTAL}:\n{task.text}", reply_markup=kb)

    @dp.callback_query(F.data.startswith("opt:"))
    async def answer_option(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        data = await state.get_data()
        i = int(data.get("idx", 0))
        if i >= TOTAL:
            await cb.message.answer("Тест уже завершён. Нажмите «Пройти ещё раз».", reply_markup=build_again())
            return

        task = TASKS[i]
        try:
            choice_index = int(cb.data.split(":")[1])
        except Exception:
            return

        chosen = task.options[choice_index] if 0 <= choice_index < len(task.options) else ""
        is_correct = normalize(chosen) == normalize(task.answer)

        # статистика
        correct = int(data.get("correct", 0)) + (1 if is_correct else 0)
        await state.update_data(correct=correct)

        if is_correct:
            msg = f"✅ Верно! {task.explain or ''}".strip()
        else:
            msg = f"❌ Неверно. Правильный ответ: <b>{task.answer}</b>.\n{task.explain or ''}".strip()

        await cb.message.answer(msg)

        # следующий вопрос
        await state.update_data(idx=i+1)
        await send_task(cb.message, state)

    # Старт поллинга
    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot)

# ---------------- entrypoint ----------------
async def main():
    # Под каждого токена подложим набор задач (А/B)
    tasks_for_bot = []
    for i, tok in enumerate(TOKENS):
        pack = TASK_MAP[i] if i < len(TASK_MAP) else TASKS_A
        tasks_for_bot.append((tok, pack))

    logging.info(f"Start polling for {len(tasks_for_bot)} bot(s): {[t[0][-10:] for t in tasks_for_bot]}")
    await asyncio.gather(*(run_single_bot(t, pack) for t, pack in tasks_for_bot))

if __name__ == "__main__":
    asyncio.run(main())
