# bot/bot.py
# ========= Multi-bot + 3 pools (basic/advanced/hard) — aiogram v3 =========

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
    ReplyKeyboardRemove,
    DefaultBotProperties,
)
from dotenv import load_dotenv

# ---- импорты пулов задач (устойчивые к различным способам запуска) ----
# tasks.py -> базовый, tasks_b.py -> продвинутый, tasks_hard.py -> хард
try:
    # когда запускаем как пакет: python -m bot.bot
    from bot.tasks import TASKS as TASKS_A
    from bot.tasks_b import TASKS as TASKS_B
    from bot.tasks_hard import TASKS as TASKS_HARD
except Exception:
    # когда файл исполняется напрямую: python bot/bot.py
    from tasks import TASKS as TASKS_A
    from tasks_b import TASKS as TASKS_B
    from tasks_hard import TASKS as TASKS_HARD

# ==================== базовая настройка ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
load_dotenv()

# читаем до двух токенов
TOKENS: List[str] = []
for name in ("BOT_TOKEN", "BOT_TOKEN2"):
    t = os.getenv(name, "").strip()
    if t:
        TOKENS.append(t)

if not TOKENS:
    raise RuntimeError("Нет токенов: добавьте переменные окружения BOT_TOKEN и/или BOT_TOKEN2")

# общее in-memory хранилище
storage = MemoryStorage()

# версия для логов
__BOT_VERSION__ = "kb-1.7-three-pools"

# ==================== модель данных ====================
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int = 10
    badge: Optional[str] = None
    explain: Optional[str] = None

# ==================== утилиты ====================
def pool_by_level(level: str) -> List[Task]:
    lvl = level.upper()
    if lvl in ("H", "HARD"):
        return [Task(**t) for t in TASKS_HARD]
    if lvl in ("B", "ADV", "ADVANCED"):
        return [Task(**t) for t in TASKS_B]
    return [Task(**t) for t in TASKS_A]  # по умолчанию — basic

def normalize(s: str) -> str:
    return (s or "").strip().lower()

def build_inline_kb(options: List[str], block: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for idx, opt in enumerate(options):
        rows.append(
            [InlineKeyboardButton(text=opt, callback_data=f"ans:{block}:{idx}")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ==================== общие обработчики ====================
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    # закрываем системную клаву невидимым сообщением
    try:
        await m.answer("\u2060", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass

    await state.update_data(level="A", idx=0, score=0)
    await m.answer(
        "Готов проверить себя на различение?\n\n"
        "Доступные уровни: <b>A</b> (базовый), <b>B</b> (продвинутый), <b>HARD</b> (хард).\n"
        "Сменить уровень: <code>/level A</code> | <code>/level B</code> | <code>/level HARD</code>",
    )
    await send_task(m, state)

async def cmd_level(m: Message, state: FSMContext):
    parts = m.text.split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Укажите уровень: /level A | /level B | /level HARD")
        return
    level = parts[1].strip().upper()
    if level not in ("A", "B", "HARD", "H"):
        await m.answer("Не понял уровень. Используйте: A, B или HARD.")
        return

    # сбрасываем прогресс и ставим новый уровень
    await state.update_data(level=level if level != "H" else "HARD", idx=0, score=0)
    await m.answer(f"Уровень сменён на <b>{level}</b>.")
    await send_task(m, state)

async def send_task(m: Message, state: FSMContext):
    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("idx", 0))

    tasks = pool_by_level(level)
    if idx >= len(tasks):
        # финал
        score = int(data.get("score", 0))
        await m.answer(
            f"Готово! Итог: <b>{score}/{len(tasks)}</b>\n\n"
            "Если понравилось — можно пройти ещё раз или позвать друга 😉",
        )
        # кнопка перезапуска
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пройти ещё раз", callback_data="restart")]
            ]
        )
        await m.answer(" ", reply_markup=kb)
        return

    task = tasks[idx]
    kb = build_inline_kb(task.options, block=task.id)
    await state.update_data(current_id=task.id, tasks_len=len(tasks))
    await m.answer(f"Задание {idx+1}/{len(tasks)}:\n<b>{task.text}</b>", reply_markup=kb)

# ==================== колбэки ====================
async def on_answer(cq: CallbackQuery, state: FSMContext):
    """
    callback_data формат: ans:<task_id>:<option_index>
    """
    parts = cq.data.split(":")
    if len(parts) != 3:
        await cq.answer()
        return
    _, block, opt_idx_s = parts
    try:
        choice_idx = int(opt_idx_s)
    except ValueError:
        await cq.answer()
        return

    data = await state.get_data()
    level = data.get("level", "A")
    idx = int(data.get("idx", 0))
    tasks = pool_by_level(level)
    if idx >= len(tasks):
        await cq.answer()
        return

    task = tasks[idx]
    # защита от несоответствия блоков
    if task.id != block:
        await cq.answer()
        return

    chosen = task.options[choice_idx] if 0 <= choice_idx < len(task.options) else ""
    is_correct = normalize(chosen) == normalize(task.answer)

    if is_correct:
        await state.update_data(score=int(data.get("score", 0)) + 1)
        prefix = "✅ Верно!"
    else:
        prefix = "❌ Неверно."

    explain = ""
    if task.explain:
        explain = f"\n\n{task.explain}"

    await cq.message.answer(
        f"{prefix} Правильный ответ: <b>{task.answer}</b>.{explain}"
    )

    # следующий вопрос
    await state.update_data(idx=idx + 1)
    await send_task(cq.message, state)
    await cq.answer()

async def on_restart(cq: CallbackQuery, state: FSMContext):
    await state.update_data(idx=0, score=0)
    await cq.message.answer("Поехали ещё раз!")
    await send_task(cq.message, state)
    await cq.answer()

# ==================== запуск нескольких ботов ====================
async def run_single_bot(token: str):
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=storage)

    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_level, F.text.startswith("/level"))
    dp.callback_query.register(on_restart, F.data == "restart")
    dp.callback_query.register(on_answer, F.data.startswith("ans:"))

    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot)

async def main():
    logging.info(f"Run polling for {len(TOKENS)} bot(s) — version {__BOT_VERSION__}")
    await asyncio.gather(*(run_single_bot(t) for t in TOKENS))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
