# bot/bot.py
# ============================================
# Multi-bot (A/B/HARD) — aiogram v3
# c антидребезгом (защита от двойных нажатий)
# ============================================

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
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# --- пулы вопросов ---
# tasks.py   -> уровень A (базовый)
# tasks_b.py -> уровень B (продвинутый)
# tasks_hard.py -> HARD (усложнённый)
from .tasks import TASKS_A
from .tasks_b import TASKS_B
# Файл с «хардом» называйте как у вас в репо: tasks_hard.py
# и экспортируйте из него переменную TASKS_HARD
from .tasks_hard import TASKS_HARD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# --- нормализация/утилиты -----------------------------------------------------
def _norm(s: str) -> str:
    return (s or "").strip().casefold()

def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"ans:{_norm(opt)}")]
        for opt in options
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def level_picker_kb(allowed: Optional[Tuple[str, ...]] = None) -> InlineKeyboardMarkup:
    allowed = allowed or ("A", "B", "HARD")
    btns = []
    if "A" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень A", callback_data="pick_level:A")])
    if "B" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень B", callback_data="pick_level:B")])
    if "HARD" in allowed:
        btns.append([InlineKeyboardButton(text="Уровень HARD", callback_data="pick_level:HARD")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

def after_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="restart")],
        [InlineKeyboardButton(text="Сменить уровень", callback_data="change_level")],
        # Telegram ограничивает «шэринг» из бота; оставим как кнопку-«раскрывашку»
        [InlineKeyboardButton(text="Поделиться", callback_data="share_info")]
    ])

# --- «каталог» всех уровней ---------------------------------------------------
ALL_LEVELS: Tuple[str, ...] = ("A", "B", "HARD")

# Политика уровней по bot.id (настройте под свои боты!)
# 8222973157 — @tod_discern_bot
# 8416181261 — @discernment_test_bot
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    8222973157: {"default": "A", "allowed": ("A", "B", "HARD")},
    8416181261: {"default": "B", "allowed": ("B", "HARD")},
}

# --- «словарь» пулов ----------------------------------------------------------
LEVEL_TASKS: Dict[str, List[dict]] = {
    "A": TASKS_A,
    "B": TASKS_B,
    "HARD": TASKS_HARD,
}

# --- состояние пользователя в памяти ------------------------------------------
@dataclass
class UserRun:
    level: str = "A"
    current_index: int = 0
    total: int = 10
    task_ids: List[str] = None
    # антидребезг: чтобы не принимать второе нажатие по тому же вопросу
    answered: bool = False

# --- сервисные функции ---------------------------------------------------------
INTRO = (
    "Готов проверить себя на различение?\n\n"
    "• 10 заданий · 2 минуты\n"
    "• Сразу разбор и советы\n\n"
    "Сменить уровень — кнопкой **Сменить уровень** или командами: /level_A, /level_B, /level_HARD.\n\n"
    "Начинаем! 🧠"
)

async def send_task(msg: Message, task: dict, index: int):
    text = f"Задание {index + 1}/10:\n{task['text']}"
    await msg.answer(text, reply_markup=answers_kb(task["options"]))

def calc_profile_summary(stats: Dict[str, int]) -> str:
    if not stats:
        return "Ошибок нет — отлично! Продолжай тренироваться на новом уровне."
    lines = ["**Где чаще промахи:**"]
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        lines.append(f"• {k} — {v}×")
    return "\n".join(lines)

def advice_block(stats: Dict[str, int]) -> str:
    adv: List[str] = []
    # примеры простых советов
    if stats.get("малый_размер_выборки"):
        adv.append("Маленькие выборки шумные — доверяй репликациям/метаанализам.")
    if stats.get("post_hoc") or stats.get("ложная_причина"):
        adv.append("Замедляйся на причинности: последовательность ≠ причина.")
    if stats.get("перекладывание_бремени_доказательства"):
        adv.append("Требуй метод/доказательства, а не статус/популярность.")
    if not adv:
        adv.append("Хорошее различение! Иногда можно ловиться на тонкие манипуляции — продолжай тренироваться.")
    return "**Советы:**\n" + "\n".join(f"• {a}" for a in adv)

def normalize_key(answer_text: str) -> str:
    # приводим «человеческие» ярлыки к «ключам» для счётчика ошибок
    return (
        _norm(answer_text)
        .replace(" ", "_")
        .replace("ё", "е")
    )

