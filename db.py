import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path("data/plugin_data/astrbot_plugin_dianfei")
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "meters_data.db"

KEEP_DAYS = 15
MAX_DB_SIZE_MB = 50
KEEP_RECORDS = 2000


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
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
            bind_time TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_bind_addr(openid):
    conn = get_db()
    row = conn.execute("SELECT user_addr FROM user_bind WHERE openid=?", (openid,)).fetchone()
    conn.close()
    return row["user_addr"] if row else None


def get_bind_users(addr):
    conn = get_db()
    rows = conn.execute("SELECT openid FROM user_bind WHERE user_addr=?", (addr,)).fetchall()
    conn.close()
    return [r["openid"] for r in rows]


def bind_user(openid, addr):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO user_bind (openid, user_addr, bind_time) VALUES (?, ?, ?)",
        (openid, addr, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def unbind_user(openid):
    conn = get_db()
    conn.execute("DELETE FROM user_bind WHERE openid=?", (openid,))
    conn.commit()
    conn.close()


def get_user_balance(addr):
    conn = get_db()
    row = conn.execute(
        "SELECT user_name, balance, record_time FROM meter_records "
        "WHERE user_addr=? ORDER BY id DESC LIMIT 1",
        (addr,)
    ).fetchone()
    conn.close()
    return row


def get_balance_history(addr, hours=24):
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    rows = conn.execute(
        "SELECT record_time, balance FROM meter_records "
        "WHERE user_addr=? AND record_time>=? ORDER BY record_time ASC",
        (addr, cutoff)
    ).fetchall()
    conn.close()
    return rows


def get_hourly_usage(addr, hours=24):
    from .utils import PRICE_PER_KWH
    rows = get_balance_history(addr, hours)
    if len(rows) < 2:
        return {}
    hourly_data = {}
    for i in range(1, len(rows)):
        curr_time = datetime.strptime(rows[i]["record_time"], "%Y-%m-%d %H:%M:%S")
        hour_key = curr_time.strftime("%m-%d %H:00")
        diff = rows[i - 1]["balance"] - rows[i]["balance"]
        if diff > 0:
            usage = round(diff / PRICE_PER_KWH, 2)
            hourly_data[hour_key] = hourly_data.get(hour_key, 0) + usage
    return hourly_data


def save_records(records):
    if not records:
        return
    conn = get_db()
    conn.executemany(
        "INSERT INTO meter_records (record_time, user_no, user_name, user_addr, balance) "
        "VALUES (?, ?, ?, ?, ?)",
        records
    )
    conn.commit()
    conn.close()


def get_bound_addrs():
    conn = get_db()
    rows = conn.execute("SELECT DISTINCT user_addr FROM user_bind").fetchall()
    conn.close()
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
    import os
    if not os.path.exists(DB_PATH):
        return
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
    if db_size <= MAX_DB_SIZE_MB:
        return
    conn = get_db()
    cursor = conn.execute("SELECT COUNT(*) FROM meter_records")
    total = cursor.fetchone()[0]
    if total > KEEP_RECORDS:
        row = conn.execute(
            f"SELECT id FROM meter_records ORDER BY id DESC LIMIT 1 OFFSET {KEEP_RECORDS}"
        ).fetchone()
        if row:
            cutoff_id = row[0]
            cursor = conn.execute("DELETE FROM meter_records WHERE id < ?", (cutoff_id,))
            deleted = cursor.rowcount
            conn.commit()
            print(f"🧹 按大小清理：删除了 {deleted} 条旧记录")
    conn.close()