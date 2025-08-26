# bot/bot.py
import os
import re
import asyncio
import logging
import unicodedata
import zoneinfo
from datetime import datetime, timedelta

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage


# ================== setup ==================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

TZ_NAME = os.getenv("TZ", "UTC")
try:
    TZ = zoneinfo.ZoneInfo(TZ_NAME)
except Exception:
    from datetime import timezone as _tz
    TZ = _tz.utc

BUY_URL = os.getenv("BUY_URL", "https://t.me/your_payment_or_landing")
UNLOCK_CODE = os.getenv("UNLOCK_CODE", "TOD2024")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())

__BOT_VERSION__ = "kb-1.4-cta-metrics"


# ================== texts ==================
STREAK_MSG_FIRST = "🔥 Серия началась! Каждый день даёт бонус XP."
STREAK_MSG_CONTINUE = "🔥 Серия продолжается — так держать!"
STREAK_MSG_RESET = "🔁 Серия началась заново. Главное — вернуться к практике."


# ================== FSM ==================
class ATest(StatesGroup):
    waiting_answer = State()

class BTest(StatesGroup):
    waiting_answer = State()


# ================== utils ==================
NUMS = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧"]
RE_LEADING_NUM = re.compile(r"^\s*(\d+)[\.\)]?\s*")
ZWS = "".join(chr(c) for c in [0x200B, 0x200C, 0x200D, 0xFE0F, 0x20E3, 0x20DD])

def normalize_text(s: str) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    for ch in ZWS:
        s = s.replace(ch, "")
    return s.strip()

def build_kb_and_labels(options: list[str]) -> tuple[ReplyKeyboardMarkup, list[str]]:
    labels = []
    rows = []
    for i, o in enumerate(options):
        prefix = NUMS[i] if i < len(NUMS) else f"{i+1}."
        label = f"{prefix} {o}"
        labels.append(label)
        rows.append([KeyboardButton(text=label)])
    kb = ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)
    return kb, labels

def reserve_parse_index(text: str, n_options: int) -> int | None:
    t = normalize_text(text or "")
    m = RE_LEADING_NUM.match(t)
    if m:
        k = int(m.group(1))
        if 1 <= k <= n_options:
            return k - 1
    return None


# ================== in-memory DB ==================
DB: dict[str, dict] = {}
def _ensure_user(user_ref: str):
    if user_ref not in DB:
        DB[user_ref] = {
            "progress": {"A": 0, "B": 0},
            "xp": 0,
            "streak": 0,
            "last_day": None,
            "badges": set(),
            "premium": False,
            "events": [],   # для отладки
        }

def init_db():
    logging.info("DB initialized (in-memory)")

def has_premium(user_ref: str) -> bool:
    _ensure_user(user_ref)
    return DB[user_ref]["premium"]

def get_progress(user_ref: str) -> dict:
    _ensure_user(user_ref)
    return DB[user_ref]["progress"]

def get_xp(user_ref: str) -> int:
    _ensure_user(user_ref)
    return DB[user_ref]["xp"]

def add_xp(user_ref: str, xp: int):
    _ensure_user(user_ref)
    DB[user_ref]["xp"] += xp

def add_progress(user_ref: str, task_id: str, xp: int, badge: str | None):
    _ensure_user(user_ref)
    block = task_id[0]
    DB[user_ref]["progress"][block] = DB[user_ref]["progress"].get(block, 0) + 1
    add_xp(user_ref, xp)
    if badge:
        DB[user_ref]["badges"].add(badge)

def apply_daily_streak(user_ref: str) -> tuple[int, int, bool, str]:
    _ensure_user(user_ref)
    today = datetime.now(TZ).date()
    last_day = DB[user_ref]["last_day"]
    streak = DB[user_ref]["streak"]
    is_new_day = last_day != today
    mode = ""
    bonus = 0
    if is_new_day:
        if last_day == today - timedelta(days=1):
            streak += 1
            mode = "continue"
        elif last_day is None:
            streak = 1
            mode = "first"
        else:
            streak = 1
            mode = "reset"
        bonus = streak * 10
        DB[user_ref]["streak"] = streak
        DB[user_ref]["last_day"] = today
    return bonus, streak, is_new_day, mode

def is_level_completed(user_ref: str, block: str) -> bool:
    total = len(TASKS_A) if block == "A" else len(TASKS_B)
    return get_progress(user_ref).get(block, 0) >= total


