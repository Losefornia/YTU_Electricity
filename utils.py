# utils.py
import re
from datetime import datetime, timedelta
from collections import defaultdict

PRICE_PER_KWH = 0.55
DAY_SPLIT_HOUR = 10


def match_key(addr):
    if not addr:
        return ''
    a = addr.upper().replace(' ', '')
    a = a.replace('北校', '').replace('南校', '')
    a = a.replace('号楼', '').replace('楼', '')
    a = a.replace('南', 'S').replace('北', 'N')
    a = a.replace('-', '')
    return a


def convert_dorm_format(addr):
    if not addr:
        return addr
    if 'BS' in addr or '北校' in addr:
        campus = '北校'
        addr = addr.replace('BS', '').replace('北校', '').replace('楼', '')
        match = re.search(r'(\d{2})([A-Z])(\d+)', addr)
        if match:
            building, section, room = match.group(1), match.group(2), str(int(match.group(3)))
            if building in ['13', '14', '15', '16']:
                return f"{campus}{building}{section}-{room}"
            return f"{campus}{building}楼{section}{room}"
        return f"{campus}{addr}"

    campus = '南校'
    if 'NS' in addr or '南校' in addr:
        addr = addr.replace('NS', '').replace('南校', '')
    match = re.search(r'(\d{2})([A-Z])-?0*(\d+)', addr)
    if match:
        addr = f"{match.group(1)}{match.group(2)}-{match.group(3)}"
    return f"{campus}{addr}"


def get_day_boundary(dt, offset_days=0):
    target = dt + timedelta(days=offset_days)
    return target.replace(hour=DAY_SPLIT_HOUR, minute=0, second=0, microsecond=0)


def get_today_start(now=None):
    now = now or datetime.now()
    if now.hour < DAY_SPLIT_HOUR:
        return get_day_boundary(now, -1)
    return get_day_boundary(now, 0)


def get_today_end(now=None):
    now = now or datetime.now()
    if now.hour < DAY_SPLIT_HOUR:
        return get_day_boundary(now, 0)
    return get_day_boundary(now, 1)


def get_yesterday_start(now=None):
    now = now or datetime.now()
    if now.hour < DAY_SPLIT_HOUR:
        return get_day_boundary(now, -2)
    return get_day_boundary(now, -1)


def get_yesterday_end(now=None):
    now = now or datetime.now()
    if now.hour < DAY_SPLIT_HOUR:
        return get_day_boundary(now, -1)
    return get_day_boundary(now, 0)


def calc_usage_with_recharge(balances):
    if len(balances) < 2:
        return 0.0
    start_balance = balances[0]
    current_balance = balances[-1]
    total_charged = 0.0
    for i in range(1, len(balances)):
        diff = balances[i] - balances[i - 1]
        if diff > 0:
            total_charged += diff
    usage = (start_balance + total_charged - current_balance) / PRICE_PER_KWH
    return round(max(0, usage), 2)


def calc_14day_usage(addr, now=None):
    """
    一次查库，返回 {offset_days: usage}。
    offset_days=0 为今天（10:00 日界），1 为昨天，... 13 为第 14 天。
    缺失的 key 不存在，调用方用 .get(i) 判断。
    """
    from . import db
    now = now or datetime.now()

    today_start = get_today_start(now)
    start = today_start - timedelta(days=14)
    start_str = start.strftime("%Y-%m-%d %H:%M:%S")

    rows = db.query_all(
        "SELECT record_time, balance FROM meter_records "
        "WHERE user_addr=? AND record_time>=? ORDER BY record_time ASC",
        (addr, start_str),
    )
    if len(rows) < 2:
        return {}

    # 按 10:00 日界分桶
    buckets = defaultdict(list)
    for r in rows:
        try:
            t = datetime.strptime(r["record_time"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        day_start = t.replace(hour=DAY_SPLIT_HOUR, minute=0, second=0, microsecond=0)
        if t.hour < DAY_SPLIT_HOUR:
            day_start -= timedelta(days=1)
        offset = (today_start - day_start).days
        buckets[offset].append(r["balance"])

    result = {}
    for offset, balances in buckets.items():
        if 0 <= offset < 14 and len(balances) >= 2:
            result[offset] = calc_usage_with_recharge(balances)
    return result


def draw_bar_chart(data, max_bars=12):
    if not data:
        return "暂无数据"
    items = list(data.items())[-max_bars:]
    chart = []
    for time_key, usage in items:
        parts = time_key.split()
        if len(parts) < 2:
            continue
        date = parts[0]
        hour = int(parts[1].split(':')[0])
        next_hour = (hour + 1) % 24
        time_range = f"{date} {hour:02d}:00-{next_hour:02d}:00"
        if usage > 0:
            bar_len = max(1, min(int(usage * 0.8), 8))
            chart.append(f"{time_range} {'█' * bar_len} {usage:.3f}度")
        else:
            chart.append(f"{time_range} 无数据")
    return '\n'.join(chart) if chart else "暂无数据"
