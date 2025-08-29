# bot/bot.py
# ==============================
# Multi-bot (A/B/HARD) — aiogram v3
# ==============================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from collections import Counter

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from dotenv import load_dotenv


# ---------- загрузка пулов вопросов ----------
# tasks.py    -> базовый (A)
# tasks_b.py  -> продвинутый (B)
# tasks_hard.py -> HARD
def _try_import(module_name: str):
    try:
        return __import__(module_name, fromlist=["*"])
    except Exception:
        return None


def _resolve_tasks_var(mod, names: List[str]) -> List[dict]:
    """Достаём TASKS / TASKS_A / TASKS_B / TASKS_HARD из модуля."""
    for n in names:
        if mod and hasattr(mod, n):
            v = getattr(mod, n)
            if isinstance(v, list):
                return v
    return []


_m_a = _try_import("bot.tasks") or _try_import("tasks")
_m_b = _try_import("bot.tasks_b") or _try_import("tasks_b")
_m_h = _try_import("bot.tasks_hard") or _try_import("tasks_hard")

TASKS_A: List[dict] = _resolve_tasks_var(_m_a, ["TASKS_A", "TASKS"])
TASKS_B: List[dict] = _resolve_tasks_var(_m_b, ["TASKS_B", "TASKS"])
TASKS_HARD: List[dict] = _resolve_tasks_var(_m_h, ["TASKS_HARD", "TASKS"])

LEVEL_POOLS: Dict[str, List[dict]] = {
    "A": TASKS_A,
    "B": TASKS_B,
    "HARD": TASKS_HARD,
}

ALL_LEVELS = ("A", "B", "HARD")


# ---------- утилиты ----------
def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def again_or_level_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="again")],
        [InlineKeyboardButton(text="Сменить уровень", callback_data="picklevel")],
    ])


def level_picker_kb(allowed: Optional[set] = None) -> InlineKeyboardMarkup:
    allowed = allowed or set(ALL_LEVELS)
    buttons = []
    if "A" in allowed:
        buttons.append([InlineKeyboardButton(text="Уровень A", callback_data="lvl:A")])
    if "B" in allowed:
        buttons.append([InlineKeyboardButton(text="Уровень B", callback_data="lvl:B")])
    if "HARD" in allowed:
        buttons.append([InlineKeyboardButton(text="Уровень HARD", callback_data="lvl:HARD")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------- «портрет» для HARD ----------
WEAK_HINTS = {
    "Корреляция": "Проверь: нет ли общей причины или случайного совпадения?",
    "Обратная причинность": "Убедись в направлении: причина и следствие не перепутаны?",
    "Выживший набор": "Посмотри на невидимую часть выборки: где провалы/ошибки?",
    "Скользкая дорожка": "Требуй промежуточные звенья и вероятности.",
    "Композиция": "Свойства целого не переносятся автоматически на части (и наоборот).",
    "Post hoc": "После ≠ из-за. Нужен контроль альтернатив.",
    "Ложная единственная причина": "Редко бывает одна причина — проверь другие факторы.",
    "Апелляция к большинству": "Популярность ≠ истина. Ищи метод и данные.",
    "Апелляция к авторитету": "Авторитет помогает, но проси доказательства.",
    "Ане́кдот": "Один случай — не статистика. Нужны системные данные.",
}


def build_hard_summary(passed: List[dict], wrong: List[dict]) -> str:
    if not passed and not wrong:
        return "Пока нет материала для анализа. Попробуй ещё раз на HARD."

    wrong_labels = [t.get("answer", "").strip() for t in wrong if t.get("answer")]
    top = Counter(wrong_labels).most_common(2)

    lines = []
    if top:
        lines.append("🔎 **Где чаще промах:**")
        for label, cnt in top:
            hint = WEAK_HINTS.get(label, "Разверни рассуждение по шагам и проверь данные.")
            lines.append(f"• {label} — {cnt} раз(а). {hint}")
        lines.append("")

    lines.append("💡 Советы:\n"
                 "— Замедляйся на причинности и выборках.\n"
                 "— Ищи альтернативные объяснения и отсутствующие данные.\n"
                 "— Проси метод/доказательства, а не статус/популярность.")
    return "\n".join(lines)


# ---------- политика уровней по bot_id ----------
# замени id на свои при необходимости
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    # @tod_discern_bot
    8222973157: {"default": "A", "allowed": {"A", "B", "HARD"}},
    # @discernment_test_bot
    8416181261: {"default": "B", "allowed": {"B", "HARD"}},
}


# ---------- старт/подача вопросов ----------
async def present_task(m: Message | CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = data.get("idx", 0)
    tasks = data.get("tasks", [])
    total = len(tasks)

    if idx >= total:
        # завершение
        score = data.get("score", 0)
        level = data.get("level", "A")
        msg = f"Готово! Итог: {score}/{total}\n"

        # добавим «портрет» для HARD
        if level == "HARD":
            passed = data.get("answered_ok", [])
            wrong = data.get("answered_err", [])
            msg += "\n" + build_hard_summary(passed, wrong)

        if isinstance(m, CallbackQuery):
            await m.message.answer(msg, reply_markup=again_or_level_kb())
        else:
            await m.answer(msg, reply_markup=again_or_level_kb())
        await state.clear()
        return

    task = tasks[idx]
    txt = f"Задание {idx + 1}/{total}:\n«{task['text']}»\nЧто это?"
    kb = answers_kb(task["options"])

    if isinstance(m, CallbackQuery):
        await m.message.answer(txt, reply_markup=kb)
    else:
        await m.answer(txt, reply_markup=kb)


async def start_quiz(message: Message, state: FSMContext, bot_id: int, level: Optional[str] = None):
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    allowed = set(policy.get("allowed", set(ALL_LEVELS)))
    level = (level or policy.get("default", "A")).upper()

    if level not in allowed or level not in LEVEL_POOLS or not LEVEL_POOLS[level]:
        # нет вопросов для этого уровня — предложим выбрать
        await message.answer(
            "Для этого уровня нет вопросов. Выбери уровень:",
            reply_markup=level_picker_kb(allowed),
        )
        await state.clear()
        return

    await state.set_data({
        "level": level,
        "tasks": LEVEL_POOLS[level][:],  # копия
        "idx": 0,
        "score": 0,
        "answered_ok": [],
        "answered_err": [],
    })

    intro = (
        "Готов проверить себя на различение?\n\n"
        "Доступные уровни: A (базовый), B (продвинутый), HARD (хард).\n"
        "Сменить уровень — кнопкой **Сменить уровень** или командами `/level A`, `/level B`, `/level HARD`."
    )
    await message.answer(intro, parse_mode=None)
    await present_task(message, state)


# ---------- хендлеры ----------
async def on_start(message: Message, state: FSMContext):
    bot_id = message.bot.id
    await start_quiz(message, state, bot_id)


async def cmd_level(message: Message, state: FSMContext):
    bot_id = message.bot.id
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    allowed = set(policy.get("allowed", set(ALL_LEVELS)))

    # /level [A|B|HARD]
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        lvl = parts[1].strip().upper()
        if lvl in allowed and lvl in LEVEL_POOLS and LEVEL_POOLS[lvl]:
            await message.answer(f"Уровень переключён на {lvl}.")
            await start_quiz(message, state, bot_id, level=lvl)
            return
        else:
            await message.answer("Для этого уровня нет вопросов. Выбери из доступных:")
    else:
        await message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed))


