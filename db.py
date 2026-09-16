# db.py
import sqlite3
import os
import time
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path("data/plugin_data/astrbot_plugin_dianfei")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "meters_data.db"

KEEP_DAYS = 15
MAX_DB_SIZE_MB = 50
KEEP_RECORDS = 2000


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_tables():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meter_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_time TEXT,
            user_no TEXT,
            user_name TEXT,
            user_addr TEXT,
            balance REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_bind (
            openid TEXT PRIMARY KEY,
            user_addr TEXT NOT NULL,
            bind_time TEXT,
            umo TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alert_ignore (
            user_addr TEXT PRIMARY KEY,
            ignore_since TEXT
        )
    """)
    # 兼容旧表：如果没有 umo 列就补上
    try:
        conn.execute("ALTER TABLE user_bind ADD COLUMN umo TEXT")
    except Exception:
        pass
    conn.commit()
    conn.close()


def query_one(sql, params=None):
    conn = get_db()
    row = conn.execute(sql, params or ()).fetchone()
    conn.close()
    return row


def query_all(sql, params=None):
    conn = get_db()
    rows = conn.execute(sql, params or ()).fetchall()
    conn.close()
    return rows


def execute(sql, params=None):
    conn = get_db()
    conn.execute(sql, params or ())
    conn.commit()
    conn.close()


def get_bind_addr(openid):
    row = query_one("SELECT user_addr FROM user_bind WHERE openid=?", (openid,))
    return row["user_addr"] if row else None


def get_bind_users(addr):
    rows = query_all("SELECT openid FROM user_bind WHERE user_addr=?", (addr,))
    return [r["openid"] for r in rows]


def get_bind_umos_and_openids(addr):
    """返回 [(umo, openid), ...]"""
    rows = query_all(
        "SELECT umo, openid FROM user_bind WHERE user_addr=? AND umo IS NOT NULL",
        (addr,),
    )
    return [(r["umo"], r["openid"]) for r in rows]


def bind_user(openid, addr, umo=None):
    execute(
        "INSERT OR REPLACE INTO user_bind (openid, user_addr, bind_time, umo) VALUES (?, ?, ?, ?)",
        (openid, addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), umo),
    )


def unbind_user(openid):
    execute("DELETE FROM user_bind WHERE openid=?", (openid,))


def get_user_balance(addr):
    return query_one(
        "SELECT user_name, balance, record_time FROM meter_records "
        "WHERE user_addr=? ORDER BY id DESC LIMIT 1",
        (addr,),
    )


def get_balance_history(addr, hours=24):
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    return query_all(
        "SELECT record_time, balance FROM meter_records "
        "WHERE user_addr=? AND record_time>=? ORDER BY record_time ASC",
        (addr, cutoff),
    )


def get_hourly_usage(addr, hours=24):
    from .utils import PRICE_PER_KWH
    rows = get_balance_history(addr, hours)
    if len(rows) < 2:
        return {}

    hourly_data = {}
    for i in range(1, len(rows)):
        prev_time = datetime.strptime(rows[i - 1]["record_time"], "%Y-%m-%d %H:%M:%S")
        curr_time = datetime.strptime(rows[i]["record_time"], "%Y-%m-%d %H:%M:%S")
        prev_balance = rows[i - 1]["balance"]
        curr_balance = rows[i]["balance"]

        diff = prev_balance - curr_balance
        if diff <= 0:
            continue

        hours_span = max(1, int((curr_time - prev_time).total_seconds() / 3600))
        for h in range(hours_span):
            t = prev_time + timedelta(hours=h)
            key = t.strftime("%m-%d %H:00")
            hourly_data[key] = hourly_data.get(key, 0) + (diff / PRICE_PER_KWH / hours_span)

    return hourly_data


def save_records(records):
    if not records:
        return
    conn = get_db()
    conn.executemany(
        "INSERT INTO meter_records (record_time, user_no, user_name, user_addr, balance) "
        "VALUES (?, ?, ?, ?, ?)",
        records,
    )
    conn.commit()
    conn.close()


def get_bound_addrs():
    rows = query_all("SELECT DISTINCT user_addr FROM user_bind")
    return [r["user_addr"] for r in rows]


def clean_old_data():
    cutoff = (datetime.now() - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cursor = conn.execute("DELETE FROM meter_records WHERE record_time < ?", (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clean_unbound_old_data():
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    cursor = conn.execute("""
        DELETE FROM meter_records 
        WHERE user_addr NOT IN (SELECT user_addr FROM user_bind)
        AND record_time < ?
    """, (cutoff,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def clean_db_by_size():
    if not os.path.exists(DB_PATH):
        return
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
    if db_size <= MAX_DB_SIZE_MB:
        return

    conn = get_db()
    cursor = conn.execute("SELECT COUNT(*) FROM meter_records")
    total = cursor.fetchone()[0]
    if total <= KEEP_RECORDS:
        conn.close()
        return

    row = conn.execute(
        f"SELECT id FROM meter_records ORDER BY id DESC LIMIT 1 OFFSET {KEEP_RECORDS}"
    ).fetchone()
    if not row:
        conn.close()
        return
    cutoff_id = row[0]

    while True:
        cursor = conn.execute(
            "DELETE FROM meter_records WHERE id IN ("
            "  SELECT id FROM meter_records WHERE id < ? LIMIT 500"
            ")",
            (cutoff_id,),
        )
        deleted = cursor.rowcount
        conn.commit()
        if deleted == 0:
            break
        time.sleep(0.1)

    print("🧹 按大小清理完成")
    conn.close()


# ===== 忽略预警相关 =====

def set_ignore(addr):
    execute(
        "INSERT OR REPLACE INTO alert_ignore (user_addr, ignore_since) VALUES (?, ?)",
        (addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )


def remove_ignore(addr):
    execute("DELETE FROM alert_ignore WHERE user_addr=?", (addr,))


def is_ignored(addr):
    row = query_one("SELECT 1 FROM alert_ignore WHERE user_addr=?", (addr,))
    return row is not None
