# bot/bot.py
# ============== Multi-bot quiz (aiogram v3) ==============
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
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# ---------- quiz data ----------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str  # текст правильного варианта

# Вставляй/меняй задачи в этом формате
TASKS: List[Task] = [
    Task(id="A1", text="Исследование: зонты ↔ дождь. Что это?", options=["Причина", "Следствие", "Корреляция"], answer="Корреляция"),
    Task(id="A2", text="«Эксперт популярен — значит прав». Что это?", options=["Апелляция к авторитету", "Факт", "Аргумент"], answer="Апелляция к авторитету"),
    Task(id="A3", text="«Чем больше кофе, тем меньше сонливость». Это…", options=["Причина", "Факт", "Наблюдение"], answer="Наблюдение"),
    Task(id="A4", text="«После X случилось Y, значит X вызвал Y». Что это?", options=["Пост hoc", "Факт", "Гипотеза"], answer="Пост hoc"),
    Task(id="A5", text="«Доказано Гарвардом» без ссылки. Что это?", options=["Апелляция к авторитету", "Факт", "Реклама"], answer="Апелляция к авторитету"),
    Task(id="A6", text="«Корреляция ≠ причинность» — это…", options=["Правило", "Гипотеза", "Следствие"], answer="Правило"),
    Task(id="A7", text="«Если бы A, то B. B — следовательно A». Ошибка?", options=["Обратная импликация", "Следствие", "Факт"], answer="Обратная импликация"),
    Task(id="A8", text="«Читающие чаще в очках. Очки повышают интеллект». Что это?", options=["Корреляция", "Аргумент", "Причина"], answer="Корреляция"),
    Task(id="A9", text="«Мы нашли связь, значит нашли причину». Это…", options=["Подмена причинности", "Факт", "Наблюдение"], answer="Подмена причинности"),
    Task(id="A10", text="«Все так думают». Что это?", options=["Аргумент к большинству", "Факт", "Следствие"], answer="Аргумент к большинству"),
]

# ---------- helpers ----------
def build_options_kb(task: Task) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for idx, opt in enumerate(task.options):
        row.append(InlineKeyboardButton(
            text=f"{idx+1}) {opt}",
            callback_data=f"ans:{task.id}:{idx}"
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def normalize(s: str) -> str:
    return (s or "").strip().casefold()

# ---------- per-bot runtime ----------
async def run_single_bot(token: str) -> None:
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher(storage=MemoryStorage())  # отдельное хранилище для каждого бота

    # --- state keys внутри FSMContext: idx, score ---
    @dp.message(CommandStart())
    async def cmd_start(m: Message, state: FSMContext):
        await state.clear()
        text = (
            "Бот на связи ✅\n\n"
            "Готов проверить себя на различение?"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Пройти мини-тест", callback_data="quiz:start")]
            ]
        )
        # только inline-кнопки — системная клавиатура не всплывает
        await m.answer(text, reply_markup=kb)

    @dp.callback_query(F.data == "quiz:start")
    async def start_quiz(cb: CallbackQuery, state: FSMContext):
        await state.update_data(idx=0, score=0)
        task = TASKS[0]
        await cb.message.edit_text(
            f"Задание 1/ {len(TASKS)}:\n<b>{task.text}</b>",
            reply_markup=build_options_kb(task)
        )
        await cb.answer()

    @dp.callback_query(F.data.startswith("ans:"))
    async def on_answer(cb: CallbackQuery, state: FSMContext):
        try:
            _, task_id, opt_idx_str = cb.data.split(":")
            opt_idx = int(opt_idx_str)
        except Exception:
            await cb.answer("Что-то не так с ответом…", show_alert=True)
            return

        data = await state.get_data()
        idx = int(data.get("idx", 0))
        score = int(data.get("score", 0))
        task: Task = TASKS[idx]

        chosen = task.options[opt_idx]
        correct = normalize(chosen) == normalize(task.answer)
        if correct:
            score += 1

        # отзыв к текущему вопросу
        verdict = "✅ Верно!" if correct else "❌ Неверно."
        await cb.answer(verdict, show_alert=False)

        # следующий вопрос или итог
        idx += 1
        if idx >= len(TASKS):
            # финал
            await state.clear()
            total = len(TASKS)
            msg = (
                f"Готово! Итог: <b>{score}/{total}</b>\n\n"
                "Если понравилось — можно пройти ещё раз или позвать друга 😉"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Пройти ещё раз", callback_data="quiz:start")]
                ]
            )
            await cb.message.edit_text(msg, reply_markup=kb)
            return

        await state.update_data(idx=idx, score=score)
        task_next = TASKS[idx]
        await cb.message.edit_text(
            f"Задание {idx+1}/ {len(TASKS)}:\n<b>{task_next.text}</b>",
            reply_markup=build_options_kb(task_next)
        )

    # «мягкий» роут: если юзер набирает текстом
    @dp.message(F.text.regexp(r"^/quiz$|^тест$|^поехать|^начать").as_("m"))
    async def soft_start(m: Message, state: FSMContext):
        await start_quiz(
            CallbackQuery(id="0", from_user=m.from_user, chat_instance="0",
                          message=m, data="quiz:start"),
            state
        )

    # старт поллинга
    # Note: allowed_updates — по реально используемым типам
    used = dp.resolve_used_update_types()
    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot, allowed_updates=used)

# ---------- main: collect tokens & run ----------
def load_tokens_from_env() -> List[str]:
    # Берём BOT_TOKEN, BOT_TOKEN2, BOT_TOKEN3 ... (в любом порядке)
    tokens: List[str] = []
    # явные имена
    for key in sorted(os.environ.keys()):
        if key == "BOT_TOKEN" or key.startswith("BOT_TOKEN"):
            val = (os.getenv(key) or "").strip()
            if val:
                tokens.append(val)
    # убрать дубликаты и пустые
    tokens = [t for i, t in enumerate(tokens) if t and t not in tokens[:i]]
    return tokens

async def main():
    tokens = load_tokens_from_env()
    if not tokens:
        raise RuntimeError("Не найден ни один токен в ENV (BOT_TOKEN / BOT_TOKEN2 / ...)")
    masked = [f"{t[:6]}…{t[-4:]}" for t in tokens]
    logging.info(f"Запускаем ботов: {len(tokens)} шт -> {masked}")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановка по Ctrl+C")
