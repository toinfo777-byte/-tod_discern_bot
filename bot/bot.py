# bot/bot.py
# ======================================
# Multi-bot + уровни A/B/HARD (aiogram v3)
# ======================================

import os
import asyncio
import logging
from typing import List, Dict, Tuple, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from dotenv import load_dotenv

# ----------------- ЛОГИ -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
load_dotenv()

# ------------- ПОДГРУЗКА ВОПРОСОВ -------------
def _safe_import(module_path: str):
    """
    Импортирует модуль, если он существует в проекте.
    Возвращает модуль либо None.
    """
    try:
        import importlib
        return importlib.import_module(module_path)
    except Exception:
        return None


def _resolve_tasks_var(mod, candidates: List[str]) -> List[dict]:
    """
    В модуле пытается найти первую попавшуюся переменную из списка candidates.
    Возвращает список задач (list[dict]) или пустой список.
    """
    if not mod:
        return []
    for name in candidates:
        val = getattr(mod, name, None)
        if isinstance(val, list):
            return val
    return []


# Ожидаем, что в файлах могут быть:
# tasks.py      -> TASKS или TASKS_A
# tasks_b.py    -> TASKS или TASKS_B
# tasks_hard.py -> TASKS или TASKS_HARD
_m_a = _safe_import("bot.tasks")
_m_b = _safe_import("bot.tasks_b")
_m_h = _safe_import("bot.tasks_hard")

TASKS_A_RAW: List[dict] = _resolve_tasks_var(_m_a, ["TASKS_A", "TASKS"])
TASKS_B_RAW: List[dict] = _resolve_tasks_var(_m_b, ["TASKS_B", "TASKS"])
TASKS_HARD_RAW: List[dict] = _resolve_tasks_var(_m_h, ["TASKS_HARD", "TASKS"])

# Нормализатор опций и ответов (в нижний регистр, без хвостовых пробелов)
def _norm(s: str) -> str:
    return (s or "").strip().casefold()


# ------------- НАСТРОЙКИ ПО УМОЛЧАНИЮ -------------
# Политика уровней по bot.id (замените id при необходимости)
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    # @tod_discern_bot
    8222973157: {"default": "A", "allowed": {"A", "B", "HARD"}},
    # @discernment_test_bot
    8416181261: {"default": "B", "allowed": {"B", "HARD"}},
}
ALL_LEVELS = ("A", "B", "HARD")


# ------------- УТИЛИТЫ КЛАВИАТУР -------------
def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def level_picker_kb(allowed: Optional[set] = None) -> InlineKeyboardMarkup:
    allowed = allowed or set(ALL_LEVELS)
    rows = []
    if "A" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень A", callback_data="set_level:A")])
    if "B" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень B", callback_data="set_level:B")])
    if "HARD" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень HARD", callback_data="set_level:HARD")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def post_finish_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пройти ещё раз", callback_data="restart")],
            [InlineKeyboardButton(text="Сменить уровень", callback_data="change_level")],
        ]
    )


# ------------- РАБОТА С СОСТОЯНИЕМ -------------
async def get_policy_for(bot: Bot) -> Dict[str, object]:
    me = await bot.get_me()
    return BOT_LEVEL_POLICY.get(me.id, {"default": "A", "allowed": set(ALL_LEVELS)})


def pick_pool(level: str) -> List[dict]:
    level = (level or "A").upper()
    if level == "A":
        return TASKS_A_RAW
    if level == "B":
        return TASKS_B_RAW
    if level == "HARD":
        return TASKS_HARD_RAW
    return TASKS_A_RAW


async def send_intro(m: Message, state: FSMContext):
    policy = await get_policy_for(m.bot)
    allowed = policy["allowed"]
    text = (
        "Готов проверить себя на различение?\n\n"
        "Доступные уровни: A (базовый), B (продвинутый), HARD (хард).\n"
        "Сменить уровень — кнопкой внизу «Сменить уровень» "
        "или командой /level A, /level B, /level HARD."
    )
    await m.answer(text)
    # сразу отправим первый вопрос текущего уровня
    await send_task(m, state)


