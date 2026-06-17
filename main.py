# region 导入
import re
import asyncio
import time
import traceback

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp
from astrbot.api.message_components import Node, Nodes, Plain

import shutil

from .core.jm_handler import JmDownloadMixin, _rate_limiter
from .core.jm_paths import get_jm_cache_path
# endregion

# 活跃任务追踪（防止同一用户重复请求）
_active_tasks: dict[str, bool] = {}

# 命令模式映射
_COMMAND_MODES: dict[str, str] = {
    "jm": None,       # 使用插件设置
    "jmpic": "不发送",
    "jmzip": "压缩包",
    "jmpdf": "PDF",
    "jmall": "两者",
}

@register(
    "astrbot_plugin_jm_downloader",
    "FloatSakura",
    "禁漫天堂本子下载，.jm 指令自动下载并以压缩包/PDF发送",
    "1.2.2",
)
class JmDownloader(JmDownloadMixin, Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or context.get_config()
        self._refresh_jm_config()
        logger.info("📖 JM下载插件已加载")

    # region 事件处理器

    async def _parse_and_execute(
        self, event: AstrMessageEvent, override_mode: str | None
    ):
        """通用指令解析+执行"""
        user_id = str(event.get_sender_id())

        if _active_tasks.get(user_id, False):
            yield event.plain_result("⏳ 上一个请求还在处理中，请稍候...")
            return

        _active_tasks[user_id] = True

        # 白名单检查
        self._refresh_jm_config()
        if self.jm_whitelist_enabled:
            gid = event.get_group_id()
            if gid and gid not in self.jm_whitelist_set:
                _active_tasks.pop(user_id, None)
                sender_uin = str(event.get_self_id())
                nodes = Nodes([])
                nodes.nodes.append(
                    Node(
                        uin=sender_uin,
                        content=[
                            Plain(f"⛔ 本群未在白名单中\n🐧 {('有需求请联系管理员：' + self.jm_admin_qq) if self.jm_admin_qq else '请联系本群管理员'}")
                        ],
                    )
                )
                yield event.chain_result([nodes])
                return

        # 私聊开关检查
        if not event.get_group_id() and not self.jm_allow_private_chat:
            _active_tasks.pop(user_id, None)
            yield event.plain_result("⛔ 私聊下载已被管理员关闭")
            return

        try:
            msg = event.message_str or ""
            logger.info(f"🔍 JM插件收到消息: {msg[:100]}")

            body = msg.strip()
            for prefix in (".jm", "/jm", ".JM", "/JM", ".Jm", "/Jm", ".jM", "/jM",
                           ".jmpic", "/jmpic", ".jmzip", "/jmzip",
                           ".jmpdf", "/jmpdf", ".jmall", "/jmall"):
                if body.lower().startswith(prefix.lower()):
                    body = body[len(prefix):].strip()
                    break

            parts = body.split()
            skip_words = ("jm", "jmpic", "jmzip", "jmpdf", "jmall", "jmdownload")
            if parts and parts[0].lower() in skip_words:
                parts = parts[1:]

            if not parts:
                yield event.plain_result(
                    "❗ 格式: .jm <本子ID> [起始章-结束章]\n"
                    "示例: .jm 350234\n"
                    "      .jm 350234 1-30\n"
                    "      .jm del-cache   # 清理所有缓存"
                )
                return

            if parts[0].lower() in ("del-cache", "delcache", "clearcache", "clear"):
                async for result in self._handle_clear_cache(event):
                    yield result
                return

            if not parts[0].isdigit():
                yield event.plain_result(
                    "❗ 格式: .jm <本子ID> [起始章-结束章]\n"
                    "示例: .jm 350234\n"
                    "      .jm 350234 1-30\n"
                    "      .jm del-cache   # 清理所有缓存"
                )
                return

            album_id = parts[0]
            range_start = None
            range_end = None
            if len(parts) >= 2:
                range_match = re.match(r"(\d{1,3})-(\d{1,3})", parts[1])
                if range_match:
                    range_start = int(range_match.group(1))
                    range_end = int(range_match.group(2))
                elif parts[1].isdigit():
                    yield event.plain_result(
                        "❗ 章节范围格式: 起始-结束 (如 1-30)\n"
                        "示例: .jm 350234 1-30"
                    )
                    return

            logger.info(f"📥 JM指令: album_id={album_id}, range={range_start}-{range_end}, override_mode={override_mode}")

            async for result in self.handle_jm_async_gen(
                event, album_id, range_start, range_end, override_mode=override_mode
            ):
                yield result

        except Exception as exc:
            logger.error(f"❌ JM插件异常: {exc}\n{traceback.format_exc()}")
            yield event.plain_result(f"❌ JM插件出错: {str(exc)[:200]}")
        finally:
            _active_tasks.pop(user_id, None)

    @filter.command("jm")
    async def on_jm_command(self, event: AstrMessageEvent):
        """.jm <本子ID> [起始章-结束章] — 下载本子，输出格式遵循插件默认设置"""
        async for r in self._parse_and_execute(event, override_mode="jm"):
            yield r

    @filter.command("jmpic")
    async def on_jmpic_command(self, event: AstrMessageEvent):
        """.jmpic <本子ID> [起始章-结束章] — 仅发送预览图，不生成PDF/ZIP文件"""
        async for r in self._parse_and_execute(event, override_mode="jmpic"):
            yield r

    @filter.command("jmzip")
    async def on_jmzip_command(self, event: AstrMessageEvent):
        """.jmzip <本子ID> [起始章-结束章] — 下载并发送ZIP压缩包"""
        async for r in self._parse_and_execute(event, override_mode="jmzip"):
            yield r

    @filter.command("jmpdf")
    async def on_jmpdf_command(self, event: AstrMessageEvent):
        """.jmpdf <本子ID> [起始章-结束章] — 下载并发送PDF文件"""
        async for r in self._parse_and_execute(event, override_mode="jmpdf"):
            yield r

    @filter.command("jmall")
    async def on_jmall_command(self, event: AstrMessageEvent):
        """.jmall <本子ID> [起始章-结束章] — 下载并同时发送ZIP+PDF"""
        async for r in self._parse_and_execute(event, override_mode="jmall"):
            yield r

    @filter.command("jmhelp")
    async def on_jm_help(self, event: AstrMessageEvent):
        """.jmhelp — 以合并转发形式显示插件使用帮助"""
        sender_uin = str(event.get_self_id())
        nodes = Nodes([])

        help_sections = [
            (
                "📥 下载本子",
                "主要使用 / 前缀（兼容 . 前缀）\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• /jm <本子ID> [起始章-结束章]\n"
                "• 示例: /jm 350234\n"
                "  （总章节 ≤30 时自动下载全部）\n"
                "• 示例: /jm 350234 1-30\n"
                "  （指定范围下载，每段最多30章）\n"
                "• 总章节 >30 时，不支持全量下载，必须分段\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🔑 ZIP 默认使用密码加密，密码随提示消息一同发送"
            ),
            (
                "📤 输出格式",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• 压缩包 (ZIP)\n"
                "  - 图片自动转为 jpg 格式\n"
                "  - 支持加密（可配置密码或留空取消）\n"
                "• PDF\n"
                "  - 图片经压缩处理，体积优化\n"
                "• 两者 — 同时发送 ZIP + PDF\n"
                "• 不发送 — 仅发送预览图\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "可在 WebUI 配置中切换"
            ),
            (
                "🖼️ 合并转发预览",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• 每次下载先发送合并转发消息\n"
                "• 包含本子信息 + 前 N 张预览图\n"
                "• N 的默认值为 20，可在配置中修改\n"
                "• 预览图数量不影响 PDF/ZIP 完整内容"
            ),
            (
                "💾 缓存机制",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• 下载的源文件、PDF、ZIP 均缓存\n"
                "• 同一章节范围重复请求直接发送缓存\n"
                "• 不同章节范围缓存独立，不会串内容\n"
                "• 自动清理：保留天数 / 总大小上限\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "/jm del-cache 可手动清空缓存"
            ),
            (
                "🔀 快捷模式指令",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• /jmpic — 仅发送预览图（不发送文件）\n"
                "• /jmzip — 仅发送 ZIP 压缩包\n"
                "• /jmpdf — 仅发送 PDF 文件\n"
                "• /jmall — 同时发送 ZIP + PDF\n"
                "  优先级高于插件默认设置"
            ),
            (
                "⚙️ 可配置项",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• 发送模式: 不发送 / 压缩包 / PDF / 两者\n"
                "• ZIP 加密密码（留空则不加密）\n"
                "• 预览图片数量（群聊/私聊 分开设置）\n"
                "• 缓存保留天数（默认3天）\n"
                "• 缓存大小上限（默认3GB）\n"
                "• 群组限速间隔（默认60秒）\n"
                "• 群聊文件合并转发开关\n"
                "• HTTP 代理 / JM Cookies\n"
                "• 错误通知开关"
            ),
            (
                "🧪 其他指令",
                "━━━━━━━━━━━━━━━━━━━━\n"
                "• /jmhelp — 显示本帮助\n"
                "• /jm del-cache — 清空缓存\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "🐧 {}".format(
                    ("有需求请联系管理员：" + getattr(self, 'jm_admin_qq', '')) if getattr(self, 'jm_admin_qq', '') else "请联系本群管理员"
                )
            ),
        ]

        for title, content in help_sections:
            nodes.nodes.append(
                Node(
                    uin=sender_uin,
                    content=[Plain(f"【{title}】\n{content}")]
                )
            )

        yield event.chain_result([nodes])

    # endregion


# endregion