# bot/bot.py
# =========================================================
# Multi-bot + 3 pools (A / B / HARD) — aiogram v3
# с мгновенным подтверждением callback'ов (c.answer())
# и валидацией токенов при старте.
# =========================================================

import os
import re
import asyncio
import logging
import importlib
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional, Any

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
from aiogram.client.default import DefaultBotProperties  # parse_mode=HTML

# ---------- логирование ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ---------- импорт пулов вопросов ----------
# Ожидаемые файлы:
#   bot/tasks.py       -> TASKS или TASKS_A
#   bot/tasks_b.py     -> TASKS или TASKS_B
#   bot/tasks_hard.py  -> TASKS или TASKS_HARD
# Каждый TASKS_* — список словарей:
#   {id, text, options: [..], answer, xp?, badge?, explain?}

def _safe_import(mod_name: str) -> Optional[Any]:
    """Импорт bot.<mod_name> с безопасным фолбэком."""
    try:
        return importlib.import_module(f"bot.{mod_name}")
    except Exception as e:
        logging.warning(f"Не удалось импортировать bot.{mod_name}: {e}")
        return None

def _resolve_tasks_var(mod: Any, names: List[str]) -> List[Dict]:
    """Берём из модуля первую существующую переменную из names, иначе []."""
    if not mod:
        return []
    for n in names:
        if hasattr(mod, n):
            val = getattr(mod, n)
            if isinstance(val, list):
                return val
    logging.warning(f"В модуле {mod.__name__} нет ни одной из переменных: {', '.join(names)}")
    return []

_m_a = _safe_import("tasks")
_m_b = _safe_import("tasks_b")
_m_h = _safe_import("tasks_hard")

TASKS_A_RAW   = _resolve_tasks_var(_m_a, ["TASKS_A", "TASKS"])
TASKS_B_RAW   = _resolve_tasks_var(_m_b, ["TASKS_B", "TASKS"])
TASKS_HARD_RAW= _resolve_tasks_var(_m_h, ["TASKS_HARD", "TASKS"])

def _normalize_task(t: Dict) -> Dict:
    """Нормализуем поля, гарантируем наличие необязательных ключей."""
    return {
        "id": t.get("id", ""),
        "text": t.get("text", ""),
        "options": list(t.get("options", [])),
        "answer": t.get("answer", ""),
        "xp": int(t.get("xp", 0)),
        "badge": t.get("badge"),
        "explain": t.get("explain", ""),
    }

TASKS_A    = [_normalize_task(t) for t in TASKS_A_RAW]
TASKS_B    = [_normalize_task(t) for t in TASKS_B_RAW]
TASKS_HARD = [_normalize_task(t) for t in TASKS_HARD_RAW]

LEVEL_TO_TASKS: Dict[str, List[Dict]] = {
    "A": TASKS_A,
    "B": TASKS_B,
    "HARD": TASKS_HARD,
}

# ---------- утилиты ----------
def normalize_text(s: str) -> str:
    return (s or "").strip().casefold()

def level_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Уровень A",    callback_data="level:A")],
        [InlineKeyboardButton(text="Уровень B",    callback_data="level:B")],
        [InlineKeyboardButton(text="Уровень HARD", callback_data="level:HARD")],
    ])

def restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Пройти ещё раз", callback_data="restart")],
        [InlineKeyboardButton(text="Сменить уровень", callback_data="level_menu")],
    ])

def options_keyboard(opts: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=o, callback_data=f"opt:{i}")]
            for i, o in enumerate(opts)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def init_level_state(state: FSMContext, level: str) -> None:
    tasks = LEVEL_TO_TASKS.get(level, [])
    await state.update_data(level=level, tasks=tasks, idx=0, score=0)

