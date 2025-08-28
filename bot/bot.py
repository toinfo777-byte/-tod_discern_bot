# bot.py
# =======================
# Multi-bot + no-keyboard-popup edition (aiogram v3)
# =======================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.filters import CommandStart
from dotenv import load_dotenv

# ------------ базовая настройка логов и env ------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
load_dotenv()

# ------------ сбор токенов (1 или 2) ------------
TOKENS: List[str] = []
for key in ("BOT_TOKEN", "BOT_TOKEN2"):
    t = os.getenv(key, "").strip()
    if t:
        TOKENS.append(t)

if not TOKENS:
    raise RuntimeError(
        "Нет токенов. Укажите хотя бы BOT_TOKEN (и при необходимости BOT_TOKEN2) в Variables."
    )

# один общий Dispatcher и память
dp = Dispatcher(storage=MemoryStorage())

# --------------------- модель данных ---------------------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str  # правильный ответ (как текст из options)

# Пример мини-набора заданий.
# При желании замените на свои — формат сохраняйте.
TASKS: List[Task] = [
    Task(
        id="A1",
        text="«Исследование: зонт = причина дождя. Что это?»",
        options=["Причина", "Следствие", "Корреляция"],
        answer="Корреляция",
    ),
    Task(
        id="A2",
        text="«Эксперт популярен, значит прав. Что это?»",
        options=["Апелляция к авторитету", "Факт", "Аргумент"],
        answer="Апелляция к авторитету",
    ),
    Task(
        id="A3",
        text="«Чтение книг улучшает зрение». Что это?",
        options=["Гипотеза", "Факт", "Причина"],
        answer="Гипотеза",
    ),
]

# --------------- утилиты построения клавы ---------------
def build_inline_kb_and_labels(options: List[str], block: str = "main") -> InlineKeyboardMarkup:
    """
    Делаем инлайн-клавиатуру: одна строка — одна кнопка.
    callback_data: "opt:<index>"
    """
    rows = [
        [InlineKeyboardButton(text=f"{i+1}) {opt}", callback_data=f"opt:{i}")]
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def normalize_text(s: str) -> str:
    return (s or "").strip().lower()

# ------------------- показ задания (edit) -------------------
async def send_task(m: Message, state: FSMContext, task: Task):
    """
    Показываем (или пере-показываем) задание *редактированием* одного и того же сообщения,
    чтобы на Android не «вскакивала» системная клавиатура.
    """
    kb = build_inline_kb_and_labels(task.options, "main")
    data = await state.get_data()
    last_id: Optional[int] = data.get("last_msg_id")

    text = f"Задание {task.id}:\n{task.text}"

    if last_id:
        try:
            await m.bot.edit_message_text(
                chat_id=m.chat.id,
                message_id=last_id,
                text=text,
                reply_markup=kb,
            )
            return
        except Exception:
            pass  # если не смогли отредактировать — отправим новое и обновим last_msg_id

    msg = await m.answer(text, reply_markup=kb)
    await state.update_data(last_msg_id=msg.message_id)

# -------------------- логика проверки ----------------------
def resolve_choice_by_text(task: Task, user_text: str) -> Optional[int]:
    """
    Пытаемся сопоставить свободный текст пользователя с вариантами.
    Возвращаем индекс варианта или None.
    """
    t = normalize_text(user_text)
    if not t:
        return None
    for i, opt in enumerate(task.options):
        if normalize_text(opt) == t:
            return i
    return None

async def process_choice_and_continue(
    *,
    chat_message: Message,
    state: FSMContext,
    task_index: int,
    choice_index: int
):
    """
    Обработка выбранного варианта: верно/нет + переход к следующему заданию.
    Всё — через edit_message_text.
    """
    task = TASKS[task_index]
    is_correct = normalize_text(task.options[choice_index]) == normalize_text(task.answer)

    if is_correct:
        feedback = f"✅ Верно!\n\n"
    else:
        feedback = f"❌ Неверно.\n\n"

    # текст для показа после ответа
    text = feedback

    # следующий шаг: либо следующее задание, либо финал
    next_index = task_index + 1
    if next_index < len(TASKS):
        next_task = TASKS[next_index]
        text += f"Задание {next_task.id}:\n{next_task.text}"
        kb = build_inline_kb_and_labels(next_task.options, "main")
        await state.update_data(task_index=next_index)

        data = await state.get_data()
        last_id = data.get("last_msg_id") or chat_message.message_id
        try:
            await chat_message.bot.edit_message_text(
                chat_id=chat_message.chat.id,
                message_id=last_id,
                text=text,
                reply_markup=kb,
            )
        except Exception:
            msg = await chat_message.answer(text, reply_markup=kb)
            await state.update_data(last_msg_id=msg.message_id)
    else:
        text += "Тест завершён. Спасибо! 🙌"
        data = await state.get_data()
        last_id = data.get("last_msg_id") or chat_message.message_id
        try:
            await chat_message.bot.edit_message_text(
                chat_id=chat_message.chat.id,
                message_id=last_id,
                text=text,
                reply_markup=None,
            )
        except Exception:
            msg = await chat_message.answer(text)
            await state.update_data(last_msg_id=msg.message_id)

# --------------------- handlers ----------------------------
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    # сбрасываем прогресс и «якорное» сообщение
    await state.clear()
    await state.update_data(task_index=0, last_msg_id=None)

    # присылаем одно «якорное» сообщение (в дальнейшем его редактируем)
    intro = "Привет! Это мини-тест на различение. Выбирай вариант на кнопках 👇"
    msg = await m.answer(intro)
    await state.update_data(last_msg_id=msg.message_id)

    # показываем первое задание
    await send_task(m, state, TASKS[0])

# Выбор по инлайн-кнопке
@dp.callback_query(F.data.startswith("opt:"))
async def on_option_callback(call: CallbackQuery, state: FSMContext):
    await call.answer()  # подтверждаем callback, НИЧЕГО не отправляем
    data = await state.get_data()
    task_index: int = data.get("task_index", 0)

    try:
        choice_index = int(call.data.split(":", 1)[1])
    except Exception:
        return

    await process_choice_and_continue(
        chat_message=call.message,
        state=state,
        task_index=task_index,
        choice_index=choice_index,
    )

# Если пользователь отвечает ТЕКСТОМ — пытаемся сопоставить с опциями.
@dp.message()
async def on_free_text(m: Message, state: FSMContext):
    data = await state.get_data()
    task_index: int = data.get("task_index", 0)
    if task_index >= len(TASKS):
        return  # уже прошли тест

    task = TASKS[task_index]
    choice_index = resolve_choice_by_text(task, m.text)

    if choice_index is None:
        # повторно показываем текущее задание ТЕМ ЖЕ сообщением (без поднятия системы)
        await send_task(m, state, task)
        return

    await process_choice_and_continue(
        chat_message=m,
        state=state,
        task_index=task_index,
        choice_index=choice_index,
    )

# -------------------- multi-bot bootstrap -------------------
async def _prepare_bot(token: str) -> Bot:
    """
    Создаём инстанс бота и снимаем webhook, чтобы polling точно получил апдейты.
    """
    bot = Bot(token)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logging.info(f"Webhook cleared for @{me.username} (id={me.id})")
    except Exception as e:
        logging.exception(f"delete_webhook failed for token ...{token[-6:]}: {e}")
    return bot

async def main():
    bots = [await _prepare_bot(t) for t in TOKENS]
    # запускаем один dp для всех ботов
    await asyncio.gather(*[dp.start_polling(b) for b in bots])

if __name__ == "__main__":
    asyncio.run(main())
