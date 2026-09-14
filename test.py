import asyncio
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain
from astrbot.api.event import MessageChain


@register("astrbot_plugin_test_proactive", "你的名字", "主动推送测试", "1.0.0", "")
class TestProactivePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    async def initialize(self):
        logger.info("✅ 主动推送测试插件已加载")

    @filter.command("测试主动")
    async def test_proactive(self, event: AstrMessageEvent):
        """触发后，往当前会话主动发一条消息"""
        umo = event.unified_msg_origin
        logger.info(f"尝试主动发送到: {umo}")

        try:
            await self.context.send_message(
                umo,
                MessageChain([Plain("✅ 主动消息测试成功")])
            )
            yield event.plain_result("已尝试主动发送，请看群里是否收到")
        except Exception as e:
            logger.error(f"主动发送失败: {e}")
            yield event.plain_result(f"❌ 主动发送失败: {e}")

    @filter.command("测试延时主动")
    async def test_delayed(self, event: AstrMessageEvent):
        """等 10 秒后再主动发，模拟定时任务场景"""
        umo = event.unified_msg_origin
        yield event.plain_result("10 秒后会尝试主动发送，请留意群里")

        async def delayed_send():
            await asyncio.sleep(10)
            try:
                await self.context.send_message(
                    umo,
                    MessageChain([Plain("✅ 延时主动消息测试成功")])
                )
                logger.info("延时主动发送成功")
            except Exception as e:
                logger.error(f"延时主动发送失败: {e}")

        asyncio.create_task(delayed_send())

    async def terminate(self):
        logger.info("👋 主动推送测试插件已卸载")