# ---------- показ задания ----------
async def send_task(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    tasks: List[Dict] = data.get("tasks", [])
    idx: int = data.get("idx", 0)

    # если задания закончились — финальный экран
    if idx >= len(tasks):
        score = data.get("score", 0)
        total = len(tasks)
        msg = (
            f"Готово! Итог: <b>{score}/{total}</b>\n\n"
            "Хорошее различение! Иногда можно ловиться на тонкие манипуляции — "
            "продолжай тренироваться."
        )
        await m.answer(msg, reply_markup=restart_keyboard())
        return

    task = tasks[idx]
    text = f"Задание {idx+1}/{len(tasks)}:\n{task['text']}"
    kb = options_keyboard(task["options"])
    await m.answer(text, reply_markup=kb)

# ---------- регистрация хендлеров ----------
def register_handlers(dp: Dispatcher):

    @dp.message(CommandStart())
    async def cmd_start(m: Message, state: FSMContext):
        # По умолчанию уровень A
        await init_level_state(state, "A")
        await m.answer(
            "Готов проверить себя на различение?\n\n"
            "Доступные уровни: A (базовый), B (продвинутый), HARD (хард).\n"
            "Сменить уровень — кнопкой внизу «Сменить уровень» или командой /level A|B|HARD."
        )
        await send_task(m, state)

    # /level A|B|HARD
    @dp.message(F.text.regexp(r"^/level\s+(A|B|HARD)\b"))
    async def cmd_level(m: Message, state: FSMContext):
        match = re.match(r"^/level\s+(A|B|HARD)\b", m.text.strip(), re.I)
        level = match.group(1).upper()
        await init_level_state(state, level)
        await m.answer(f"Уровень переключён на <b>{level}</b>.")
        await send_task(m, state)

    # Показ меню уровней
    @dp.callback_query(F.data == "level_menu")
    async def on_level_menu(c: CallbackQuery):
        await c.answer()
        await c.message.answer("Выбери уровень:", reply_markup=level_keyboard())

    # Переключение уровня (кнопки)
    @dp.callback_query(F.data.startswith("level:"))
    async def on_change_level(c: CallbackQuery, state: FSMContext):
        await c.answer()
        _, level = c.data.split(":", 1)
        level = level.upper()
        await init_level_state(state, level)
        await c.message.answer(f"Уровень переключён на <b>{level}</b>.")
        await send_task(c.message, state)

    # Повтор (пройти ещё раз)
    @dp.callback_query(F.data == "restart")
    async def on_restart(c: CallbackQuery, state: FSMContext):
        await c.answer()
        data = await state.get_data()
        level = data.get("level", "A")
        await init_level_state(state, level)
        await c.message.answer("Начали заново!")
        await send_task(c.message, state)

    # Выбор варианта ответа
    @dp.callback_query(F.data.startswith("opt:"))
    async def on_option(c: CallbackQuery, state: FSMContext):
        # критично: подтверждаем мгновенно — иначе у юзера крутится спиннер
        await c.answer()

        data = await state.get_data()
        tasks: List[Dict] = data.get("tasks", [])
        idx: int = data.get("idx", 0)
        score: int = data.get("score", 0)

        if idx >= len(tasks):
            # уже всё, на всякий случай
            await c.message.answer("Тест завершён. Нажми «Пройти ещё раз».")
            return

        task = tasks[idx]
        # что выбрали
        try:
            choice_idx = int(c.data.split(":", 1)[1])
        except Exception:
            choice_idx = -1

        chosen = task["options"][choice_idx] if 0 <= choice_idx < len(task["options"]) else ""
        is_correct = normalize_text(chosen) == normalize_text(task["answer"])

        if is_correct:
            score += 1
            await c.message.answer(
                f"✅ Верно! Правильный ответ: <b>{task['answer']}</b>.\n"
                f"{task.get('explain', '')}".strip()
            )
        else:
            await c.message.answer(
                f"❌ Неверно. Правильный ответ: <b>{task['answer']}</b>.\n"
                f"{task.get('explain', '')}".strip()
            )

        # следующий вопрос
        await state.update_data(idx=idx + 1, score=score)
        await send_task(c.message, state)

# ---------- запуск нескольких ботов ----------
async def start_single_bot(token: str) -> Optional[Tuple[Bot, Dispatcher]]:
    try:
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
        me = await bot.get_me()  # валидация токена
        logging.info(f"OK token …{token[-6:]} -> @{me.username} (id={me.id})")

        dp = Dispatcher(storage=MemoryStorage())
        register_handlers(dp)
        # Запускаем polling в фоне
        asyncio.create_task(dp.start_polling(bot))
        return bot, dp
    except Exception as e:
        logging.error(f"BAD token …{token[-6:]} -> {e}")
        return None

async def main() -> None:
    tokens: List[str] = []
    for key in ("BOT_TOKEN", "BOT_TOKEN2"):
        t = (os.getenv(key) or "").strip()
        if t:
            tokens.append(t)

    if not tokens:
        raise RuntimeError("Нет ни одного токена в env (BOT_TOKEN / BOT_TOKEN2).")

    results = await asyncio.gather(*(start_single_bot(t) for t in tokens))
    started = [r for r in results if r]
    logging.info(f"Running {len(started)} bot(s).")

    if not started:
        raise RuntimeError("Не удалось запустить ни один бот — проверь токены и логи.")

    # держим процесс живым
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Stopped.")
