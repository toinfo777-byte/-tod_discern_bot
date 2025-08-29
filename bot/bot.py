# bot/bot.py
# =========================================================
# Multi-bot (2 токена) + уровни A / B / HARD — aiogram v3
# Финальный портрет, советы, анти-дабл-клик, /level и deep-link
# =========================================================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from dotenv import load_dotenv

# ---------- импорт пулов вопросов ----------
# tasks.py — базовый (A), tasks_b.py — продвинутый (B), tasks_hard.py — хард (HARD)
from .tasks import TASKS as TASKS_A
from .tasks_b import TASKS_B
try:
    # файл может называться по-разному — пробуем оба варианта
    from .tasks_hard import TASKS_HARD
except ImportError:
    # на случай, если переменная названа иначе
    from .tasks_hard import TASKS_H as TASKS_HARD  # type: ignore


# ---------- конфиг ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
load_dotenv()

TOKENS: List[str] = []
for key in ("BOT_TOKEN", "BOT_TOKEN2"):
    t = os.getenv(key, "").strip()
    if t:
        TOKENS.append(t)

if not TOKENS:
    raise RuntimeError("Нет токенов: добавьте env BOT_TOKEN (и при желании BOT_TOKEN2)")

# политика уровней по bot.id (можешь заменить id на свои)
# @tod_discern_bot -> 8222973157
# @discernment_test_bot -> 8416181261
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    8222973157: {"default": "A", "allowed": {"A", "B", "HARD"}},
    8416181261: {"default": "B", "allowed": {"B", "HARD"}},
}
ALL_LEVELS = ("A", "B", "HARD")

# ---------- финальный портрет/советы ----------
ADVICE_MAP = {
    "причина": "Замедляйся на причинности: ищи альтернативные объяснения и контроль групп.",
    "корреляция": "Корреляция ≠ причина. Проверяй, нет ли общей третьей переменной.",
    "post hoc": "Последовательность событий не доказывает причинность.",
    "апелляция к авторитету": "Оцени метод/доказательства, а не статус/популярность.",
    "выживший набор": "Смотри на невидимые провалы: проси полную выборку.",
    "малый размер выборки": "Маленькие выборки шумные — доверяй только репликациям/метаанализу.",
    "композиция": "Свойства части и целого не взаимозаменяемы.",
    "ложная дилемма": "Ищи третий вариант: бинарность часто искусственная.",
    "анекдот": "Отдельные кейсы — не доказательство без базы.",
    "пример": "Отдельные кейсы — не доказательство без базы.",
}

def build_portrait(mistakes: List[str], score: int, total: int, level: str) -> str:
    from collections import Counter
    cnt = Counter(mistakes)
    if not cnt:
        headline = "Отлично! Ошибок нет — устойчивое различение 👏"
        tips = ["Поднимай планку — попробуй уровень HARD.", "Проверь себя на новостных примерах."]
    else:
        worst = [f"• {k} — {v}×" for k, v in cnt.most_common(3)]
        tips = []
        for k, _ in cnt.most_common(3):
            key = k.lower().strip()
            tips.append("• " + ADVICE_MAP.get(key, f"Тренируй распознавание приёма: {k}."))
        headline = "**Где чаще промахи:**\n" + "\n".join(worst)

    return (
        f"Готово! Итог: **{score}/{total}**\n\n"
        f"{headline}\n\n"
        f"**Советы:**\n" + "\n".join(tips) +
        f"\n\nУровень сейчас: **{level}**"
    )

# ---------- модели ----------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int = 0
    badge: Optional[str] = None
    explain: Optional[str] = None

# ---------- нормализация пулов ----------
def _norm(s: str) -> str:
    return (s or "").strip().casefold()

def _to_tasks(raw_list: List[dict]) -> List[Task]:
    out: List[Task] = []
    for r in raw_list:
        out.append(Task(
            id=r.get("id", ""),
            text=r.get("text", ""),
            options=r.get("options", []),
            answer=r.get("answer", ""),
            xp=int(r.get("xp", 0)),
            badge=r.get("badge"),
            explain=r.get("explain"),
        ))
    return out

TASKS_BY_LEVEL: Dict[str, List[Task]] = {
    "A": _to_tasks(TASKS_A),
    "B": _to_tasks(TASKS_B),
    "HARD": _to_tasks(TASKS_HARD),
}

# ---------- утилиты клавиатур ----------
def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def level_picker_kb(allowed: Optional[set] = None) -> InlineKeyboardMarkup:
    allowed = allowed or set(ALL_LEVELS)
    btns = []
    if "A" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень A", callback_data="set_level:A")])
    if "B" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень B", callback_data="set_level:B")])
    if "HARD" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень HARD", callback_data="set_level:HARD")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ---------- анти-дабл-клик ----------
# (user_id, level) -> index вопроса
LAST_ANS: Dict[Tuple[int, str], int] = {}


# ---------- ядро логики ----------
async def send_question(message: Message, state: FSMContext):
    data = await state.get_data()
    level: str = data.get("level", "A")
    idx: int = data.get("i", 0)
    tasks = TASKS_BY_LEVEL[level]
    total = len(tasks)

    if idx >= total:
        # финал
        score = int(data.get("score", 0))
        mistakes: List[str] = data.get("mistakes", [])
        portrait = build_portrait(mistakes, score, total, level)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Пройти ещё раз", callback_data="again")],
            [InlineKeyboardButton(text="Сменить уровень", callback_data="pick_level")],
            [InlineKeyboardButton(text="Поделиться", url=f"https://t.me/{(await message.bot.me()).username}")]
        ])
        await message.answer(portrait, reply_markup=kb, parse_mode="Markdown")
        return

    task = tasks[idx]
    await message.answer(
        f"Задание {idx+1}/{total}:\n{task.text}",
        reply_markup=answers_kb(task.options)
    )


