# utils.py
import re
from datetime import datetime, timedelta
from collections import defaultdict
from io import BytesIO

PRICE_PER_KWH = 0.55
DAY_SPLIT_HOUR = 10


# ========= 字体延迟配置（只做一次） =========
_FONT_READY = False


def _setup_font():
    """首次绘图时初始化 matplotlib 和中文字体。"""
    global _FONT_READY
    if _FONT_READY:
        return

    import matplotlib
    matplotlib.use("Agg")  # 无 GUI 后端，服务器必须
    from matplotlib import font_manager
    import matplotlib.pyplot as plt

    # 按优先级找系统中文字体
    candidates = [
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "Source Han Sans SC",
        "Microsoft YaHei",
        "SimHei",
    ]
    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((c for c in candidates if c in installed), None)
    if chosen:
        plt.rcParams["font.sans-serif"] = [chosen]
    plt.rcParams["axes.unicode_minus"] = False
    _FONT_READY = True


# ========= 地址处理 =========

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


# ========= 时间边界 =========

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


# ========= 用量计算 =========

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


# ========= 绘图 =========

def draw_bar_chart(data, addr=""):
    """
    data: {offset: usage}，来自 calc_14day_usage
    addr: 宿舍地址（用于标题）
    返回 BytesIO(PNG)，失败返回 None
    """
    if not data:
        return None

    _setup_font()  # 延迟初始化 matplotlib + 字体
    import matplotlib.pyplot as plt

    sorted_items = sorted(data.items(), key=lambda x: x[0])
    date_text, week_text, usage_data = [], [], []

    now = datetime.now()
    today_start = get_today_start(now)
    for offset, usage_val in sorted_items:
        day_dt = today_start - timedelta(days=offset)
        date_text.append(f"{day_dt.month:02d}-{day_dt.day:02d}")
        week_text.append(["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day_dt.weekday()])
        usage_data.append(usage_val)

    xtick_labels = [f"{d}\n{w}" for d, w in zip(date_text, week_text)]
    x = list(range(len(date_text)))

    fig, ax = plt.subplots(figsize=(9, 5), dpi=150)
    bars = ax.bar(x, usage_data, color="#87CEEB", alpha=0.7, width=0.5, label="日用电量")
    ax.plot(x, usage_data, marker="o", color="#2E86AB", linewidth=2, markersize=4, label="趋势线")

    for bar, val in zip(bars, usage_data):
        height = bar.get_height()
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height + 0.22,
            f"{val}",
            ha="center",
            va="bottom",
            fontsize=7,
            color="#c82423",
        )

    title_addr = convert_dorm_format(addr) if addr else "宿舍"
    ax.set_title(f"{title_addr} 近14天用电量统计（单位：度）", fontsize=10)
    ax.set_xlabel("日期")
    ax.set_ylabel("用电量(度)")
    ax.set_xticks(x)
    ax.set_xticklabels(xtick_labels, fontsize=7)
    ax.legend(fontsize=7)
    ax.grid(axis="y", alpha=0.3)

    buf = BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf
