# bot/bot.py
# ==========================================================
# Multi-bot | уровни A / B / HARD | aiogram v3
# Стабильные ответы: idempotency по message_id + safe_answer
# ==========================================================

import os
import asyncio
import logging
import contextlib
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramRetryAfter, TelegramBadRequest
from dotenv import load_dotenv

# ---------- Пулы вопросов ----------
try:
    from .tasks import TASKS as TASKS_A
except Exception:
    from .tasks import TASKS_A  # type: ignore

try:
    from .tasks_b import TASKS as TASKS_B
except Exception:
    from .tasks_b import TASKS_B  # type: ignore

try:
    from .tasks_hard import TASKS as TASKS_HARD
except Exception:
    try:
        from .tasks_hard import TASKS_HARD  # type: ignore
    except Exception:
        TASKS_HARD = []

# ---------- Логирование ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bot")

# ---------- Хелперы ----------
def _norm(s: str) -> str:
    return (s or "").strip().casefold()

# Политика уровней по ботам (замени id при необходимости)
BOT_LEVEL_POLICY: Dict[int, Dict[str, object]] = {
    # @tod_discern_bot
    8222973157: {"default": "A", "allowed": {"A", "B", "HARD"}},
    # @discernment_test_bot
    8416181261: {"default": "B", "allowed": {"B", "HARD"}},
}
ALL_LEVELS: Tuple[str, ...] = ("A", "B", "HARD")

# ---------- Клавиатуры ----------
def answers_kb(options: List[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=opt, callback_data=f"ans:{i}")]
            for i, opt in enumerate(options)]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def level_picker_kb(allowed: Optional[set] = None) -> InlineKeyboardMarkup:
    allowed = allowed or set(ALL_LEVELS)
    rows = []
    if "A" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень A", callback_data="setlvl:A")])
    if "B" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень B", callback_data="setlvl:B")])
    if "HARD" in allowed:
        rows.append([InlineKeyboardButton(text="Уровень HARD", callback_data="setlvl:HARD")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def restart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Пройти ещё раз", callback_data="again")],
            [InlineKeyboardButton(text="Сменить уровень", callback_data="levelpick")],
            [InlineKeyboardButton(text="Поделиться", callback_data="share")],
        ]
    )

def share_kb(username: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(
            text="Поделиться ботом", url=f"https://t.me/{username}?start=share"
        )]]
    )

# ---------- safe utils ----------
async def safe_answer(cq: CallbackQuery, text: Optional[str] = None, *, cache_time: int = 0, show_alert: bool = False):
    try:
        await cq.answer(text=text, cache_time=cache_time, show_alert=show_alert)
    except TelegramBadRequest:
        # query is too old / invalid — игнорируем
        pass

async def safe_edit_text(msg: Message, text: str, reply_markup=None, parse_mode="HTML") -> Message:
    try:
        return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" in (str(e) or "").lower():
            return msg
        raise
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.5)
        return await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        log.warning("safe_edit_text failed: %s", e)
        return msg

# ---------- Состояние пользователя ----------
@dataclass
class UserState:
    level: str = "A"
    idx: int = 0
    score: int = 0
    total: int = 0
    misses: Dict[str, int] = None

    def reset(self, level: Optional[str] = None):
        if level:
            self.level = level
        self.idx = 0
        self.score = 0
        self.total = 0
        self.misses = {}

# Ключ: (bot_id, chat_id)
STATE: Dict[Tuple[int, int], UserState] = {}

# Идемпотентность ответов: (bot_id, user_id, message_id)
HANDLED: Set[Tuple[int, int, int]] = set()

def _key(bot_id: int, chat_id: int) -> Tuple[int, int]:
    return (bot_id, chat_id)

def get_tasks_by_level(level: str) -> List[dict]:
    if level == "A":
        return list(TASKS_A)
    if level == "B":
        return list(TASKS_B)
    if level == "HARD":
        return list(TASKS_HARD)
    return list(TASKS_A)

def render_intro(levels_line: str) -> str:
    return (
        "Готов проверить себя на различение?\n\n"
        "• 10 заданий · 2 минуты\n"
        "• Сразу разбор и советы\n\n"
        "Сменить уровень — кнопкой <b>«Сменить уровень»</b> или "
        f"командами: {levels_line}\n\n"
        "Начинаем! 🧠"
    )

def render_question(task: dict, idx: int, total: int) -> str:
    return f"Задание {idx}/{total}:\n{task['text']}"

def render_verdict(is_right: bool, task: dict) -> str:
    prefix = "✅ Верно!" if is_right else "❌ Неверно."
    ans = task.get("answer", "")
    explain = task.get("explain", "")
    if explain:
        return f"{prefix} Правильный ответ: <b>{ans}</b>.\n{explain}"
    else:
        return f"{prefix} Правильный ответ: <b>{ans}</b>."

