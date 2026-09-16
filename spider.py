import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .utils import match_key
from . import db

URL = "https://ydny.ytu.edu.cn/pms/wechat/ytdx/payItemList"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": URL,
}
REQUEST_TIMEOUT = 30

# ===== 爬取节奏参数 =====
MAX_BUFFER_SIZE = 1 * 1024 * 1024   # 缓冲区上限 1MB
PANEL_SLEEP = 0.01                  # 每解析 10 个 panel 歇一下
BATCH_SIZE = 20                     # 每批写库 20 条
BATCH_SLEEP = 1                     # 每批写库之间歇 1 秒


def parse_one_panel(panel_html: str):
    """解析单个 weui-panel，返回一条记录。"""
    soup = BeautifulSoup(panel_html, "html.parser")
    panel = soup.find("div", class_="weui-panel")
    if not panel:
        return None

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

    if user_addr == "未知":
        return None

    return {
        "user_no": user_no,
        "user_name": user_name,
        "user_addr": user_addr,
        "balance": balance,
    }


async def stream_and_parse(client, target_addrs):
    """
    流式读取 HTML，逐块找出完整的 weui-panel 并解析。
    内存可控，适合大量宿舍。
    """
    target_map = {match_key(t): t for t in target_addrs}
    records = []
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    buffer = ""
    panel_pattern = re.compile(
        r'<div class="weui-panel".*?</div>\s*(?=<div class="weui-panel"|</div>\s*<div class="weui-cells")',
        re.DOTALL,
    )

    async with client.stream("GET", URL) as resp:
        if resp.status_code != 200:
            print(f"请求失败: {resp.status_code}")
            return records
        resp.encoding = "utf-8"

        total_panels = 0
        async for chunk in resp.aiter_text():
            buffer += chunk

            while True:
                match = panel_pattern.search(buffer)
                if not match:
                    break
                panel_html = match.group(0)
                buffer = buffer[match.end():]
                total_panels += 1

                item = parse_one_panel(panel_html)
                if not item:
                    continue

                key = match_key(item["user_addr"])
                if key in target_map:
                    matched = target_map[key]
                    records.append((
                        now_str,
                        item["user_no"],
                        item["user_name"],
                        matched,
                        item["balance"],
                    ))

                if total_panels % 10 == 0:
                    await asyncio.sleep(PANEL_SLEEP)

            if len(buffer) > MAX_BUFFER_SIZE:
                buffer = buffer[-512 * 1024:]

    print(f"📄 解析 {total_panels} 个 panel，命中 {len(records)} 条")
    return records


async def fetch_specific(addrs):
    """抓取指定宿舍的余额，写入数据库。"""
    if not addrs:
        return

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=HEADERS) as client:
        records = await stream_and_parse(client, addrs)

    if records:
        for i in range(0, len(records), BATCH_SIZE):
            batch = records[i:i + BATCH_SIZE]
            db.save_records(batch)
            await asyncio.sleep(BATCH_SLEEP)
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
