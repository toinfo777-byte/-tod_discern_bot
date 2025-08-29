# bot/bot.py
# ===============================
# Multi-bot + уровни (A/B/HARD)  — aiogram v3
# ===============================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from dotenv import load_dotenv

# ---------- импорт пулов вопросов ----------
# tasks.py — базовый, tasks_b.py — продвинутый, tasks_hard.py — хард
def _import_tasks_module(path: str):
    # импорт с префиксом bot. (когда запускаем через python -m)
    try:
        mod = __import__(f"bot.{path}", fromlist=["*"])
        return mod
    except Exception:
        # локальный импорт (на всякий)
        return __import__(path, fromlist=["*"])

def _resolve_tasks_var(mod, names: List[str]):
    for name in names:
        if hasattr(mod, name):
            return getattr(mod, name)
    raise ImportError(f"В модуле {mod.__name__} не найдено ни одной из переменных: {', '.join(names)}")


_m_a = _import_tasks_module("tasks")
_m_b = _import_tasks_module("tasks_b")
# модуль может отсутствовать — обернём в try
try:
    _m_h = _import_tasks_module("tasks_hard")
except Exception:
    _m_h = None

TASKS_A_RAW = _resolve_tasks_var(_m_a, ["TASKS_A", "TASKS"])
TASKS_B_RAW = _resolve_tasks_var(_m_b, ["TASKS_B", "TASKS"])
TASKS_HARD_RAW = _resolve_tasks_var(_m_h, ["TASKS_HARD", "TASKS"]) if _m_h else []

# ---------- модель вопроса ----------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int = 10
    badge: Optional[str] = None
    explain: Optional[str] = None

def _normalize_pool(raw: List[Dict]) -> List[Task]:
    out: List[Task] = []
    for it in raw:
        out.append(Task(
            id=str(it.get("id", "")),
            text=str(it.get("text", "")),
            options=list(it.get("options", [])),
            answer=str(it.get("answer", "")),
            xp=int(it.get("xp", 10)),
            badge=it.get("badge"),
            explain=it.get("explain"),
        ))
    return out

POOL_A: List[Task] = _normalize_pool(TASKS_A_RAW)
POOL_B: List[Task] = _normalize_pool(TASKS_B_RAW)
POOL_H: List[Task] = _normalize_pool(TASKS_HARD_RAW)

# ---------- общие константы ----------
LEVELS = ("A", "B", "HARD")
LEVEL_LABELS = {"A": "Базовый", "B": "Продвинутый", "HARD": "Хард"}

def get_pool(level: str) -> List[Task]:
    if level == "A":
        return POOL_A
    if level == "B":
        return POOL_B
    return POOL_H

# ---------- клавиатуры ----------
def kb_levels() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=f"Уровень A — {LEVEL_LABELS['A']}", callback_data="level:A"),
        ],
        [
            InlineKeyboardButton(text=f"Уровень B — {LEVEL_LABELS['B']}", callback_data="level:B"),
        ],
        [
            InlineKeyboardButton(text=f"Уровень HARD — {LEVEL_LABELS['HARD']}", callback_data="level:HARD"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_options(opts: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"opt:{opt}") ] for opt in opts]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="retry")],
        [InlineKeyboardButton(text="Сменить уровень", callback_data="levels")],
    ])

# ---------- портрет по итогам ----------
def make_portrait(score: int, total: int) -> str:
    if score <= 3:
        return "Ты пока легко доверяешь словам без проверки. Немного практики — и будет прогресс."
    if score <= 6:
        return "Ты часто замечаешь уловки, но на сложных приёмах иногда спотыкаешься."
    if score <= 9:
        return "Хорошее различение! Иногда можно ловиться на тонкие манипуляции — продолжай тренироваться."
    return "Мастер различения 💡. Фейки тебе не страшны."

