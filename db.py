import time
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool
import crypto
from config import DATABASE_URL

from contextlib import contextmanager

_pool = None
_pool_lock = threading.Lock()

def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    2, 10, DATABASE_URL,
                    keepalives=1,
                    keepalives_idle=30,
                    keepalives_interval=10,
                    keepalives_count=5,
                )
    return _pool

_checked = {}
_CHECK_AFTER = 20

def _alive(conn) -> bool:
    if conn.closed:
        return False
    # только что работали через это соединение — лишний рейс до сервера ни к чему
    if time.time() - _checked.get(id(conn), 0) < _CHECK_AFTER:
        return True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
        _checked[id(conn)] = time.time()
        return True
    except psycopg2.Error:
        _checked.pop(id(conn), None)
        return False

@contextmanager
def get_conn():
    pool = _get_pool()
    # после простоя пулер рвёт коннекты, а пул продолжает их раздавать
    conn = None
    for _ in range(pool.maxconn):
        candidate = pool.getconn()
        if _alive(candidate):
            conn = candidate
            break
        _checked.pop(id(candidate), None)
        pool.putconn(candidate, close=True)
    if conn is None:
        conn = pool.getconn()
    try:
        yield conn
    finally:
        try:
            if not conn.closed:
                conn.rollback()
                _checked[id(conn)] = time.time()
            else:
                _checked.pop(id(conn), None)
        except psycopg2.Error:
            _checked.pop(id(conn), None)
        pool.putconn(conn, close=bool(conn.closed))

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
                    next_fire        DOUBLE PRECISION,
                    created_at       DOUBLE PRECISION NOT NULL,
                    category         TEXT DEFAULT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id   BIGINT NOT NULL,
                    day       DATE NOT NULL,
                    reminders INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, day)
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
            cur.execute("""
                ALTER TABLE users ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ru'
            """)
            cur.execute("""
                ALTER TABLE reminders
                    ALTER COLUMN next_fire TYPE DOUBLE PRECISION,
                    ALTER COLUMN created_at TYPE DOUBLE PRECISION
            """)
            cur.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS days_of_week TEXT")
            cur.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS at_time TEXT")
            cur.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS until DOUBLE PRECISION")
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
            cur.execute(
                """INSERT INTO users (user_id, username, first_name, registered_at)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (user_id) DO NOTHING""",
                (user_id, username, first_name, time.time())
            )
            created = cur.rowcount > 0
        conn.commit()
        return created

def get_user(user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            row = cur.fetchone()
            return _row_to_dict(cur, row)

def update_language(user_id: int, language: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET language = %s WHERE user_id = %s",
                (language, user_id)
            )
        conn.commit()

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
                 interval_seconds=None, next_fire=None,
                 days_of_week=None, at_time=None, until=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO reminders
                   (user_id, chat_id, message, type, interval_seconds, next_fire, created_at,
                    days_of_week, at_time, until)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (user_id, chat_id, crypto.encrypt(message), type_,
                 interval_seconds, next_fire, time.time(),
                 days_of_week, at_time, until)
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
            return crypto.decrypt_field(_rows_to_dicts(cur, cur.fetchall()), "message")

def get_reminder(reminder_id: int, user_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM reminders WHERE id = %s AND user_id = %s",
                (reminder_id, user_id)
            )
            row = _row_to_dict(cur, cur.fetchone())
            return crypto.decrypt_field([row], "message")[0] if row else None

def get_all_reminders():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM reminders ORDER BY id")
            return crypto.decrypt_field(_rows_to_dicts(cur, cur.fetchall()), "message")

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

def delete_expired_reminders():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reminders WHERE until IS NOT NULL AND until < %s", (time.time(),))
        conn.commit()

def update_next_fire(reminder_id, next_fire):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE reminders SET next_fire = %s WHERE id = %s",
                (next_fire, reminder_id)
            )
        conn.commit()

def take_daily_slot(user_id: int, limit: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO usage_daily (user_id, day, reminders)
                   VALUES (%s, CURRENT_DATE, 1)
                   ON CONFLICT (user_id, day) DO UPDATE
                       SET reminders = usage_daily.reminders + 1
                     WHERE usage_daily.reminders < %s
                   RETURNING reminders""",
                (user_id, limit)
            )
            taken = cur.fetchone() is not None
        conn.commit()
        return taken

def purge_old_usage(days: int = 7):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM usage_daily WHERE day < CURRENT_DATE - %s::integer",
                (days,)
            )
        conn.commit()

# notes

def add_note(user_id: int, text: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notes (user_id, text, created_at) VALUES (%s, %s, %s) RETURNING id",
                (user_id, crypto.encrypt(text), time.time())
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
            return crypto.decrypt_field(_rows_to_dicts(cur, cur.fetchall()), "text")

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