def render_summary(state: UserState, level: str) -> str:
    lines = [f"Готово! Итог: <b>{state.score}/{state.total}</b>\n"]
    if state.misses:
        lines.append("<b>Где чаще промахи:</b>")
        for k, v in state.misses.items():
            lines.append(f"• {k} — {v}×")
        lines.append("")
        lines.append("<b>Советы:</b>")
        lines.append("• Замедляйся на причинности и выборках.")
        lines.append("• Ищи альтернативные объяснения и отсутствующие данные.")
        lines.append("• Проси метод/доказательства, а не статус/популярность.")
    else:
        lines.append("Хорошее различение! Иногда можно ловиться на тонкие манипуляции — продолжай тренироваться.")
    lines.append(f"\nУровень сейчас: <b>{level}</b>")
    return "\n".join(lines)

# ---------- Хендлеры ----------
async def start_quiz(msg: Message, bot_id: int, username: str):
    k = _key(bot_id, msg.chat.id)
    st = STATE.setdefault(k, UserState())

    policy = BOT_LEVEL_POLICY.get(bot_id, {"default": "A", "allowed": set(ALL_LEVELS)})
    if st.level not in policy.get("allowed", set(ALL_LEVELS)):
        st.level = policy.get("default", "A")

    tasks = get_tasks_by_level(st.level)
    st.idx = 0
    st.score = 0
    st.total = len(tasks)
    st.misses = {}

    levels_line = "<code>/level A</code>, <code>/level B</code>, <code>/level HARD</code>."
    await msg.answer(render_intro(levels_line), parse_mode="HTML")

    # Первый вопрос
    st.idx = 1
    task = tasks[0]
    await msg.answer(
        render_question(task, st.idx, st.total),
        reply_markup=answers_kb(task["options"]),
        parse_mode="HTML",
    )

def _current_task(bot_id: int, chat_id: int) -> Tuple[UserState, dict, List[dict]]:
    k = _key(bot_id, chat_id)
    st = STATE.setdefault(k, UserState())
    tasks = get_tasks_by_level(st.level)
    cur = tasks[st.idx - 1]
    return st, cur, tasks

def _record_miss(st: UserState, label: str):
    if not label:
        return
    st.misses[label] = st.misses.get(label, 0) + 1

# /start
async def on_start(message: Message, bot: Bot):
    bot_id = (await bot.me()).id
    username = (await bot.me()).username
    k = _key(bot_id, message.chat.id)
    st = STATE.setdefault(k, UserState())
    st.reset(level=BOT_LEVEL_POLICY.get(bot_id, {}).get("default", "A"))
    await start_quiz(message, bot_id, username)

# Команда выбора уровня
async def on_level_command(msg: Message, bot: Bot):
    bot_id = (await bot.me()).id
    allowed = BOT_LEVEL_POLICY.get(bot_id, {}).get("allowed", set(ALL_LEVELS))
    await msg.answer("Выбери уровень:", reply_markup=level_picker_kb(set(allowed)))

# Смена уровня (кнопка)
async def on_set_level(cq: CallbackQuery, bot: Bot):
    await safe_answer(cq, cache_time=0)
    bot_id = (await bot.me()).id

    level = cq.data.split(":")[1]
    policy = BOT_LEVEL_POLICY.get(bot_id, {"allowed": set(ALL_LEVELS)})
    allowed = policy.get("allowed", set(ALL_LEVELS))
    if level not in allowed:
        await cq.message.answer("Этот уровень недоступен для данного бота.")
        return

    k = _key(bot_id, cq.message.chat.id)
    st = STATE.setdefault(k, UserState())
    st.reset(level=level)

    with contextlib.suppress(Exception):
        await cq.message.edit_reply_markup()

    await cq.message.answer(f"Уровень переключён на <b>{level}</b>.", parse_mode="HTML")
    await start_quiz(cq.message, bot_id, (await bot.me()).username)