async def send_task(m: Message, state: FSMContext):
    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("task_index", 0))
    score = int(data.get("score", 0))

    pool = pick_pool(level)
    total = len(pool)

    if total == 0:
        await m.answer("Для этого уровня нет вопросов. Выберите другой уровень:", reply_markup=level_picker_kb())
        return

    if idx >= total:
        # финал
        msg = (
            f"Готово! Итог: {score}/{total}\n\n"
            "Хорошее различение! Иногда можно ловиться на тонкие манипуляции — "
            "продолжай тренироваться."
        )
        await m.answer(msg, reply_markup=post_finish_kb())
        return

    task = pool[idx]
    text = task.get("text", "Задание")
    options = task.get("options", [])

    await state.update_data(task_index=idx, score=score, level=level)

    await m.answer(f"Задание {idx+1}/{total}:\n{text}", reply_markup=answers_kb(options))


def is_correct(task: dict, opt_text: str) -> bool:
    # Считаем верным, если текст совпал с task["answer"] (без учёта регистра/пробелов)
    ans = _norm(task.get("answer", ""))
    return _norm(opt_text) == ans


# ------------- КОМАНДЫ -------------
dp = Dispatcher(storage=MemoryStorage())


@dp.message(F.text == "/start")
async def cmd_start(m: Message, state: FSMContext):
    policy = await get_policy_for(m.bot)
    default_level = policy["default"]
    await state.clear()
    await state.update_data(level=default_level, task_index=0, score=0)
    await send_intro(m, state)


@dp.message(F.text.regexp(r"^/level(\s+.*)?$"))
async def cmd_level(m: Message, state: FSMContext):
    policy = await get_policy_for(m.bot)
    allowed = policy["allowed"]

    parts = (m.text or "").strip().split()
    selected = parts[1].upper() if len(parts) > 1 else None

    if selected not in {"A", "B", "HARD"} or selected not in allowed:
        await m.answer("Выбери уровень:", reply_markup=level_picker_kb(set(allowed)))
        return

    await switch_level_and_restart(m, state, selected)


async def switch_level_and_restart(msg: Message, state: FSMContext, level: str):
    await state.update_data(level=level, task_index=0, score=0)
    await msg.answer(f"Уровень переключён на {level}.")
    await send_task(msg, state)


# ------------- CALLBACK'И -------------
@dp.callback_query(F.data.startswith("ans:"))
async def on_answer(cb: CallbackQuery, state: FSMContext):
    await cb.answer()  # закрыть "часики"
    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("task_index", 0))
    score = int(data.get("score", 0))

    pool = pick_pool(level)
    if idx >= len(pool):
        await cb.message.answer("Тест уже завершён.", reply_markup=post_finish_kb())
        return

    task = pool[idx]
    # По индексу из callback достаём текст опции:
    try:
        opt_idx = int(cb.data.split(":")[1])
        opt_text = task.get("options", [])[opt_idx]
    except Exception:
        opt_text = ""

    correct = is_correct(task, opt_text)
    if correct:
        score += 1
        explain = task.get("explain", "Верно!")
        await cb.message.answer(f"✅ Верно! {('Правильный ответ: ' + task.get('answer',''))}\n{explain}")
    else:
        explain = task.get("explain", "")
        await cb.message.answer(
            f"❌ Неверно. Правильный ответ: {task.get('answer','')}.\n{explain}"
        )

    # следующий
    idx += 1
    await state.update_data(task_index=idx, score=score)
    await send_task(cb.message, state)


@dp.callback_query(F.data == "restart")
async def on_restart(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    data = await state.get_data()
    level = data.get("level", "A")
    await state.update_data(task_index=0, score=0, level=level)
    await send_task(cb.message, state)


@dp.callback_query(F.data == "change_level")
async def on_change_level(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    policy = await get_policy_for(cb.message.bot)
    allowed = policy["allowed"]
    await cb.message.answer("Выбери уровень:", reply_markup=level_picker_kb(set(allowed)))


@dp.callback_query(F.data.startswith("set_level:"))
async def on_set_level(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    level = cb.data.split(":", 1)[1]
    await switch_level_and_restart(cb.message, state, level)


# ------------- ЗАПУСК НЕСКОЛЬКИХ БОТОВ -------------
async def run_single_bot(token: str):
    bot = Bot(token=token)  # без parse_mode по умолчанию (совместимо с 3.7+)
    logging.info("Starting polling for bot…")
    await dp.start_polling(bot)


async def main():
    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = (os.getenv(key, "") or "").strip()
        if t:
            tokens.append(t)

    if not tokens:
        raise RuntimeError("Нет токенов. Добавьте переменные окружения BOT_TOKEN (и при желании BOT_TOKEN2).")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