# ---------- состояние (в памяти) ----------
# В state кладём: level (A/B/HARD), idx, score
async def start_quiz(m: Message, state: FSMContext, level: str):
    pool = get_pool(level)
    await state.update_data(level=level, idx=0, score=0)
    if not pool:
        await m.answer("Для этого уровня пока нет вопросов. Выбери другой:", reply_markup=kb_levels())
        return
    task = pool[0]
    await m.answer("Готов проверить себя на различение?\n\n"
                   f"Текущий уровень: {LEVEL_LABELS[level]}\n"
                   "Можно сменить уровень в любой момент.",
                   reply_markup=kb_levels())
    await m.answer(f"Задание 1/{len(pool)}:\n{task.text}", reply_markup=kb_options(task.options))

async def ask_next(m_or_cq, state: FSMContext):
    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("idx", 0))
    score = int(data.get("score", 0))
    pool = get_pool(level)

    # если закончили
    if idx >= len(pool):
        portrait = make_portrait(score, len(pool))
        await (m_or_cq.message.answer if isinstance(m_or_cq, CallbackQuery) else m_or_cq.answer)(
            f"Готово! Итог: {score}/{len(pool)}\n\n{portrait}",
            reply_markup=kb_retry()
        )
        return

    task = pool[idx]
    sender = m_or_cq.message.answer if isinstance(m_or_cq, CallbackQuery) else m_or_cq.answer
    await sender(f"Задание {idx+1}/{len(pool)}:\n{task.text}", reply_markup=kb_options(task.options))

# ---------- обработчики ----------
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    # по умолчанию — уровень A
    await start_quiz(m, state, "A")

@dp.callback_query(F.data == "levels")
async def show_levels(cq: CallbackQuery):
    await cq.message.answer("Выбери уровень:", reply_markup=kb_levels())
    await cq.answer()

@dp.callback_query(F.data.startswith("level:"))
async def change_level(cq: CallbackQuery, state: FSMContext):
    level = cq.data.split(":", 1)[1]
    if level not in LEVELS:
        await cq.answer("Неизвестный уровень", show_alert=True)
        return
    # запускаем заново с нового уровня
    await state.clear()
    await start_quiz(cq.message, state, level)
    await cq.answer(f"Уровень: {LEVEL_LABELS[level]}")

@dp.callback_query(F.data == "retry")
async def retry_test(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    level = data.get("level", "A")
    await state.clear()
    await start_quiz(cq.message, state, level)
    await cq.answer("Поехали ещё раз!")

@dp.callback_query(F.data.startswith("opt:"))
async def answer_option(cq: CallbackQuery, state: FSMContext):
    chosen = cq.data.split(":", 1)[1]
    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("idx", 0))
    score = int(data.get("score", 0))

    pool = get_pool(level)
    if idx >= len(pool):
        await cq.answer("Тест уже завершён.")
        return

    task = pool[idx]
    is_correct = (chosen.strip().casefold() == task.answer.strip().casefold())

    if is_correct:
        score += 1
        msg = f"✅ Верно! Правильный ответ: {task.answer}."
    else:
        msg = f"❌ Неверно. Правильный ответ: {task.answer}."

    if task.explain:
        msg += f"\n{task.explain}"

    await cq.message.answer(msg)
    # следующий
    idx += 1
    await state.update_data(idx=idx, score=score)
    await cq.answer()
    await ask_next(cq, state)

# ---------- запуск нескольких ботов ----------
async def run_single_bot(token: str):
    bot = Bot(token=token)  # без DefaultBotProperties — совместимо со старыми aiogram v3
    await bot.delete_webhook(drop_pending_updates=True)
    logging.info(f"Starting polling for bot token ***{token[-6:]}")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    load_dotenv()

    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = os.getenv(key, "").strip()
        if t:
            tokens.append(t)

    if not tokens:
        raise RuntimeError("Нет токенов. Добавьте BOT_TOKEN (и при желании BOT_TOKEN2) в Variables.")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    asyncio.run(main())