# ================== analytics ==================
AN = {
    "starts_a": 0,
    "starts_b": 0,
    "cta_buy_clicks": 0,
    "cta_paid_clicks": 0,
    "unlock_success": 0,
    "unlock_fail": 0,
    "answers_total": 0,
    "answers_correct": 0,
    "task_shown": {},
    "task_answered": {},
    "task_correct": {},
}

def an_inc(key: str, subkey: str | None = None, add: int = 1):
    if subkey is None:
        AN[key] = AN.get(key, 0) + add
    else:
        bucket = AN.get(key)
        if not isinstance(bucket, dict):
            bucket = {}
            AN[key] = bucket
        bucket[subkey] = bucket.get(subkey, 0) + add

def an_dump_text() -> str:
    lines = []
    lines.append("📈 <b>Метрики</b>")
    lines.append(f"• Старт A: {AN['starts_a']}, Старт B: {AN['starts_b']}")
    lines.append(f"• CTA ‘Полный доступ’: {AN['cta_buy_clicks']}")
    lines.append(f"• CTA ‘Я оплатил(а)’: {AN['cta_paid_clicks']}")
    lines.append(f"• Разблокировок: ok {AN['unlock_success']}, fail {AN['unlock_fail']}")
    lines.append(f"• Ответов: всего {AN['answers_total']}, верных {AN['answers_correct']}")
    if AN["task_shown"]:
        lines.append("\n<b>Показы задач:</b>")
        for k, v in sorted(AN["task_shown"].items(), key=lambda kv: kv[1], reverse=True):
            ans = AN["task_answered"].get(k, 0)
            cor = AN["task_correct"].get(k, 0)
            lines.append(f"• {k}: показов {v}, ответов {ans}, верных {cor}")
    return "\n".join(lines)


# ================== tasks ==================
TASKS_A = [
    {"id":"A1","text":"«В ресторане всегда самая вкусная еда». Что это?",
     "options":["Факт","Мнение"],"answer":"Мнение",
     "explain":"«Самая вкусная» — оценка, а не проверяемый факт.","xp":10,"badge":None},
    {"id":"A2","text":"«Человек стоит под дождём без зонта, он наверняка простудится». Что это?",
     "options":["Факт","Интерпретация/прогноз"],"answer":"Интерпретация/прогноз",
     "explain":"Факт — только «стоит под дождём». Простуда — прогноз.","xp":10,"badge":None},
    {"id":"A3","text":"«Все умные люди покупают этот курс». Что это?",
     "options":["Факт","Манипуляция"],"answer":"Манипуляция",
     "explain":"Давление «все умные» апеллирует к статусу, не к истине.","xp":10,"badge":None},
    {"id":"A4","text":"«Эту новость сказал профессор, значит, она верная». Что это?",
     "options":["Факт","Аргумент к авторитету"],"answer":"Аргумент к авторитету",
     "explain":"Статус источника ≠ истинность утверждения.","xp":10,"badge":None},
    {"id":"A5","text":"«Люди в очках чаще читают книги. Значит, очки делают умнее». Что это?",
     "options":["Факт","Логическая ошибка"],"answer":"Логическая ошибка",
     "explain":"Корреляция ≠ причинность.","xp":10,"badge":None},
    {"id":"A6","text":"«Этот спикер богат — значит, его идеи правильные». Что это?",
     "options":["Аргумент","Манипуляция"],"answer":"Манипуляция",
     "explain":"Апелляция к успеху/статусу вместо аргументов.","xp":10,"badge":None},
    {"id":"A7","text":"«После внедрения CRM выросли продажи. Значит, CRM их вызвала». Что это?",
     "options":["Корректная причинность","Логическая ошибка"],"answer":"Логическая ошибка",
     "explain":"Пост hoc: совпадение по времени ≠ причина.","xp":10,"badge":None},
    {"id":"A8","text":"«В городе стало больше зонтов, значит, увеличились дожди». Что это?",
     "options":["Логическая ошибка","Факт"],"answer":"Логическая ошибка",
     "explain":"Зонты могут быть следствием, а не причиной.","xp":10,"badge":None},
    {"id":"A9","text":"«Исследование: читающие чаще носят очки. Очки улучшают интеллект». Что это?",
     "options":["Аргумент","Логическая ошибка"],"answer":"Логическая ошибка",
     "explain":"Вывод не следует из данных (корреляция).","xp":10,"badge":None},
    {"id":"A10","text":"«Этот эксперт популярен и уважаем, его мнение истинно». Что это?",
     "options":["Аргумент к авторитету","Факт"],"answer":"Аргумент к авторитету",
     "explain":"Популярность/авторитет — не критерий истины.","xp":10,"badge":None},
]

