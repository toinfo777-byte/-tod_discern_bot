import os, sqlite3, threading, json
from contextlib import contextmanager
from datetime import datetime, date, timedelta
import zoneinfo

# ---- Config / TZ ----
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data.db"))
import datetime
TZ_NAME = os.getenv("TZ", "UTC")  # при желании в .env можно задать TZ=Europe/Moscow
try:
    TZ = zoneinfo.ZoneInfo(TZ_NAME)
except Exception:
    TZ = datetime.timezone.utc

DAILY_BONUS_XP = int(os.getenv("DAILY_BONUS_XP", "5"))

_lock = threading.Lock()

@contextmanager
def connect():
    with _lock:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

# ---- Core tables ----
def init_db():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_ref TEXT PRIMARY KEY,
            premium INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            payment_id TEXT PRIMARY KEY,
            user_ref TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
    ensure_levels_table()
    init_notifications()
    init_metrics()

def set_premium(user_ref: str, payment_id: str, status: str = "succeeded"):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO payments (payment_id, user_ref, status) VALUES (?, ?, ?)",
                  (payment_id, user_ref, status))
        c.execute("""
            INSERT INTO users (user_ref, premium)
            VALUES (?, 1)
            ON CONFLICT(user_ref) DO UPDATE SET premium=1, updated_at=CURRENT_TIMESTAMP
        """, (user_ref,))

def has_premium(user_ref: str) -> bool:
    with connect() as c:
        row = c.execute("SELECT premium FROM users WHERE user_ref = ?", (user_ref,)).fetchone()
        return bool(row and row[0] == 1)

# ---- Progress / XP ----
def add_progress(user_ref: str, task_id: str, xp: int, badge: str|None):
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS progress (
            user_ref TEXT,
            task_id TEXT,
            correct INTEGER,
            xp INTEGER,
            badge TEXT,
            PRIMARY KEY (user_ref, task_id)
        )""")
        c.execute("""
        INSERT OR REPLACE INTO progress (user_ref, task_id, correct, xp, badge)
        VALUES (?, ?, 1, ?, ?)
        """, (user_ref, task_id, xp, badge))
    add_xp(user_ref, xp)

def add_xp(user_ref: str, xp: int):
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS xp_bank (
            user_ref TEXT PRIMARY KEY,
            xp_total INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        INSERT INTO xp_bank (user_ref, xp_total)
        VALUES (?, ?)
        ON CONFLICT(user_ref) DO UPDATE SET xp_total=xp_bank.xp_total+excluded.xp_total,
            updated_at=CURRENT_TIMESTAMP
        """, (user_ref, xp))

def get_xp(user_ref: str) -> int:
    with connect() as c:
        row = c.execute("SELECT xp_total FROM xp_bank WHERE user_ref=?", (user_ref,)).fetchone()
        return int(row[0]) if row else 0

def award_badge(user_ref: str, badge: str):
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS badges (
            user_ref TEXT,
            badge TEXT,
            PRIMARY KEY (user_ref, badge)
        )""")
        c.execute("INSERT OR IGNORE INTO badges (user_ref, badge) VALUES (?, ?)", (user_ref, badge))

def get_progress(user_ref: str):
    with connect() as c:
        task_badges = [r[0] for r in c.execute(
            "SELECT badge FROM progress WHERE user_ref=? AND badge IS NOT NULL", (user_ref,)
        ).fetchall()]
        level_badges = [r[0] for r in c.execute(
            "SELECT badge FROM badges WHERE user_ref=?", (user_ref,)
        ).fetchall()]
    total_xp = get_xp(user_ref)
    return total_xp, task_badges + level_badges

# ---- Levels ----
def ensure_levels_table():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS levels (
            user_ref TEXT,
            level_code TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            reward_xp INTEGER NOT NULL DEFAULT 0,
            completed_at DATETIME,
            PRIMARY KEY (user_ref, level_code)
        )""")

def is_level_completed(user_ref: str, level_code: str) -> bool:
    with connect() as c:
        row = c.execute("SELECT completed FROM levels WHERE user_ref=? AND level_code=?",
                        (user_ref, level_code)).fetchone()
        return bool(row and row[0] == 1)

def complete_level_once(user_ref: str, level_code: str, reward_xp: int) -> bool:
    ensure_levels_table()
    with connect() as c:
        row = c.execute("SELECT completed FROM levels WHERE user_ref=? AND level_code=?",
                        (user_ref, level_code)).fetchone()
        if row and row[0] == 1:
            return False
        c.execute("""
        INSERT INTO levels (user_ref, level_code, completed, reward_xp, completed_at)
        VALUES (?, ?, 1, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_ref, level_code) DO UPDATE SET
          completed=1, reward_xp=?, completed_at=CURRENT_TIMESTAMP
        """, (user_ref, level_code, reward_xp, reward_xp))
    add_xp(user_ref, reward_xp)
    return True