# Ответ на вариант
async def on_answer(cq: CallbackQuery, bot: Bot):
    await safe_answer(cq, cache_time=0)
    bot_id = (await bot.me()).id
    key = (bot_id, cq.from_user.id, cq.message.message_id)

    # Идемпотентность по message_id: один вопрос — один зачёт
    if key in HANDLED:
        await safe_answer(cq, text="Ответ уже принят ✅", cache_time=1)
        return
    HANDLED.add(key)

    k = _key(bot_id, cq.message.chat.id)
    st, task, tasks = _current_task(bot_id, cq.message.chat.id)

    # снимаем клавиатуру у старого вопроса
    with contextlib.suppress(Exception):
        await cq.message.edit_reply_markup()

    try:
        idx = int(cq.data.split(":")[1])
    except Exception:
        idx = -1

    chosen = task["options"][idx] if 0 <= idx < len(task["options"]) else ""
    is_right = _norm(chosen) == _norm(task["answer"])
    if is_right:
        st.score += 1
    else:
        _record_miss(st, _norm(task.get("answer", "")))

    await cq.message.answer(render_verdict(is_right, task), parse_mode="HTML")

    # следующий вопрос или финал
    if st.idx < st.total:
        st.idx += 1
        next_task = tasks[st.idx - 1]
        await cq.message.answer(
            render_question(next_task, st.idx, st.total),
            reply_markup=answers_kb(next_task["options"]),
            parse_mode="HTML",
        )
    else:
        summary = render_summary(st, st.level)
        await cq.message.answer(summary, reply_markup=restart_kb(), parse_mode="HTML")

# Пройти ещё раз
async def on_again(cq: CallbackQuery, bot: Bot):
    await safe_answer(cq, cache_time=0)
    bot_id = (await bot.me()).id
    k = _key(bot_id, cq.message.chat.id)
    st = STATE.setdefault(k, UserState())
    st.reset(level=st.level)
    with contextlib.suppress(Exception):
        await cq.message.edit_reply_markup()
    await start_quiz(cq.message, bot_id, (await bot.me()).username)

# Сменить уровень (под итогом)
async def on_level_pick(cq: CallbackQuery, bot: Bot):
    await safe_answer(cq, cache_time=0)
    bot_id = (await bot.me()).id
    allowed = BOT_LEVEL_POLICY.get(bot_id, {}).get("allowed", set(ALL_LEVELS))
    await cq.message.answer("Выбери уровень:", reply_markup=level_picker_kb(set(allowed)))

# Поделиться
async def on_share(cq: CallbackQuery, bot: Bot):
    await safe_answer(cq, cache_time=0)
    me = await bot.me()
    kb = share_kb(me.username or "discernment_test_bot")
    await cq.message.answer("Кинь другу — пусть тоже проверит различение:", reply_markup=kb)

# ------- Запуск одного бота -------
async def run_single_bot(token: str):
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(on_start, CommandStart())
    dp.message.register(on_level_command, F.text.startswith("/level"))

    dp.callback_query.register(on_set_level, F.data.startswith("setlvl:"))
    dp.callback_query.register(on_answer, F.data.startswith("ans:"))
    dp.callback_query.register(on_again, F.data == "again")
    dp.callback_query.register(on_level_pick, F.data == "levelpick")
    dp.callback_query.register(on_share, F.data == "share")

        # --- DIAG: пинг и лог всего ---
    async def ping(m: Message, bot: Bot):
        me = await bot.me()
        await m.answer(f"pong ✅ (@{me.username})")

    async def ping(m: Message, bot: Bot):
    me = await bot.me()
    log.info("Ping received from %s", m.from_user.id)
    await m.answer(f"pong ✅ (@{me.username})")

    async def log_any_message(m: Message):
        log.info("MSG from %s | chat=%s | text=%r",
                 m.from_user.id if m.from_user else None,
                 m.chat.id,
                 getattr(m, "text", None))

    async def log_any_callback(cq: CallbackQuery):
        log.info("CQ from %s | chat=%s | data=%r",
                 cq.from_user.id if cq.from_user else None,
                 cq.message.chat.id if cq.message else None,
                 cq.data)

    dp.message.register(ping, F.text == "/ping")
    dp.message.register(log_any_message)
    dp.callback_query.register(log_any_callback)
    
    with contextlib.suppress(Exception):
        me = await bot.me()
        log.info("Deleting webhook & dropping pending updates for @%s ...", me.username)
        await bot.delete_webhook(drop_pending_updates=True)

    # Сносим вебхук и «хвост» апдейтов, чтобы не ловить протухшие query
    with contextlib.suppress(Exception):
        await bot.delete_webhook(drop_pending_updates=True)

    me = await bot.me()
    log.info("Starting polling for bot @%s (id=%s)", me.username, me.id)
    await dp.start_polling(bot)

# ------- main -------
async def main():
    load_dotenv()
    tokens: List[str] = []
    for k, v in os.environ.items():
        if k.startswith("BOT_TOKEN") and v:
            tokens.append(v)
    if not tokens and os.environ.get("BOT_TOKEN"):
        tokens.append(os.environ["BOT_TOKEN"])
    if not tokens:
        raise RuntimeError("Не найден ни один BOT_TOKEN* в переменных окружения")

    log.info("Starting polling for %d bot(s): %s", len(tokens), ["***" + t[-5:] for t in tokens])
    await asyncio.gather(*(run_single_bot(t) for t in tokens))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Stopped.")