TASKS_B = [
    {"id":"B1","text":"Пример задания B1: выбери правильный вариант.",
     "options":["Вариант A","Вариант B"],"answer":"Вариант A",
     "explain":"Демо-объяснение.","xp":20,"badge":"B-starter"},
]


# ================== helpers ==================
async def send_task(m: Message, state: FSMContext, task: dict, block: str):
    kb, labels = build_kb_and_labels(task["options"])
    await state.update_data(task_index=task.get("_idx", 0), labels=labels, block=block)
    await m.answer(f"Задание {task['id']}:\n{task['text']}", reply_markup=kb)
    an_inc("task_shown", task["id"], 1)

async def finish_if_needed(user_ref: str, m: Message, block: str):
    if block == "A" and is_level_completed(user_ref, "A"):
        await m.answer("🏅 Бейдж уровня A получен!")
    if block == "B" and is_level_completed(user_ref, "B"):
        await m.answer("🏅 Бейдж уровня B получен!")

def cta_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔓 Полный доступ (30+)", callback_data="cta_buy"),
    ], [
        InlineKeyboardButton(text="✅ Я оплатил(а)", callback_data="cta_paid"),
    ]])

async def send_cta_after_A(m: Message):
    await m.answer(
        "🚀 Хочешь продолжение: 30+ заданий уровня B, прогресс и бейджи?\n\n"
        "• Нажми «Полный доступ» — пришлю ссылку\n"
        "• Уже оплатил(а)? Нажми «Я оплатил(а)»",
        reply_markup=cta_keyboard()
    )


# ================== commands ==================
@dp.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    user_ref = f"tg:{m.from_user.id}"
    premium = has_premium(user_ref)
    pay_hint = "" if premium else "\n\nНачни бесплатный блок: /a_start"
    await m.answer(
        "Привет! Это <b>Test of Discernment</b> — тренажёр различения.\n\n"
        "Доступно:\n"
        "• Базовый тест (бесплатно)\n"
        "• Расширенная версия (30+ заданий, прогресс, подсказки)"
        + pay_hint,
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(Command("version"))
async def version(m: Message):
    await m.answer(f"bot version: {__BOT_VERSION__}")

@dp.message(Command("metrics"))
async def metrics(m: Message):
    await m.answer(an_dump_text())

@dp.message(Command("progress"))
async def progress(m: Message):
    user_ref = f"tg:{m.from_user.id}"
    xp = get_xp(user_ref)
    p = get_progress(user_ref)
    streak = DB[user_ref]["streak"]
    await m.answer(
        f"📊 <b>Прогресс</b>\n"
        f"A: <b>{p.get('A',0)}/{len(TASKS_A)}</b>\n"
        f"B: <b>{p.get('B',0)}/{len(TASKS_B)}</b>\n"
        f"XP: <b>{xp}</b>   Streak: <b>{streak}</b> дней",
        reply_markup=ReplyKeyboardRemove(),
    )

@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("⏹ Остановил текущее задание.", reply_markup=ReplyKeyboardRemove())

@dp.message(Command("a_start"))
async def a_start(m: Message, state: FSMContext):
    an_inc("starts_a", add=1)
    await state.clear()
    await state.set_state(ATest.waiting_answer)
    for i, t in enumerate(TASKS_A):
        t["_idx"] = i
    await send_task(m, state, TASKS_A[0], "A")

@dp.message(Command("b_start"))
async def b_start(m: Message, state: FSMContext):
    an_inc("starts_b", add=1)
    await state.clear()
    await state.set_state(BTest.waiting_answer)
    for i, t in enumerate(TASKS_B):
        t["_idx"] = i
    await send_task(m, state, TASKS_B[0], "B")

@dp.message(Command("unlock"))
async def unlock(m: Message, state: FSMContext):
    user_ref = f"tg:{m.from_user.id}"
    parts = (m.text or "").split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else ""
    if code == UNLOCK_CODE:
        DB[user_ref]["premium"] = True
        an_inc("unlock_success", add=1)
        await m.answer("✅ Доступ открыт! Запускаю уровень B…", reply_markup=ReplyKeyboardRemove())
        await b_start(m, state)
    else:
        an_inc("unlock_fail", add=1)
        await m.answer("❌ Неверный код. Проверь и пришли ещё раз: /unlock КОД")


# ================== CTA callbacks ==================
@dp.callback_query(F.data == "cta_buy")
async def cta_buy(c: CallbackQuery):
    an_inc("cta_buy_clicks", add=1)
    await c.answer()  # закрыть «часики»
    await c.message.answer(
        "Вот ссылка для оформления:\n"
        f"{BUY_URL}\n\n"
        "После оплаты пришли код: <code>/unlock КОД</code>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Оформить доступ", url=BUY_URL)
        ]])
    )