async def cb_pick_level(call: CallbackQuery, state: FSMContext):
    bot_id = call.bot.id
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    allowed = set(policy.get("allowed", set(ALL_LEVELS)))

    lvl = call.data.split(":", 1)[1]
    if lvl in allowed and lvl in LEVEL_POOLS and LEVEL_POOLS[lvl]:
        await call.message.answer(f"Уровень переключён на {lvl}.")
        await start_quiz(call.message, state, bot_id, level=lvl)
    else:
        await call.message.answer("Для этого уровня нет вопросов. Выбери из доступных:",
                                  reply_markup=level_picker_kb(allowed))
    await call.answer()


async def cb_again(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lvl = data.get("level", "A")
    await state.clear()
    await call.answer()
    # стартуем заново на том же уровне
    await start_quiz(call.message, state, call.bot.id, level=lvl)


async def cb_picklevel_button(call: CallbackQuery, state: FSMContext):
    bot_id = call.bot.id
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    allowed = set(policy.get("allowed", set(ALL_LEVELS)))
    await call.message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed))
    await call.answer()


async def cb_answer(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    idx = data.get("idx", 0)
    tasks = data.get("tasks", [])
    if idx >= len(tasks):
        await call.answer()
        return

    task = tasks[idx]
    answer_i = int(call.data.split(":", 1)[1])
    picked = task["options"][answer_i]
    correct = _norm(picked) == _norm(task["answer"])

    # копим статистику для «портрета»
    ok_list = data.get("answered_ok", [])
    er_list = data.get("answered_err", [])
    if correct:
        ok_list.append(task)
    else:
        er_list.append(task)
    await state.update_data(answered_ok=ok_list, answered_err=er_list)

    if correct:
        await call.message.answer("✅ Верно!")
        await state.update_data(score=data.get("score", 0) + 1)
    else:
        await call.message.answer(f"❌ Неверно. Правильный ответ: {task['answer']}.\n{task.get('explain','').strip()}")

    await state.update_data(idx=idx + 1)
    await call.answer()
    await present_task(call, state)


# ---------- запуск нескольких ботов ----------
async def run_single_bot(token: str):
    bot = Bot(token=token)  # parse_mode не задаём (aiogram>=3.7)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(on_start, CommandStart())
    dp.message.register(cmd_level, F.text.startswith("/level"))
    dp.callback_query.register(cb_pick_level, F.data.startswith("lvl:"))
    dp.callback_query.register(cb_again, F.data == "again")
    dp.callback_query.register(cb_picklevel_button, F.data == "picklevel")
    dp.callback_query.register(cb_answer, F.data.startswith("ans:"))

    logging.info("Starting polling for bot…")
    await dp.start_polling(bot)


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    load_dotenv()

    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = os.getenv(key, "").strip()
        if t:
            tokens.append(t)

    if not tokens:
        raise RuntimeError("Нет токенов: добавь BOT_TOKEN (и при желании BOT_TOKEN2) в Railway Variables")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))


if __name__ == "__main__":
    asyncio.run(main())
