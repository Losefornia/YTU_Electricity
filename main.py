from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import re
import asyncio

from . import db
from . import spider
from .utils import (
    convert_dorm_format, draw_bar_chart,
    PRICE_PER_KWH,
)

ALERT_THRESHOLD = 5
WARN_THRESHOLD = 50
FETCH_INTERVAL_MIN = 30


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
        """每 30 分钟抓一次所有已绑定宿舍"""
        # 启动时先抓一次
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

        # 后台抓一次，让数据尽快出现
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

    # ===== 查询 =====
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
        status = "🔴 欠费" if balance < 0 else "🟠 预警" if balance < WARN_THRESHOLD else "🟢 正常"
        warn = f"\n⚠️ 余额不足{ALERT_THRESHOLD}元，请及时充值！" if balance < ALERT_THRESHOLD else ""

        yield event.plain_result(
            f"🏠 {addr} {display_name}\n"
            f"💰 余额：{balance} 元\n"
            f"📊 状态：{status}\n"
            f"🕐 更新：{record_time}{warn}"
        )

    # ===== 详情 =====
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
            f"🕐 更新：{result['record_time']}"
        )

    # ===== 帮助 =====
    @filter.command("帮助")
    async def help(self, event: AstrMessageEvent):
        yield event.plain_result(
            "📖 电管Doro 使用指南\n"
            "━━━━━━━━━━━━━━━━\n"
            "📝 绑定：/绑定 NS07N0488\n"
            "⚡ 查询：/查\n"
            "📊 详情：/详情\n"
            "🔓 解绑：/解绑\n"
            "━━━━━━━━━━━━━━━━\n"
            "💡 NS=南校，BS=北校"
        )

    async def terminate(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        logger.info("👋 电管Doro 插件已卸载")
