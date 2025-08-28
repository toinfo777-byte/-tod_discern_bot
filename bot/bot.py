# bot/bot.py
# =========================
# Multi-bot + no-popup-keyboard (aiogram v3.7+)
# =========================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from aiogram import F
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
)
from dotenv import load_dotenv

# ---------- логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

load_dotenv()

# ---------- глобальный Dispatcher (важно: объявлен ДО декораторов) ----------
dp = Dispatcher(storage=MemoryStorage())

# ---------- утилиты клавиатуры ----------
def build_inline_kb(labels: List[str], block: str = "opt") -> InlineKeyboardMarkup:
    """
    Только inline-кнопки (они не поднимают системную клавиатуру).
    """
    rows = [[InlineKeyboardButton(text=txt, callback_data=f"{block}:{i}")]
            for i, txt in enumerate(labels)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- демо-данные (можно заменить своими) ----------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str

DEMO_TASKS: List[Task] = [
    Task(id="A1", text="Исследование: зонт = причина дождя. Что это?",
         options=["Причина", "Следствие", "Корреляция"], answer="Корреляция"),
    Task(id="A2", text="Эксперт популярен, значит прав. Что это?",
         options=["Апелляция к авторитету", "Факт", "Аргумент"], answer="Апелляция к авторитету"),
]

# ---------- handlers ----------
@dp.message(CommandStart())
async def cmd_start(m: Message):
    # На всякий случай закрываем системную клавиатуру
    await m.answer("Бот на связи ✅", reply_markup=ReplyKeyboardRemove())

    # Кнопка "начать мини-тест"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Пройти мини-тест", callback_data="quiz:start")
    ]])
    await m.answer("Готов проверить себя на различение?", reply_markup=kb)


@dp.callback_query(F.data == "quiz:start")
async def cb_quiz_start(cq: CallbackQuery):
    # Показываем первый вопрос
    task = DEMO_TASKS[0]
    kb = build_inline_kb(task.options, block=f"q:{task.id}")
    await cq.message.answer(f"Задание {task.id}:\n{task.text}", reply_markup=kb)
    await cq.answer()


@dp.callback_query(F.data.startswith("q:"))
async def cb_quiz_answer(cq: CallbackQuery):
    """
    Обрабатываем ответ вида: q:<task_id>:<index>
    """
    parts = cq.data.split(":")
    # Форматы: ["q", "<task_id>", "<index>"] или ["q", "<task_id>","<index>"] – в зависимости от сборки
    if len(parts) == 2:
        # старый формат "q:<task_id>" (кнопка была нажата, но без индекса) – игнорируем
        await cq.answer()
        return
    _, task_id, idx_str = parts[0], parts[1], parts[2]

    task: Optional[Task] = next((t for t in DEMO_TASKS if t.id == task_id), None)
    if not task:
        await cq.answer()
        return

    try:
        idx = int(idx_str)
    except ValueError:
        await cq.answer()
        return

    choice = task.options[idx] if 0 <= idx < len(task.options) else ""
    is_correct = (choice.casefold() == task.answer.casefold())

    if is_correct:
        await cq.message.answer(
            f"✅ Верно! {task.answer}.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await cq.message.answer(
            f"❌ Неверно. Правильный ответ: <b>{task.answer}</b>.",
            reply_markup=ReplyKeyboardRemove()
        )

    # Следующий вопрос (просто для примера: показываем второй, если был первый)
    if task_id == "A1":
        nxt = DEMO_TASKS[1]
        kb = build_inline_kb(nxt.options, block=f"q:{nxt.id}")
        await cq.message.answer(f"Задание {nxt.id}:\n{nxt.text}", reply_markup=kb)

    await cq.answer()


# ---------- запуск одного бота ----------
async def run_single_bot(token: str):
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    # важно: на каждый ответ кладём ReplyKeyboardRemove(), inline-кнопки не поднимают клавиатуру
    await dp.start_polling(bot)


# ---------- main ----------
async def main():
    # собираем токены из ENV
    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = os.getenv(key, "").strip()
        if t:
            tokens.append(t)

    if not tokens:
        logging.error("Нет токенов: добавьте переменные окружения BOT_TOKEN и/или BOT_TOKEN2")
        raise SystemExit(1)

    masked = ["***" + t[-6:] for t in tokens]
    logging.info(f"Starting polling for {len(tokens)} bot(s): {masked}")

    # Запускаем оба бота параллельно с одним Dispatcher
    await asyncio.gather(*(run_single_bot(t) for t in tokens))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
