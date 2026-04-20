import time
import os
import psycopg2
import psycopg2.extras
from config import DATABASE_URL

def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id          BIGINT PRIMARY KEY,
                    username         TEXT,
                    first_name       TEXT,
                    timezone         TEXT DEFAULT 'UTC',
                    registered_at    REAL NOT NULL,
                    is_banned        INTEGER DEFAULT 0,
                    reminders_created INTEGER DEFAULT 0
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id               SERIAL PRIMARY KEY,
                    user_id          BIGINT NOT NULL,
                    chat_id          BIGINT NOT NULL,
                    message          TEXT NOT NULL,
                    type             TEXT NOT NULL,
                    interval_seconds INTEGER,
                    next_fire        REAL,
                    created_at       REAL NOT NULL,
                    category         TEXT DEFAULT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS notes (
                    id         SERIAL PRIMARY KEY,
                    user_id    BIGINT NOT NULL,
                    text       TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
            """)
        conn.commit()

def _row_to_dict(cursor, row):
    if row is None:
        return None
    cols = [desc[0] for desc in cursor.description]
    return dict(zip(cols, row))

def _rows_to_dicts(cursor, rows):
    cols = [desc[0] for desc in cursor.description]
    return [dict(zip(cols, row)) for row in rows]

# users

def register_user(user_id: int, username: str, first_name: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
            if cur.fetchone():
                return False
            cur.execute(
                """INSERT INTO users (user_id, username, first_name, registered_at)
                   VALUES (%s, %s, %s, %s)""",
                (user_id, username, first_name, time.time())
            )
        conn.commit()
        return True

def get_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return _row_to_dict(cur, row)

def update_timezone(user_id: int, timezone: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET timezone = %s WHERE user_id = %s",
                (timezone, user_id)
            )
        conn.commit()

def ban_user(user_id: int, banned: bool = True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_banned = %s WHERE user_id = %s",
                (1 if banned else 0, user_id)
            )
        conn.commit()

def get_all_users_count() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]

def increment_reminders_created(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET reminders_created = reminders_created + 1 WHERE user_id = %s",
                (user_id,)
            )
        conn.commit()

# reminders

def add_reminder(user_id, chat_id, message, type_,
                 interval_seconds=None, next_fire=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reminders
                   (user_id, chat_id, message, type, interval_seconds, next_fire, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (user_id, chat_id, message, type_,
                 interval_seconds, next_fire, time.time())
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id

def get_reminders(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM reminders WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return _rows_to_dicts(cur, cur.fetchall())

def get_all_reminders():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reminders ORDER BY id")
            return _rows_to_dicts(cur, cur.fetchall())

def get_active_reminders_count(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM reminders WHERE user_id = %s", (user_id,)
            )
            return cur.fetchone()[0]

def get_total_reminders_count() -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM reminders")
            return cur.fetchone()[0]

def delete_reminder(reminder_id, user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reminders WHERE id = %s AND user_id = %s",
                (reminder_id, user_id)
            )
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted

def delete_reminder_by_id(reminder_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
        conn.commit()

def update_next_fire(reminder_id, next_fire):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reminders SET next_fire = %s WHERE id = %s",
                (next_fire, reminder_id)
            )
        conn.commit()

# notes

def add_note(user_id: int, text: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notes (user_id, text, created_at) VALUES (%s, %s, %s) RETURNING id",
                (user_id, text, time.time())
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id

def get_notes(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM notes WHERE user_id = %s ORDER BY id",
                (user_id,)
            )
            return _rows_to_dicts(cur, cur.fetchall())

def delete_note(note_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notes WHERE id = %s AND user_id = %s",
                (note_id, user_id)
            )
            deleted = cur.rowcount > 0
        conn.commit()
        return deleted

def get_notes_count(user_id: int) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM notes WHERE user_id = %s", (user_id,)
            )
            return cur.fetchone()[0]

# stats

def get_user_stats(user_id: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reminders_created FROM users WHERE user_id = %s", (user_id,)
            )
            user = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) FROM reminders WHERE user_id = %s", (user_id,)
            )
            active = cur.fetchone()[0]
    return {
        "total_created": user[0] if user else 0,
        "active": active,
    }
