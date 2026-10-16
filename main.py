# main.py
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import re
import asyncio
from datetime import datetime, timedelta

from . import db
from . import spider
from .utils import (
    convert_dorm_format, draw_bar_chart,
    PRICE_PER_KWH, calc_14day_usage,
)

ALERT_THRESHOLD = 5
WARN_THRESHOLD = 15
FETCH_INTERVAL_MIN = 60

# ===== 允许使用的群聊白名单（已修正第二个群ID） =====
ALLOWED_GROUPS = [
    "default_1905605993:GroupMessage:8136D3D9580E7AD9FB0B516C4E5FDF1",
    "default_1905605993:GroupMessage:E13B720565366E510BCBCA5E16BA84E0",
]


def is_allowed(event: AstrMessageEvent):
    return event.unified_id in ALLOWED_GROUPS


@register("YTU_Electricity", "YTU电费", "烟大电费查询插件", "1.0.0")
class DianFeiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.addr_map = {}
        self._fetch_task = None

    async def initialize(self):
        self._fetch_task = asyncio.create_task(self._fetch_loop())

    async def _on_task_done(self, task):
        try:
            await task
        except Exception as e:
            logger.error(f"抓取任务异常: {e}")

    async def _fetch_loop(self):
        while True:
            await asyncio.sleep(FETCH_INTERVAL_MIN * 60)
            try:
                await self._safe_fetch()
            except Exception as e:
                logger.error(f"定时抓取失败: {e}")

    async def _safe_fetch(self):
        all_addrs = db.get_all_addr()
        for addr in all_addrs:
            try:
                balance = await spider.fetch_balance(addr)
                if balance is not None:
                    db.insert_record(addr, balance)
                    await self._check_alerts(addr, balance)
            except Exception as e:
                logger.warning(f"抓取 {addr} 失败 {e}")

    async def _check_alerts(self, addr, balance):
        last_alert = db.get_last_alert(addr)
        if balance <= ALERT_THRESHOLD:
            if last_alert != "alert":
                await self._send_alert(addr, balance, "alert")
                db.set_last_alert(addr, "alert")
        elif balance <= WARN_THRESHOLD:
            if last_alert != "warn":
                await self._send_alert(addr, balance, "warn")
                db.set_last_alert(addr, "warn")
        else:
            db.set_last_alert(addr, "ok")

    async def _send_alert(self, addr, balance, level):
        name = convert_dorm_format(addr)
        if level == "alert":
            msg = f"【⚠️电费紧急提醒】{name} 余额不足！当前余额：{balance:.2f}元，请尽快充值！"
        else:
            msg = f"【💡电费预警】{name} 电费即将不足，当前余额：{balance:.2f}元，留意用电。"
        for gid in ALLOWED_GROUPS:
            await self.context.send_message(gid, MessageChain([Plain(msg)]))

    def get_addr(self, event: AstrMessageEvent):
        uid = event.unified_id
        return self.addr_map.get(uid)

    def get_electric_text(self, addr):
        recs_14 = calc_14day_usage(addr)
        balance = db.get_latest_balance(addr)
        if balance is None:
            return "❌ 暂无该宿舍电费数据"

        if not recs_14 or len(recs_14) < 2:
            return f"""🏠 {convert_dorm_format(addr)}
💰 余额：{balance:.2f} 元
📊 状态：{"🟢正常" if balance >= WARN_THRESHOLD else "🟡预警" if balance >= ALERT_THRESHOLD else "🔴欠费"}
━━━━━━━━━━━━━━━━
⚠️ 近14天数据不足，无法计算日均用电"""

        usage_list = list(recs_14.values())
        today_use = usage_list[-1]
        yesterday_use = usage_list[-2]
        avg_use = round(sum(usage_list)/len(usage_list),3)
        avg_cost = round(avg_use * PRICE_PER_KWH,2)
        day_left = round(balance / avg_cost,0) if avg_cost>0 else 0

        if balance >= WARN_THRESHOLD:
            status = "🟢 正常"
        elif balance >= ALERT_THRESHOLD:
            status = "🟡 预警"
        else:
            status = "🔴 欠费"

        text = f"""🏠 {convert_dorm_format(addr)}
💰 余额：{balance:.2f} 元
📊 状态：{status}
━━━━━━━━━━━━━━━━
⚡ 今日用电：{today_use:.3f} 度
⚡ 昨日用电：{yesterday_use:.3f} 度
📊 近14天日均：{avg_use:.3f} 度/天
💰 日均电费：{avg_cost} 元/天
📅 预计可用：约 {day_left:.0f} 天
━━━━━━━━━━━━━━━━
📊 近14天每日用电（10:00-次日10:00）："""
        sorted_items = sorted(recs_14.items(), key=lambda x:x[0])
        now = datetime.now()
        today_start = get_today_start(now)
        for offset, u in sorted_items:
            day_dt = today_start - timedelta(days=offset)
            d_str = f"{day_dt.month:02d}-{day_dt.day:02d}"
            cost = round(u * PRICE_PER_KWH,2)
            text += f"\n{d_str} █ {u:.3f}度（{cost}元）"
        text += "\n━━━━━━━━━━━━━━━━"
        return text

    @filter.command("绑定宿舍")
    async def bind_dorm(self, event: AstrMessageEvent, dorm_addr):
        if not is_allowed(event):
            yield event.plain_result("无权使用")
            return
        addr_key = convert_dorm_format(dorm_addr)
        self.addr_map[event.unified_id] = addr_key
        yield event.plain_result(f"✅ 已绑定宿舍：{addr_key}")

    @filter.command("电费")
    async def query_electric(self, event: AstrMessageEvent):
        if not is_allowed(event):
            yield event.plain_result("无权使用")
            return
        addr = self.get_addr(event)
        if not addr:
            yield event.plain_result("❌ 请先使用 /绑定宿舍 绑定宿舍")
            return
        text = self.get_electric_text(addr)
        yield event.plain_result(text)

    @filter.command("电费图")
    async def cmd_electric_img(self, event: AstrMessageEvent):
        if not is_allowed(event):
            yield event.plain_result("无权使用")
            return
        addr = self.get_addr(event)
        if not addr:
            yield event.plain_result("❌ 请先使用 /绑定宿舍 绑定宿舍")
            return
        try:
            data_14 = calc_14day_usage(addr)
            if not data_14:
                yield event.plain_result("❌ 14天用电数据不足，无法绘图")
                return
            img_buf = draw_bar_chart(data_14)
            text_report = self.get_electric_text(addr)
            yield event.plain_result(text_report)
            yield event.image_result(img_buf.getvalue())
        except Exception as e:
            logger.error(f"绘图异常: {e}")
            yield event.plain_result(f"❌ 绘图失败：{str(e)}")
