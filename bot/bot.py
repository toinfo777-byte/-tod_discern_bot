# bot/bot.py
# =========================================================
# Multi-bot (две переменные BOT_TOKEN, BOT_TOKEN2) — aiogram v3
# Уровни A / B / HARD, финальный «портрет», анти-дабл-клик, /level и deep-link
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

# ------------------------ ЛОГИ ------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
load_dotenv()

# ----------------- ИМПОРТ ПУЛОВ ВОПРОСОВ -----------------
# A (tasks.py): поддерживаем оба имени — TASKS и TASKS_A
try:
    from .tasks import TASKS as TASKS_A_RAW  # type: ignore
except Exception:
    from .tasks import TASKS_A as TASKS_A_RAW  # type: ignore

# B (tasks_b.py): обычно TASKS_B
try:
    from .tasks_b import TASKS_B as TASKS_B_RAW  # type: ignore
except Exception:
    from .tasks_b import TASKS as TASKS_B_RAW  # fallback если называли TASKS

# HARD (tasks_hard.py): поддерживаем TASKS_HARD и TASKS_H
try:
    from .tasks_hard import TASKS_HARD as TASKS_HARD_RAW  # type: ignore
except Exception:
    try:
        from .tasks_hard import TASKS as TASKS_HARD_RAW  # type: ignore
    except Exception:
        try:
            from .tasks_hard import TASKS_H as TASKS_HARD_RAW  # type: ignore
        except Exception:
            TASKS_HARD_RAW = []  # нет файла или переменной — ок, просто без HARD

# -------------------- МОДЕЛИ/НОРМАЛИЗАЦИЯ --------------------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int = 0
    badge: Optional[str] = None
    explain: Optional[str] = None

def _norm(s: str) -> str:
    return (s or "").strip().casefold()

def _to_tasks(raw_list: List[dict]) -> List[Task]:
    out: List[Task] = []
    for r in raw_list or []:
        out.append(Task(
            id=str(r.get("id", "")),
            text=str(r.get("text", "")),
            options=list(r.get("options", [])),
            answer=str(r.get("answer", "")),
            xp=int(r.get("xp", 0) or 0),
            badge=r.get("badge"),
            explain=r.get("explain"),
        ))
    return out

TASKS_BY_LEVEL: Dict[str, List[Task]] = {
    "A": _to_tasks(TASKS_A_RAW),
    "B": _to_tasks(TASKS_B_RAW),
    "HARD": _to_tasks(TASKS_HARD_RAW),
}

ALL_LEVELS = ("A", "B", "HARD")

# -------------------- ПОЛИТИКА УРОВНЕЙ ПО bot.id --------------------
# Замени id на свои при необходимости
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    8222973157: {"default": "A", "allowed": {"A", "B", "HARD"}},  # @tod_discern_bot
    8416181261: {"default": "B", "allowed": {"B", "HARD"}},       # @discernment_test_bot
}

# -------------------- ФИНАЛЬНЫЙ ПОРТРЕТ/СОВЕТЫ --------------------
ADVICE_MAP = {
    "причина": "Замедляйся на причинности: ищи альтернативные объяснения и контроль групп.",
    "корреляция": "Корреляция ≠ причина. Проверь, нет ли общей третьей переменной.",
    "post hoc": "Последовательность событий не доказывает причинность.",
    "апелляция к авторитету": "Оцени метод/доказательства, а не статус/популярность.",
    "выживший набор": "Смотри на невидимые провалы: проси полную выборку.",
    "малый размер выборки": "Маленькие выборки шумные — доверяй репликациям/метаанализам.",
    "композиция": "Свойства части и целого не взаимозаменяемы.",
    "ложная дилемма": "Ищи третий вариант — бинарность часто искусственная.",
    "анекдот": "Один случай — не статистика. Нужны системные данные.",
    "пример": "Отдельные кейсы — не доказательство без базы.",
}