# ---- Streaks ----
def _ensure_user_row(user_ref: str):
    with connect() as c:
        c.execute("""
        INSERT INTO users (user_ref, premium)
        VALUES (?, COALESCE((SELECT premium FROM users WHERE user_ref=?), 0))
        ON CONFLICT(user_ref) DO NOTHING
        """, (user_ref, user_ref))
        c.execute("""
        CREATE TABLE IF NOT EXISTS streaks (
            user_ref TEXT PRIMARY KEY,
            last_active DATE,
            streak_count INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

def apply_daily_streak(user_ref: str):
    """
    Обновляет streak.
    Возвращает (added_bonus, streak_count, is_new_day, mode)
    mode ∈ {"first","continue","reset","same_day"}
    """
    _ensure_user_row(user_ref)
    today = datetime.now(TZ).date()
    with connect() as c:
        row = c.execute("SELECT last_active, streak_count FROM streaks WHERE user_ref=?",
                        (user_ref,)).fetchone()
        if not row:
            c.execute("INSERT INTO streaks (user_ref, last_active, streak_count) VALUES (?, ?, ?)",
                      (user_ref, today.isoformat(), 1))
            return DAILY_BONUS_XP, 1, True, "first"

        last_str, count = row
        last = date.fromisoformat(last_str) if last_str else None

        if last == today:
            return 0, count, False, "same_day"

        if last and (today - last == timedelta(days=1)):
            count += 1
            mode = "continue"
        else:
            count = 1
            mode = "reset" if last else "first"

        c.execute("UPDATE streaks SET last_active=?, streak_count=?, updated_at=CURRENT_TIMESTAMP WHERE user_ref=?",
                  (today.isoformat(), count, user_ref))
        return DAILY_BONUS_XP, count, True, mode

def get_streak(user_ref: str):
    with connect() as c:
        row = c.execute("SELECT last_active, streak_count FROM streaks WHERE user_ref=?", (user_ref,)).fetchone()
        if not row:
            return None, 0
        return row[0], row[1]

# ---- Notifications ----
def init_notifications():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            user_ref TEXT PRIMARY KEY,
            hour INTEGER NOT NULL DEFAULT 10,
            minute INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS notify_log (
            user_ref TEXT,
            send_date DATE,
            PRIMARY KEY (user_ref, send_date)
        )""")

def set_notify(user_ref: str, hour: int, minute: int, enabled: bool):
    with connect() as c:
        c.execute("""
        INSERT INTO notifications (user_ref, hour, minute, enabled)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_ref) DO UPDATE SET
            hour=excluded.hour, minute=excluded.minute,
            enabled=excluded.enabled, updated_at=CURRENT_TIMESTAMP
        """, (user_ref, hour, minute, 1 if enabled else 0))

def get_notify(user_ref: str):
    with connect() as c:
        row = c.execute("SELECT hour, minute, enabled FROM notifications WHERE user_ref=?",
                        (user_ref,)).fetchone()
        if not row:
            return None
        return {"hour": row[0], "minute": row[1], "enabled": bool(row[2])}

def list_due_subscribers(hour: int, minute: int):
    with connect() as c:
        rows = c.execute("SELECT user_ref FROM notifications WHERE enabled=1 AND hour=? AND minute=?",
                         (hour, minute)).fetchall()
        return [r[0] for r in rows]

def was_notified_today(user_ref: str, iso_date: str) -> bool:
    with connect() as c:
        row = c.execute("SELECT 1 FROM notify_log WHERE user_ref=? AND send_date=?",
                        (user_ref, iso_date)).fetchone()
        return bool(row)

def mark_notified_today(user_ref: str, iso_date: str):
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO notify_log (user_ref, send_date) VALUES (?, ?)",
                  (user_ref, iso_date))

# ---- Metrics ----
def init_metrics():
    with connect() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            iso_date DATE NOT NULL DEFAULT (DATE('now')),
            meta TEXT,
            ua TEXT,
            ref TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )""")

def log_event(event: str, meta: str|None, ua: str|None, ref: str|None):
    with connect() as c:
        c.execute("INSERT INTO metrics (event, meta, ua, ref) VALUES (?, ?, ?, ?)",
                  (event, meta, ua, ref))

def daily_counts():
    with connect() as c:
        rows = c.execute("""
        SELECT iso_date, event, COUNT(*) cnt
        FROM metrics
        GROUP BY iso_date, event
        ORDER BY iso_date DESC, event
        """).fetchall()
        return [{"date": r[0], "event": r[1], "count": r[2]} for r in rows]
