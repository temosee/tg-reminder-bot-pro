"""
Тесты работы с пулом соединений. Запускать:
    python -X utf8 test_db.py
"""
import os

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("DATABASE_URL", "postgresql://dummy")

from cryptography.fernet import Fernet

os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())

import psycopg2
import crypto
import db
import middleware


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.dead:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        self.conn.queries.append(sql)
        self.conn.params.append(params)
        self.rowcount = 1 if self.conn.rows else 0

    def fetchone(self):
        return self.conn.rows.pop(0) if self.conn.rows else None

    def fetchall(self):
        rows, self.conn.rows = self.conn.rows, []
        return rows


class FakeConn:
    def __init__(self, name, dead=False, rows=None):
        self.name = name
        self.dead = dead
        self.closed = 0
        self.queries = []
        self.params = []
        self.rows = list(rows or [])
        self.rollbacks = 0
        self.commits = 0

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        if self.dead:
            raise psycopg2.OperationalError("connection already closed")
        self.rollbacks += 1

    def commit(self):
        self.commits += 1


class FakePool:
    def __init__(self, conns, maxconn=10):
        self.maxconn = maxconn
        self.queue = list(conns)
        self.returned = []
        self.discarded = []

    def getconn(self):
        if self.queue:
            return self.queue.pop(0)
        return FakeConn("fresh")

    def putconn(self, conn, close=False):
        if close:
            self.discarded.append(conn)
        else:
            self.returned.append(conn)


def use_pool(pool):
    db._pool = pool


def case_alive_connection_used():
    live = FakeConn("live")
    pool = FakePool([live])
    use_pool(pool)
    with db.get_conn() as conn:
        pass
    errors = []
    if conn is not live:
        errors.append(f"выдано не то соединение: {conn.name}")
    if pool.discarded:
        errors.append("живое соединение выброшено из пула")
    if pool.returned != [live]:
        errors.append("соединение не вернулось в пул")
    return errors


def case_dead_connections_discarded():
    dead1, dead2 = FakeConn("dead1", dead=True), FakeConn("dead2", dead=True)
    live = FakeConn("live")
    pool = FakePool([dead1, dead2, live])
    use_pool(pool)
    with db.get_conn() as conn:
        pass
    errors = []
    if conn is not live:
        errors.append(f"выдано мёртвое соединение: {conn.name}")
    if pool.discarded != [dead1, dead2]:
        errors.append("мёртвые соединения не выброшены")
    return errors


def case_rollback_after_read():
    live = FakeConn("live")
    pool = FakePool([live])
    use_pool(pool)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users")
    # без rollback соединение остаётся idle in transaction
    if live.rollbacks < 2:
        return [f"ожидался rollback после работы, получено {live.rollbacks}"]
    return []


def case_rollback_after_failure():
    live = FakeConn("live")
    pool = FakePool([live])
    use_pool(pool)
    before = live.rollbacks
    try:
        with db.get_conn():
            raise RuntimeError("сбой запроса")
    except RuntimeError:
        pass
    errors = []
    if live.rollbacks <= before:
        errors.append("после ошибки транзакция не откачена")
    if pool.discarded:
        errors.append("рабочее соединение выброшено из-за ошибки запроса")
    return errors


def case_broken_connection_not_reused():
    live = FakeConn("live")
    pool = FakePool([live])
    use_pool(pool)
    try:
        with db.get_conn() as conn:
            conn.dead = True
            conn.closed = 2
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
    except psycopg2.OperationalError:
        pass
    if pool.returned:
        return ["оборванное соединение вернулось в пул"]
    if pool.discarded != [live]:
        return ["оборванное соединение не закрыто"]
    return []


def case_pool_exhausted_by_dead_conns():
    dead = [FakeConn(f"dead{i}", dead=True) for i in range(10)]
    pool = FakePool(dead, maxconn=10)
    use_pool(pool)
    with db.get_conn() as conn:
        pass
    if conn.dead:
        return ["не создано новое соединение, когда весь пул мёртв"]
    return []


def case_daily_slot_granted():
    live = FakeConn("live", rows=[(3,)])
    use_pool(FakePool([live]))
    errors = []
    if not db.take_daily_slot(1, 20):
        errors.append("слот не выдан, хотя лимит не исчерпан")
    if live.commits == 0:
        errors.append("изменение не зафиксировано в базе")
    return errors