def build_portrait(mistakes: List[str], score: int, total: int, level: str) -> str:
    from collections import Counter
    cnt = Counter([m.strip().lower() for m in mistakes if m])
    if not cnt:
        headline = "Отлично! Ошибок почти нет — устойчивое различение 👏"
        tips = ["Поднимай планку — попробуй уровень HARD.", "Тренируйся на новостных примерах."]
    else:
        worst = [f"• {k} — {v}×" for k, v in cnt.most_common(3)]
        tips = []
        for k, _ in cnt.most_common(3):
            tips.append("• " + ADVICE_MAP.get(k, f"Тренируй распознавание приёма: {k}."))
        headline = "**Где чаще промахи:**\n" + "\n".join(worst)

    return (
        f"Готово! Итог: **{score}/{total}**\n\n"
        f"{headline}\n\n"
        f"**Советы:**\n" + "\n".join(tips) +
        f"\n\nУровень сейчас: **{level}**"
    )

# -------------------- КЛАВИАТУРЫ --------------------
def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=o, callback_data=f"ans:{i}")]
            for i, o in enumerate(options)]
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

def finish_kb(bot_username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="again")],
        [InlineKeyboardButton(text="Сменить уровень", callback_data="pick_level")],
        [InlineKeyboardButton(text="Поделиться", url=f"https://t.me/{bot_username}")],
    ])

# -------------------- АНТИ-ДАБЛ-КЛИК --------------------
# Ключ: (user_id, level) -> index вопроса
LAST_ANS: Dict[Tuple[int, str], int] = {}

# -------------------- ЯДРО: ПОКАЗ ВОПРОСОВ --------------------
async def send_question(msg: Message, state: FSMContext):
    data = await state.get_data()
    level: str = data.get("level", "A")
    idx: int = data.get("i", 0)
    tasks: List[Task] = TASKS_BY_LEVEL.get(level, [])
    total = len(tasks)

    if total == 0:
        await msg.answer("Для этого уровня пока нет вопросов. Выбери другой:", reply_markup=level_picker_kb())
        return

    if idx >= total:
        score = int(data.get("score", 0))
        mistakes: List[str] = data.get("mistakes", [])
        portrait = build_portrait(mistakes, score, total, level)
        me = await msg.bot.me()
        await msg.answer(portrait, reply_markup=finish_kb(me.username), parse_mode="Markdown")
        return

    task = tasks[idx]
    await msg.answer(
        f"Задание {idx+1}/{total}:\n{task.text}",
        reply_markup=answers_kb(task.options)
    )

async def start_flow(msg: Message, state: FSMContext, default_level: str):
    data = await state.get_data()
    lvl = data.get("level", default_level)
    tasks = TASKS_BY_LEVEL.get(lvl, [])
    await state.update_data(level=lvl, i=0, score=0, mistakes=[], total=len(tasks))
    await msg.answer("Начинаем! 🧠")
    await send_question(msg, state)

# -------------------- РЕГИСТРАЦИЯ ХЭНДЛЕРОВ --------------------
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
        parts = (message.text or "").split()
        if len(parts) == 2:
            new_level = parts[1].upper()
            if new_level in allowed_levels and new_level in TASKS_BY_LEVEL and TASKS_BY_LEVEL[new_level]:
                await state.update_data(level=new_level, i=0, score=0, mistakes=[], total=len(TASKS_BY_LEVEL[new_level]))
                await message.answer(f"Уровень переключён на {new_level}.")
                await start_flow(message, state, default_level)
                return
        # иначе показать меню
        await message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed_levels))

    @dp.callback_query(F.data == "pick_level")
    async def on_pick_level(cb: CallbackQuery):
        await cb.message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed_levels))
        await cb.answer()

    @dp.callback_query(F.data.startswith("ans:"))