async def start_flow(message: Message, state: FSMContext, default_level: str):
    # если в стейте ещё нет уровня — поставить дефолтный
    data = await state.get_data()
    lvl = data.get("level")
    if not lvl:
        lvl = default_level
        await state.update_data(level=lvl)

    # сброс прогресса
    await state.update_data(i=0, score=0, total=len(TASKS_BY_LEVEL[lvl]), mistakes=[])

    await message.answer("Начинаем! 🧠")
    await send_question(message, state)


# =========================================================
#             Регистрация хэндлеров для DP
# =========================================================
def register_handlers(dp: Dispatcher, default_level: str, allowed_levels: set):
    @dp.message(CommandStart())
    async def on_start(message: Message, state: FSMContext):
        # deep-link: /start level_A|level_B|level_HARD
        args = message.text.split(maxsplit=1)[1:] if message.text else []
        if args:
            p = args[0].strip().lower()
            if p in ("level_a", "level_b", "level_hard"):
                lvl = p.split("_")[1].upper()
                if lvl in allowed_levels:
                    await state.update_data(level=lvl)

        hello = (
            "Готов проверить себя на различение?\n\n"
            "• 10 заданий · 2 минуты\n"
            "• Сразу разбор и советы\n\n"
            "Сменить уровень — кнопкой **«Сменить уровень»** или командами: "
            "`/level A`, `/level B`, `/level HARD`."
        )
        await message.answer(hello, parse_mode="Markdown")
        await start_flow(message, state, default_level)

    @dp.message(Command("level"))
    async def cmd_level(message: Message, state: FSMContext):
        parts = message.text.split()
        if len(parts) == 2 and parts[1].upper() in ALL_LEVELS and parts[1].upper() in allowed_levels:
            new_level = parts[1].upper()
            await state.update_data(level=new_level, i=0, score=0, mistakes=[], total=len(TASKS_BY_LEVEL[new_level]))
            await message.answer(f"Уровень переключён на {new_level}.")
            await start_flow(message, state, default_level)
            return
        # иначе меню
        await message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed_levels))

    @dp.callback_query(F.data == "pick_level")
    async def on_pick_level(callback: CallbackQuery):
        await callback.message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed_levels))
        await callback.answer()

    @dp.callback_query(F.data.startswith("set_level:"))
    async def on_set_level(callback: CallbackQuery, state: FSMContext):
        _, lvl = callback.data.split(":", 1)
        if lvl not in allowed_levels:
            await callback.answer("Этот уровень недоступен для этого бота.", show_alert=True)
            return
        await state.update_data(level=lvl, i=0, score=0, mistakes=[], total=len(TASKS_BY_LEVEL[lvl]))
        await callback.message.answer(f"Уровень переключён на {lvl}.")
        await callback.answer()
        await start_flow(callback.message, state, default_level)

    @dp.callback_query(F.data == "again")
    async def on_again(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        lvl = data.get("level", default_level)
        await state.update_data(i=0, score=0, mistakes=[], total=len(TASKS_BY_LEVEL[lvl]))
        await callback.answer()
        await start_flow(callback.message, state, default_level)

    @dp.callback_query(F.data.startswith("ans:"))
    async def on_answer(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        level: str = data.get("level", default_level)
        idx: int = data.get("i", 0)
        tasks = TASKS_BY_LEVEL[level]
        total = len(tasks)
        if idx >= total:
            await callback.answer()
            return

        # анти-дабл-клик
        uid = callback.from_user.id
        if LAST_ANS.get((uid, level)) == idx:
            await callback.answer("Ответ уже принят ✅")
            return
        LAST_ANS[(uid, level)] = idx

        task = tasks[idx]
        # какой вариант выбран
        try:
            opt_index = int(callback.data.split(":")[1])
        except Exception:
            opt_index = -1

        user_answer = task.options[opt_index] if 0 <= opt_index < len(task.options) else ""
        is_correct = (_norm(user_answer) == _norm(task.answer))

        if is_correct:
            text = f"✅ Верно! Правильный ответ: {task.answer}."
            if task.explain:
                text += f"\n{task.explain}"
            new_score = int(data.get("score", 0)) + 1
            await state.update_data(score=new_score)
        else:
            text = f"❌ Неверно. Правильный ответ: {task.answer}."
            if task.explain:
                text += f"\n{task.explain}"
            # копим «тип» ошибки
            mistakes: List[str] = data.get("mistakes", [])
            mistakes.append(_norm(task.answer))
            await state.update_data(mistakes=mistakes)

        await callback.message.answer(text)
        await callback.answer()

        # следующий вопрос
        await state.update_data(i=idx + 1, total=total)
        await send_question(callback.message, state)


# =========================================================
#                    run & polling
# =========================================================
async def run_single_bot(token: str):
    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    me = await bot.me()
    bot_id = me.id

    # политика для этого бота
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    default_level = policy.get("default", "A")  # type: ignore
    allowed_levels = set(policy.get("allowed", set(ALL_LEVELS)))  # type: ignore

    register_handlers(dp, default_level, allowed_levels)

    logging.info(f"Starting polling for bot @{me.username} (id={bot_id})")
    await dp.start_polling(bot)


async def main():
    await asyncio.gather(*(run_single_bot(t) for t in TOKENS))


if __name__ == "__main__":
    asyncio.run(main())