# --- хэндлеры -----------------------------------------------------------------
def setup_handlers(dp: Dispatcher, bot_id: int):

    def _bot_policy() -> Tuple[str, Tuple[str, ...]]:
        policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": ALL_LEVELS})
        return policy["default"], tuple(policy["allowed"])

    @dp.message(CommandStart())
    async def on_start(m: Message, state: FSMContext):
        default_level, _ = _bot_policy()
        # инициализация пробега
        run = UserRun(level=default_level, current_index=0, total=10, task_ids=[], answered=False)
        await state.update_data(run=run.__dict__, stats={})
        await m.answer(INTRO, parse_mode=None)  # без parse_mode для совместимости
        # стартуем с первого задания
        tasks = LEVEL_TASKS[run.level][: run.total]
        await state.update_data(task_list=tasks)
        await send_task(m, tasks[0], 0)

    # Быстрые алиасы для команд уровней
    @dp.message(F.text.in_({"/level", "/level_A", "/level_B", "/level_HARD"}))
    async def on_level_cmd(m: Message, state: FSMContext):
        _default, allowed = _bot_policy()
        # Если команда вида /level_X — переключим сразу
        txt = (m.text or "").strip().lower()
        mapping = {"/level_a": "A", "/level_b": "B", "/level_hard": "HARD"}
        if txt in mapping:
            new_level = mapping[txt]
            if new_level in allowed:
                data = await state.get_data()
                run_d = data.get("run", {})
                run_d.update(level=new_level, current_index=0, answered=False)
                await state.update_data(run=run_d, stats={}, task_list=LEVEL_TASKS[new_level][:10])
                await m.answer(f"Уровень переключён на {new_level}.")
                await m.answer("Начинаем! 🧠")
                await send_task(m, LEVEL_TASKS[new_level][0], 0)
                return

        # иначе покажем клавиатуру выбора
        await m.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed))

    @dp.callback_query(F.data == "change_level")
    async def on_change_level(cb: CallbackQuery, state: FSMContext):
        _default, allowed = _bot_policy()
        await cb.message.answer("Выбери уровень:", reply_markup=level_picker_kb(allowed))
        await cb.answer()

    @dp.callback_query(F.data.startswith("pick_level:"))
    async def on_pick_level(cb: CallbackQuery, state: FSMContext):
        _default, allowed = _bot_policy()
        new_level = cb.data.split(":", 1)[1]
        if new_level not in allowed:
            await cb.answer("Этот уровень недоступен для этого бота", show_alert=True)
            return
        data = await state.get_data()
        run_d = data.get("run", {})
        run_d.update(level=new_level, current_index=0, answered=False)
        await state.update_data(run=run_d, stats={}, task_list=LEVEL_TASKS[new_level][:10])
        await cb.message.answer(f"Уровень переключён на {new_level}.")
        await cb.message.answer("Начинаем! 🧠")
        await send_task(cb.message, LEVEL_TASKS[new_level][0], 0)
        await cb.answer()

    @dp.callback_query(F.data == "restart")
    async def on_restart(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        run_d = data.get("run", {})
        level = run_d.get("level", "A")
        run_d.update(current_index=0, answered=False)
        await state.update_data(run=run_d, stats={}, task_list=LEVEL_TASKS[level][:10])
        await cb.message.answer("Поехали ещё раз! 🧠")
        await send_task(cb.message, LEVEL_TASKS[level][0], 0)
        await cb.answer()

    @dp.callback_query(F.data == "share_info")
    async def on_share(cb: CallbackQuery):
        await cb.answer("Скопируй любой вопрос и кинь другу. Увидимся в боте ✌️", show_alert=True)

    # --- ГЛАВНЫЙ ФИКС: антидребезг ------------------------------------------
    @dp.callback_query(F.data.startswith("ans:"))
    async def handle_answer(cb: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        run_d: dict = data.get("run", {}) or {}
        task_list: List[dict] = data.get("task_list", []) or []
        stats: Dict[str, int] = data.get("stats", {}) or {}

        idx = int(run_d.get("current_index", 0))
        answered = bool(run_d.get("answered", False))

        # если уже отвечали на этот вопрос — игнорим повтор
        if answered:
            await cb.answer("Ответ уже принят ✅")
            return

        # защита включена с этого момента
        run_d["answered"] = True

        if idx >= len(task_list):
            await cb.message.answer("Тест уже завершён ✅")
            await state.update_data(run=run_d)  # всё равно сохраним
            await cb.answer()
            return

        task = task_list[idx]
        correct = _norm(task["answer"])
        user_ans = cb.data.split(":", 1)[1]

        if user_ans == correct:
            await cb.message.answer(f"✅ Верно! Правильный ответ: {task['answer']}\n\n{task['explain']}")
        else:
            await cb.message.answer(f"❌ Неверно. Правильный ответ: {task['answer']}\n\n{task['explain']}")
            key = normalize_key(task["answer"])
            stats[key] = stats.get(key, 0) + 1

        idx += 1
        if idx < len(task_list):
            # переход к следующему
            run_d["current_index"] = idx
            run_d["answered"] = False  # сброс для нового вопроса
            await state.update_data(run=run_d, stats=stats)
            await send_task(cb.message, task_list[idx], idx)
        else:
            # финалка
            summary = calc_profile_summary(stats)
            adv = advice_block(stats)
            await cb.message.answer(
                f"Готово! Итог: {sum(1 for _ in task_list) - sum(stats.values())}/{len(task_list)}\n\n{summary}\n\n{adv}",
                reply_markup=after_result_kb()
            )
            # Не очищаем state полностью — оставляем «run» и «level» для перезапуска/смены уровня
            run_d["current_index"] = 0
            run_d["answered"] = False
            await state.update_data(run=run_d, stats={})

        await cb.answer()

# --- запуск двух ботов --------------------------------------------------------
async def run_single_bot(token: str):
    bot = Bot(token=token)  # без parse_mode ради совместимости с 3.6/3.7
    me = await bot.get_me()
    dp = Dispatcher(storage=MemoryStorage())
    setup_handlers(dp, me.id)
    logging.info("Starting polling for bot…")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

async def main():
    tokens = []
    # Railway env:
    t1 = os.getenv("BOT_TOKEN")
    t2 = os.getenv("BOT_TOKEN2")
    if t1:
        tokens.append(t1)
    if t2:
        tokens.append(t2)

    if not tokens:
        logging.error("Нет токенов BOT_TOKEN / BOT_TOKEN2")
        return

    logging.info(f"Starting polling for {len(tokens)} bot(s).")
    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    asyncio.run(main())
