"""
Тесты работы с пулом соединений. Запускать:
    python -X utf8 test_db.py
"""
import os

os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("DATABASE_URL", "postgresql://dummy")

import psycopg2
import db


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if self.conn.dead:
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        self.conn.queries.append(sql)


class FakeConn:
    def __init__(self, name, dead=False):
        self.name = name
        self.dead = dead
        self.closed = 0
        self.queries = []
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
        with db.get_conn() as conn:
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


cases = [
    ("живое соединение отдаётся как есть", case_alive_connection_used),
    ("мёртвые соединения выбрасываются", case_dead_connections_discarded),
    ("rollback после чтения", case_rollback_after_read),
    ("rollback после ошибки запроса", case_rollback_after_failure),
    ("оборванное соединение не переиспользуется", case_broken_connection_not_reused),
    ("весь пул мёртв — берём новое", case_pool_exhausted_by_dead_conns),
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
