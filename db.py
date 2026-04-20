import sqlite3
import time
from config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id          INTEGER PRIMARY KEY,
                username         TEXT,
                first_name       TEXT,
                timezone         TEXT DEFAULT 'UTC',
                registered_at    REAL NOT NULL,
                is_banned        INTEGER DEFAULT 0,
                reminders_created INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                chat_id          INTEGER NOT NULL,
                message          TEXT NOT NULL,
                type             TEXT NOT NULL,
                interval_seconds INTEGER,
                next_fire        REAL,
                created_at       REAL NOT NULL,
                category         TEXT DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                text       TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        # Миграция: добавить category если таблица уже существует без него
        try:
            conn.execute("ALTER TABLE reminders ADD COLUMN category TEXT DEFAULT NULL")
        except Exception:
            pass

# users

def register_user(user_id: int, username: str, first_name: str) -> bool:
    """Создаёт запись если не существует. Возвращает True если новый."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO users (user_id, username, first_name, registered_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, username, first_name, time.time())
        )
        return True

def get_user(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

def update_timezone(user_id: int, timezone: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET timezone = ? WHERE user_id = ?",
            (timezone, user_id)
        )

def ban_user(user_id: int, banned: bool = True):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET is_banned = ? WHERE user_id = ?",
            (1 if banned else 0, user_id)
        )

def get_all_users_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

def increment_reminders_created(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET reminders_created = reminders_created + 1 WHERE user_id = ?",
            (user_id,)
        )

# reminders

def add_reminder(user_id, chat_id, message, type_,
                 interval_seconds=None, next_fire=None):
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO reminders
               (user_id, chat_id, message, type, interval_seconds, next_fire, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, message, type_,
             interval_seconds, next_fire, time.time())
        )
        return cursor.lastrowid

def get_reminders(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE user_id = ? ORDER BY id",
            (user_id,)
        ).fetchall()

def get_all_reminders():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM reminders ORDER BY id").fetchall()

def get_active_reminders_count(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

def get_total_reminders_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]

def delete_reminder(reminder_id, user_id):
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?",
            (reminder_id, user_id)
        )
        return cursor.rowcount > 0

def delete_reminder_by_id(reminder_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))

def update_next_fire(reminder_id, next_fire):
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET next_fire = ? WHERE id = ?",
            (next_fire, reminder_id)
        )

# notes

def add_note(user_id: int, text: str) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (user_id, text, created_at) VALUES (?, ?, ?)",
            (user_id, text, time.time())
        )
        return cursor.lastrowid

def get_notes(user_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE user_id = ? ORDER BY id",
            (user_id,)
        ).fetchall()

def delete_note(note_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?",
            (note_id, user_id)
        )
        return cursor.rowcount > 0

def get_notes_count(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM notes WHERE user_id = ?", (user_id,)
        ).fetchone()[0]

# stats

def get_user_stats(user_id: int) -> dict:
    with get_conn() as conn:
        user = conn.execute(
            "SELECT reminders_created FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        active = conn.execute(
            "SELECT COUNT(*) FROM reminders WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    return {
        "total_created": user["reminders_created"] if user else 0,
        "active": active,
    }
