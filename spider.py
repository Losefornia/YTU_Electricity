import httpx
from bs4 import BeautifulSoup
from datetime import datetime
from .utils import match_key
from . import db

URL = "https://ydny.ytu.edu.cn/pms/wechat/ytdx/payItemList"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": URL,
}
REQUEST_TIMEOUT = 15


def parse_html(html_text: str):
    soup = BeautifulSoup(html_text, "html.parser")
    panels = soup.find_all("div", class_="weui-panel")
    if panels and "综合能源缴费查询及充值服务" in panels[0].get_text():
        panels = panels[1:]

    all_items = []
    for panel in panels:
        user_no, user_name, user_addr, balance = "未知", "未知", "未知", 0.0
        for cell in panel.find_all("div", class_="weui-cell"):
            bd = cell.find("div", class_="weui-cell__bd")
            ft = cell.find("div", class_="weui-cell__ft")
            if bd and ft:
                title = bd.get_text(strip=True)
                val = ft.get_text(strip=True)
                if "用户编号" in title:
                    user_no = val
                elif "用户姓名" in title:
                    user_name = val
                elif "用户地址" in title:
                    user_addr = val
                elif "实时余额" in title:
                    try:
                        balance = float(val)
                    except (ValueError, TypeError):
                        balance = 0.0
        all_items.append({
            "user_no": user_no,
            "user_name": user_name,
            "user_addr": user_addr,
            "balance": balance,
        })
    return all_items


async def fetch_specific(addrs):
    """抓取指定宿舍的余额，写入数据库。"""
    if not addrs:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=HEADERS) as client:
            resp = await client.get(URL)
            resp.encoding = "utf-8"
            if resp.status_code != 200:
                print(f"请求失败: {resp.status_code}")
                return
            all_items = parse_html(resp.text)
    except Exception as e:
        print(f"爬虫请求失败: {e}")
        return

    target_map = {match_key(t): t for t in addrs}
    records = []
    for item in all_items:
        key = match_key(item["user_addr"])
        if key in target_map:
            matched = target_map[key]
            records.append((now_str, item["user_no"], item["user_name"], matched, item["balance"]))

    if records:
        db.save_records(records)
        print(f"✅ 入库 {len(records)} 条")


async def fetch_all_bound():
    """抓取所有已绑定宿舍的余额。"""
    addrs = db.get_bound_addrs()
    if not addrs:
        return
    await fetch_specific(addrs)
    # 清理旧数据
    deleted1 = db.clean_old_data()
    deleted2 = db.clean_unbound_old_data()
    db.clean_db_by_size()
    if deleted1 or deleted2:
        print(f"🧹 清理旧数据: {deleted1 + deleted2} 条")