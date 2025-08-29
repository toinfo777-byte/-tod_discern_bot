# bot/bot.py
# =======================
# Multi-bot + 3 pools (basic/advanced/hard) — aiogram v3
# =======================

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    DefaultBotProperties,
)
from dotenv import load_dotenv

# ---- импорт пулов вопросов ----
# tasks.py -> базовый, tasks_b.py -> продвинутый, tasks_hard.py -> хард
from .tasks import TASKS as TASKS_A
from .tasks_b import TASKS_B
from .tasks_hard import TASKS_HARD

# ---------- модель задания ----------
@dataclass
class Task:
    id: str
    text: str
    options: List[str]
    answer: str
    xp: int = 10
    badge: str | None = None
    explain: str | None = None


def normalize_pool(src: List[dict]) -> List[Task]:
    out: List[Task] = []
    for t in src:
        out.append(
            Task(
                id=t["id"],
                text=t["text"],
                options=t["options"],
                answer=t["answer"],
                xp=t.get("xp", 10),
                badge=t.get("badge"),
                explain=t.get("explain"),
            )
        )
    return out


POOLS: Dict[str, List[Task]] = {
    "a": normalize_pool(TASKS_A),       # Базовый
    "b": normalize_pool(TASKS_B),       # Продвинутый
    "h": normalize_pool(TASKS_HARD),    # Хард
}

POOL_TITLES = {
    "a": "Базовый",
    "b": "Продвинутый",
    "h": "Хард",
}

# ---------- state в памяти процесса ----------
# ключ — (bot_id, user_id)
StateKey = Tuple[int, int]
STATE: Dict[StateKey, Dict] = {}


def skey_from_message(m: Message) -> StateKey:
    return (m.bot.id, m.from_user.id)


def skey_from_callback(cq: CallbackQuery) -> StateKey:
    uid = cq.from_user.id if cq.from_user else 0
    return (cq.bot.id, uid)


def ensure_state(key: StateKey):
    if key not in STATE:
        STATE[key] = {"pool": "a", "idx": 0, "score": 0}


# ---------- UI helpers ----------
def kb_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧪 Пройти мини-тест", callback_data="start")],
            [InlineKeyboardButton(text="🎚 Выбрать режим", callback_data="modes")],
        ]
    )


def kb_modes(current: str) -> InlineKeyboardMarkup:
    rows = []
    for code, title in [("a", "Базовый"), ("b", "Продвинутый"), ("h", "Хард")]:
        mark = " • текущий" if code == current else ""
        rows.append(
            [InlineKeyboardButton(text=f"{title}{mark}", callback_data=f"mode:{code}")]
        )
    rows.append([InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_options(options: List[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        rows.append([InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_again() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Пройти ещё раз", callback_data="again")],
            [InlineKeyboardButton(text="🎚 Сменить режим", callback_data="modes")],
        ]
    )


# ---------- основной поток логики ----------
async def send_task(message: Message, st: Dict):
    pool_code = st["pool"]
    pool = POOLS[pool_code]
    idx = st["idx"]
    task = pool[idx]

    await message.answer(
        f"<b>Задание {idx + 1}/{len(pool)}:</b>\n{task.text}",
        reply_markup=kb_options(task.options),
    )


async def finish(message: Message, st: Dict):
    pool = POOLS[st["pool"]]
    total = len(pool)
    score = st["score"]
    await message.answer(
        f"<b>Готово!</b> Итог: <b>{score}/{total}</b>\n\n"
        f"Если понравилось — можно пройти ещё раз или позвать друга 😉",
        reply_markup=kb_again(),
    )


# ---------- aiogram handlers ----------
def register_handlers(dp: Dispatcher):
    @dp.message(CommandStart())
    async def on_start(m: Message):
        key = skey_from_message(m)
        ensure_state(key)
        st = STATE[key]
        await m.answer(
            "Бот на связи ✅\n\n"
            f"Готов проверить себя на различение?\n"
            f"Текущий режим: <b>{POOL_TITLES[st['pool']]}</b>",
            reply_markup=kb_start(),
        )

    @dp.callback_query(F.data == "back_to_menu")
    async def back_menu(cq: CallbackQuery):
        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        await cq.message.edit_text(
            "Готов проверить себя на различение?\n"
            f"Текущий режим: <b>{POOL_TITLES[st['pool']]}</b>",
            reply_markup=kb_start(),
        )
        await cq.answer()

    @dp.callback_query(F.data == "modes")
    async def show_modes(cq: CallbackQuery):
        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        await cq.message.edit_text(
            "Выбери уровень задач:",
            reply_markup=kb_modes(st["pool"]),
        )
        await cq.answer()

    @dp.callback_query(F.data.startswith("mode:"))
    async def set_mode(cq: CallbackQuery):
        code = cq.data.split(":", 1)[1]
        if code not in POOLS:
            await cq.answer("Неизвестный режим", show_alert=True)
            return

        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        st["pool"] = code
        st["idx"] = 0
        st["score"] = 0

        await cq.message.edit_text(
            f"Режим переключён на: <b>{POOL_TITLES[code]}</b>",
            reply_markup=kb_start(),
        )
        await cq.answer("Готово!")

    @dp.callback_query(F.data == "start")
    async def start_quiz(cq: CallbackQuery):
        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        st["idx"] = 0
        st["score"] = 0

        # отправляем первое задание
        await cq.message.edit_text("Поехали! 👇")
        await send_task(cq.message, st)
        await cq.answer()

    @dp.callback_query(F.data.startswith("ans:"))
    async def answer(cq: CallbackQuery):
        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        pool = POOLS[st["pool"]]
        idx = st["idx"]
        task = pool[idx]

        try:
            choice_idx = int(cq.data.split(":", 1)[1])
        except Exception:
            await cq.answer("Ошибочный ответ", show_alert=True)
            return

        choice_text = task.options[choice_idx].casefold()
        correct = (choice_text == task.answer.casefold())

        # ответ-фидбек
        if correct:
            st["score"] += 1
            msg = "✅ <b>Верно!</b>"
        else:
            msg = f"❌ Неверно. Правильный ответ: <b>{task.answer}</b>."

        if task.explain:
            msg += f"\n{task.explain}"

        await cq.message.answer(msg)

        # следующий шаг
        st["idx"] += 1
        if st["idx"] >= len(pool):
            await finish(cq.message, st)
        else:
            await send_task(cq.message, st)

        await cq.answer()

    @dp.callback_query(F.data == "again")
    async def again(cq: CallbackQuery):
        key = skey_from_callback(cq)
        ensure_state(key)
        st = STATE[key]
        st["idx"] = 0
        st["score"] = 0
        await cq.message.edit_text("Начинаем заново 👇")
        await send_task(cq.message, st)
        await cq.answer()


# ---------- запуск нескольких ботов ----------
async def run_single_bot(token: str):
    # важно: parse_mode указывается через DefaultBotProperties — это aiogram v3.7+
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    register_handlers(dp)

    me = await bot.get_me()
    logging.info(f"Starting polling for @{me.username} (id={me.id})")
    await dp.start_polling(bot)


async def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
    )
    load_dotenv()

    tokens: List[str] = []
    for env_key in ("BOT_TOKEN", "BOT_TOKEN2"):
        val = os.getenv(env_key, "").strip()
        if val:
            tokens.append(val)

    if not tokens:
        raise RuntimeError("Добавьте хотя бы один токен в env: BOT_TOKEN / BOT_TOKEN2")

    masked = ["*" * 6 + t[-6:] for t in tokens]
    logging.info(f"Tokens found: {len(tokens)} -> {masked}")

    await asyncio.gather(*(run_single_bot(t) for t in tokens))


if __name__ == "__main__":
    asyncio.run(main())