def case_daily_slot_exhausted():
    # при исчерпанном лимите UPDATE не срабатывает и RETURNING ничего не отдаёт
    live = FakeConn("live", rows=[])
    use_pool(FakePool([live]))
    if db.take_daily_slot(1, 20):
        return ["слот выдан сверх дневного лимита"]
    return []

def case_daily_limit_blocks_user():
    live = FakeConn("live", rows=[(50,)])  # get_active_reminders_count
    use_pool(FakePool([live, FakeConn("live2", rows=[])]))
    allowed, err = middleware.check_new_reminder(1, "ru")
    if allowed:
        return ["пользователь пропущен при исчерпанном лимите активных"]
    if not err:
        return ["не вернулось сообщение об ошибке"]
    return []

def case_limit_survives_restart():
    # счётчик живёт в базе, а не в памяти процесса — перезапуск его не сбрасывает
    if hasattr(middleware, "_daily_counter"):
        return ["дневной счётчик всё ещё хранится в памяти процесса"]
    return []

def case_reminder_stored_encrypted():
    plain = "сходить к врачу"
    live = FakeConn("live", rows=[(1,)])
    use_pool(FakePool([live]))
    db.add_reminder(1, 1, plain, "once", next_fire=0)
    stored = live.params[-1][2]
    errors = []
    if stored == plain:
        errors.append("текст напоминания ушёл в базу открытым")
    if crypto.decrypt(stored) != plain:
        errors.append(f"зашифрованное не расшифровывается обратно: {stored!r}")
    return errors


def case_note_stored_encrypted():
    plain = "пароль от вайфая 12345"
    live = FakeConn("live", rows=[(1,)])
    use_pool(FakePool([live]))
    db.add_note(1, plain)
    stored = live.params[-1][1]
    errors = []
    if stored == plain:
        errors.append("текст заметки ушёл в базу открытым")
    if crypto.decrypt(stored) != plain:
        errors.append("заметка не расшифровывается обратно")
    return errors


def case_legacy_plaintext_still_readable():
    # записи, сделанные до включения шифрования, должны читаться как есть
    if crypto.decrypt("старое напоминание") != "старое напоминание":
        return ["старые незашифрованные записи перестали читаться"]
    return []


def case_key_paste_mistakes():
    good = Fernet.generate_key().decode()
    variants = {
        "как есть": good,
        "строка из .env целиком": f"ENCRYPTION_KEY={good}",
        "в кавычках": f'"{good}"',
        "с пробелами": f"  {good}  ",
    }
    errors = []
    for name, raw in variants.items():
        if crypto._clean_key(raw) != good:
            errors.append(f"ключ не распознан, когда вставлен {name}")
    return errors


def case_works_without_key():
    saved = crypto._fernet
    crypto._fernet = None
    try:
        if crypto.encrypt("текст") != "текст" or crypto.decrypt("текст") != "текст":
            return ["без ключа бот должен работать с открытым текстом, а не падать"]
    finally:
        crypto._fernet = saved
    return []


cases = [
    ("живое соединение отдаётся как есть", case_alive_connection_used),
    ("мёртвые соединения выбрасываются", case_dead_connections_discarded),
    ("rollback после чтения", case_rollback_after_read),
    ("rollback после ошибки запроса", case_rollback_after_failure),
    ("оборванное соединение не переиспользуется", case_broken_connection_not_reused),
    ("весь пул мёртв — берём новое", case_pool_exhausted_by_dead_conns),
    ("дневной слот выдаётся в пределах лимита", case_daily_slot_granted),
    ("дневной слот не выдаётся сверх лимита", case_daily_slot_exhausted),
    ("лимит активных напоминаний блокирует", case_daily_limit_blocks_user),
    ("дневной счётчик переехал из памяти в базу", case_limit_survives_restart),
    ("напоминание шифруется перед записью", case_reminder_stored_encrypted),
    ("заметка шифруется перед записью", case_note_stored_encrypted),
    ("старые открытые записи читаются", case_legacy_plaintext_still_readable),
    ("кривая вставка ключа не ломает бота", case_key_paste_mistakes),
    ("без ключа бот не падает", case_works_without_key),
]

ok = fail = 0
for name, case in cases:
    errs = case()
    if errs:
        fail += 1
        print(f"[FAIL] {name}")
        for e in errs:
            print(f"       {e}")
    else:
        ok += 1
        print(f"[OK]   {name}")

print(f"\nИтого: {ok} OK, {fail} FAIL")
if fail:
    exit(1)
