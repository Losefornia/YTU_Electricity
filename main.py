from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import re
import asyncio
from datetime import datetime, timedelta

from . import db
from . import spider
from .utils import (
    convert_dorm_format, draw_bar_chart,
    PRICE_PER_KWH, calc_usage_with_recharge,
)

ALERT_THRESHOLD = 5
WARN_THRESHOLD = 50
FETCH_INTERVAL_MIN = 60   # 一小时爬一次


def _calc_day_usage(addr, day):
    """
    计算某一天（10:00 到次日 10:00）的用电量。
    返回 None 表示数据不足。
    """
    if day.hour < 10:
        start = (day - timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        start = day.replace(hour=10, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    start_str = start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end.strftime("%Y-%m-%d %H:%M:%S")

    rows = db.query_all(
        "SELECT balance FROM meter_records WHERE user_addr=? AND record_time>=? AND record_time<? ORDER BY record_time ASC",
        (addr, start_str, end_str),
    )

    if len(rows) < 2:
        return None

    balances = [r["balance"] for r in rows]
    return calc_usage_with_recharge(balances)


@register("astrbot_plugin_dianfei", "你的名字", "电费查询插件", "1.0.0", "")
class DianFeiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        db.init_tables()
        self._fetch_task = None

    async def initialize(self):
        logger.info("✅ 电管Doro 插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())

    async def _fetch_loop(self):
        """每小时抓一次所有已绑定宿舍"""
        try:
            await spider.fetch_all_bound()
        except Exception as e:
            logger.warning(f"启动抓取异常: {e}")
        while True:
            await asyncio.sleep(FETCH_INTERVAL_MIN * 60)
            try:
                await spider.fetch_all_bound()
            except Exception as e:
                logger.warning(f"定时抓取异常: {e}")

    # ===== 绑定 =====
    @filter.command("绑定")
    async def bind(self, event: AstrMessageEvent):
        openid = event.get_sender_id()
        parts = event.message_str.split()
        if len(parts) < 2:
            yield event.plain_result("📖 格式：绑定 宿舍号\n示例：绑定 NS07N0488")
            return

        addr_raw = parts[1].upper()
        if not re.match(r'^(NS|BS)\d{2}[A-Z]\d{4}$', addr_raw):
            yield event.plain_result(
                f"❌ 宿舍号格式错误：{addr_raw}\n"
                "💡 正确格式：NS07N0488（9位）\n"
                "💡 NS=南校，BS=北校"
            )
            return

        if db.get_bind_addr(openid):
            yield event.plain_result("❌ 你已经绑定过了，先发「解绑」")
            return

        db_addr = convert_dorm_format(addr_raw)
        db.bind_user(openid, db_addr)
        yield event.plain_result(f"✅ 绑定成功\n🏠 宿舍：{db_addr}\n💡 发送「查」查询电量")

        try:
            await spider.fetch_specific([db_addr])
        except Exception as e:
            logger.warning(f"绑定后抓取失败: {e}")

    # ===== 解绑 =====
    @filter.command("解绑")
    async def unbind(self, event: AstrMessageEvent):
        openid = event.get_sender_id()
        addr = db.get_bind_addr(openid)
        if not addr:
            yield event.plain_result("❌ 你还没有绑定宿舍")
            return
        db.unbind_user(openid)
        yield event.plain_result(f"✅ 已解绑宿舍 {addr}")

    # ===== 查询（14 天日用电） =====
    @filter.command("查")
    async def query(self, event: AstrMessageEvent):
        openid = event.get_sender_id()
        addr = db.get_bind_addr(openid)
        if not addr:
            yield event.plain_result("❌ 你还没有绑定宿舍，请私聊发送「绑定 宿舍号」")
            return

        result = db.get_user_balance(addr)
        if not result:
            yield event.plain_result(f"❌ 暂无数据，请等待爬虫采集\n🏠 宿舍：{addr}")
            return

        name = result["user_name"]
        balance = result["balance"]
        record_time = result["record_time"]
        display_name = name[0] + "**" if name and len(name) >= 2 else name or addr

        now = datetime.now()

        today_usage = _calc_day_usage(addr, now)
        yesterday_usage = _calc_day_usage(addr, now - timedelta(days=1))

        days_detail = []
        valid_usages = []
        for i in range(14):
            d = now - timedelta(days=i)
            usage = _calc_day_usage(addr, d)
            label = "今天" if i == 0 else f"{d.month}月{d.day}日"
            if usage is None:
                days_detail.append(f"{label} 无数据")
            else:
                bar = "█" * max(1, min(int(usage * 0.8), 8))
                cost = round(usage * PRICE_PER_KWH, 2)
                days_detail.append(f"{label} {bar} {usage:.2f}度（{cost}元）")
                valid_usages.append(usage)

        if valid_usages:
            avg_usage = round(sum(valid_usages) / len(valid_usages), 2)
            avg_cost = round(avg_usage * PRICE_PER_KWH, 2)
            days_left = int(balance / (avg_usage * PRICE_PER_KWH)) if avg_usage > 0 else None
            days_left_text = f"约 {days_left} 天" if days_left and days_left > 0 else "不足1天"
        else:
            avg_usage = 0.0
            avg_cost = 0.0
            days_left_text = "--"

        status = "🔴 欠费" if balance < 0 else "🟠 预警" if balance < WARN_THRESHOLD else "🟢 正常"
        warn = f"\n⚠️ 余额不足{ALERT_THRESHOLD}元，请及时充值！" if balance < ALERT_THRESHOLD else ""

        detail_text = "\n".join(days_detail)

        yield event.plain_result(
            f"🏠 {addr} {display_name}\n"
            f"💰 余额：{balance} 元\n"
            f"📊 状态：{status}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"⚡ 今日用电：{today_usage if today_usage is not None else '--'} 度\n"
            f"⚡ 昨日用电：{yesterday_usage if yesterday_usage is not None else '--'} 度\n"
            f"📊 近14天日均：{avg_usage} 度/天\n"
            f"💰 日均电费：{avg_cost} 元/天\n"
            f"📅 预计可用：{days_left_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"📊 近14天每日用电（10:00-次日10:00）：\n{detail_text}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 更新时间：{record_time}{warn}"
        )

    # ===== 详情（小时图，仅供参考） =====
    @filter.command("详情")
    async def detail(self, event: AstrMessageEvent):
        openid = event.get_sender_id()
        addr = db.get_bind_addr(openid)
        if not addr:
            yield event.plain_result("❌ 你还没有绑定宿舍")
            return

        result = db.get_user_balance(addr)
        if not result:
            yield event.plain_result("❌ 暂无数据")
            return

        hourly_data = db.get_hourly_usage(addr, 24)
        chart = draw_bar_chart(hourly_data, 12)

        yield event.plain_result(
            f"⚡ {addr} 小时详情\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{chart}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"🕐 更新：{result['record_time']}\n"
            f"📌 小时用电受学校网站数据更新延迟影响，仅供参考\n"
            f"📌 建议以「查」里的 14 天日用电为准"
        )

    # ===== 帮助 =====
    @filter.command("帮助")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "📖 电管Doro 使用指南\n"
            "━━━━━━━━━━━━━━━━\n"
            "📝 绑定：/绑定 NS07N0488\n"
            "⚡ 查询：/查（近14天日用电）\n"
            "📊 详情：/详情（小时图，仅供参考）\n"
            "🔓 解绑：/解绑\n"
            "━━━━━━━━━━━━━━━━\n"
            "💡 NS=南校，BS=北校\n"
            "💡 日用电按 10:00 - 次日 10:00 统计"
        )

    async def terminate(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        logger.info("👋 电管Doro 插件已卸载")