async def handle_answer(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = callback.data.split(":", 1)[1]

    # Берём состояние пользователя
    user_state = await state.get_data()
    current_index = user_state.get("current_index", 0)
    answered = user_state.get("answered", False)

    # Если ответ уже был принят → игнорируем повтор
    if answered:
        await callback.answer("Ответ уже принят ✅", show_alert=False)
        return

    # Отмечаем, что ответ принят
    user_state["answered"] = True

    # Проверка правильности ответа
    task_list = user_state.get("task_list", [])
    if current_index < len(task_list):
        task = task_list[current_index]
        correct_answer = task["answer"].strip().lower()
        if data.strip().lower() == correct_answer:
            await callback.message.answer(f"✅ Верно! Правильный ответ: {task['answer']}\n\n{task['explain']}")
        else:
            await callback.message.answer(f"❌ Неверно. Правильный ответ: {task['answer']}\n\n{task['explain']}")

        # Переход к следующему вопросу
        current_index += 1
        if current_index < len(task_list):
            user_state["current_index"] = current_index
            user_state["answered"] = False  # сбрасываем флаг для нового вопроса
            await state.set_data(user_state)
            await send_task(callback.message, task_list[current_index], current_index)
        else:
            await callback.message.answer("Готово! Тест завершён ✅")
            await state.clear()
    else:
        await callback.message.answer("Тест уже завершён ✅")
        await state.clear()

    # Обновляем состояние
    await state.set_data(user_state)
    await callback.answer()


    @dp.callback_query(F.data == "again")
    async def on_again(cb: CallbackQuery, state: FSMContext):
        await cb.answer()
        data = await state.get_data()
        lvl = data.get("level", default_level)
        await state.update_data(i=0, score=0, mistakes=[], total=len(TASKS_BY_LEVEL.get(lvl, [])))
        await start_flow(cb.message, state, default_level)

    @dp.callback_query(F.data.startswith("ans:"))
    async def on_answer(cb: CallbackQuery, state: FSMContext):
        await cb.answer()  # мгновенное подтверждение для UX
        data = await state.get_data()
        level: str = data.get("level", default_level)
        idx: int = data.get("i", 0)
        tasks: List[Task] = TASKS_BY_LEVEL.get(level, [])
        total = len(tasks)
        if idx >= total:
            return

        # анти-дабл-клик
        uid = cb.from_user.id
        if LAST_ANS.get((uid, level)) == idx:
            await cb.message.answer("Ответ уже принят ✅")
            return
        LAST_ANS[(uid, level)] = idx

        task = tasks[idx]
        try:
            opt_index = int(cb.data.split(":", 1)[1])
        except Exception:
            opt_index = -1
        chosen = task.options[opt_index] if 0 <= opt_index < len(task.options) else ""

        correct = (_norm(chosen) == _norm(task.answer))
        if correct:
            txt = f"✅ Верно! Правильный ответ: {task.answer}."
            if task.explain:
                txt += f"\n{task.explain}"
            await cb.message.answer(txt)
            await state.update_data(score=int(data.get("score", 0)) + 1)
        else:
            txt = f"❌ Неверно. Правильный ответ: {task.answer}."
            if task.explain:
                txt += f"\n{task.explain}"
            await cb.message.answer(txt)
            # копим «тип» ошибки для портрета (используем нормализованный answer как ярлык)
            mistakes = list(data.get("mistakes", []))
            mistakes.append(_norm(task.answer))
            await state.update_data(mistakes=mistakes)

        await state.update_data(i=idx + 1, total=total)
        await send_question(cb.message, state)

# -------------------- ЗАПУСК НЕСКОЛЬКИХ БОТОВ --------------------
async def run_single_bot(token: str):
    bot = Bot(token=token)  # без parse_mode — совместимо с aiogram 3.7+
    dp = Dispatcher(storage=MemoryStorage())

    me = await bot.me()
    bot_id = me.id
    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    default_level = str(policy.get("default", "A"))
    allowed_levels = set(policy.get("allowed", set(ALL_LEVELS)))

    register_handlers(dp, default_level, allowed_levels)

    logging.info(f"Start polling for bot @{me.username} id={me.id}")
    await dp.start_polling(bot)

async def main():
    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = (os.getenv(key) or "").strip()
        if t:
            tokens.append(t)
    if not tokens:
        raise RuntimeError("Нет токенов. Добавьте BOT_TOKEN (и при желании BOT_TOKEN2).")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopped.")