@dp.callback_query(F.data == "cta_paid")
async def cta_paid(c: CallbackQuery):
    an_inc("cta_paid_clicks", add=1)
    await c.answer("Если оплатил(а), пришли: /unlock КОД", show_alert=True)


# ================== engine ==================
async def handle_answer(m: Message, state: FSMContext, tasks: list[dict], block: str):
    data = await state.get_data()
    idx = data.get("task_index", 0)
    labels: list[str] = data.get("labels", [])
    if idx >= len(tasks):
        await m.answer("Блок уже завершён.", reply_markup=ReplyKeyboardRemove())
        await state.clear()
        return
    task = tasks[idx]
    user_ref = f"tg:{m.from_user.id}"

    an_inc("answers_total", add=1)
    an_inc("task_answered", task["id"], 1)

    text = normalize_text(m.text)
    choice = None

    for i, lbl in enumerate(labels):
        if text == normalize_text(lbl):
            choice = i
            break
    if choice is None:
        choice = reserve_parse_index(text, len(task["options"]))
    if choice is None:
        for i, opt in enumerate(task["options"]):
            if text.casefold() == normalize_text(opt).casefold():
                choice = i
                break
    if choice is None:
        kb, _ = build_kb_and_labels(task["options"])
        await m.answer("Выбери кнопкой или пришли цифру 1/2/3:", reply_markup=kb)
        return

    is_correct = (normalize_text(task["options"][choice]).casefold()
                  == normalize_text(task["answer"]).casefold())
    if is_correct:
        an_inc("answers_correct", add=1)
        an_inc("task_correct", task["id"], 1)
        bonus, streak_count, is_new_day, mode = apply_daily_streak(user_ref)
        add_progress(user_ref, task["id"], task["xp"], task["badge"])
        msg = f"✅ Верно! {task['explain']}\n+{task['xp']} XP"
        if is_new_day and bonus:
            add_xp(user_ref, bonus)
            msg += f"\n🔥 Ежедневная серия: +{bonus} XP (streak {streak_count} дн.)"
        if task["badge"]:
            msg += f"\n🏅 Новый бейдж: {task['badge']}"
        await m.answer(msg, reply_markup=ReplyKeyboardRemove())
        if is_new_day:
            if mode == "first": await m.answer(STREAK_MSG_FIRST)
            elif mode == "continue": await m.answer(STREAK_MSG_CONTINUE)
            elif mode == "reset": await m.answer(STREAK_MSG_RESET)
    else:
        await m.answer(f"❌ Неверно. {task['explain']}", reply_markup=ReplyKeyboardRemove())

    # next / finish
    if idx + 1 < len(tasks):
        await asyncio.sleep(0.03)
        await state.update_data(task_index=idx + 1)
        next_task = tasks[idx + 1]
        await send_task(m, state, next_task, block)
    else:
        if block == "A":
            await m.answer("🎉 Ты прошёл блок A1–A10! Посмотри прогресс: /progress")
            await send_cta_after_A(m)
        else:
            await m.answer("🎉 Уровень B завершён! Посмотри прогресс: /progress")
        await finish_if_needed(user_ref, m, block)
        await state.clear()


@dp.message(ATest.waiting_answer, F.text)
async def a_answer(m: Message, state: FSMContext):
    await handle_answer(m, state, TASKS_A, "A")

@dp.message(BTest.waiting_answer, F.text)
async def b_answer(m: Message, state: FSMContext):
    await handle_answer(m, state, TASKS_B, "B")


# ================== entry ==================
async def main():
    init_db()
    logging.info("Starting bot polling…")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
