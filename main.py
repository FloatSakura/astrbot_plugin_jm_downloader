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

from .core.jm_group_files import (
    clean_bot_files,
    compute_used_space,
    format_size,
    format_space,
    get_member_role,
    get_onebot_client,
)
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
    "1.2.5",
)
class JmDownloader(JmDownloadMixin, Star):
    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None):
        super().__init__(context)
        self.context = context
        self.config = config or context.get_config()
        self._clean_task = None
        # 先把旧版扁平配置迁移到分组配置，再读取（迁移失败也会走 _cfg 的旧键回退）
        self._migrate_legacy_config()
        self._refresh_jm_config()
        # 定时清理任务必须在事件循环内创建；构造阶段若无循环，则延后到首次指令
        self.ensure_clean_task()
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
        # 兜底：构造阶段未成功创建时，在此补建定时清理任务
        self.ensure_clean_task()
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

    @filter.command("del-files")
    async def on_del_files(self, event: AstrMessageEvent):
        """/del-files — 清理本群中 Bot 上传的过期群文件"""
        self.ensure_clean_task()
        async for r in self._handle_del_files(event):
            yield r

    @filter.command("jmspace")
    async def on_jmspace(self, event: AstrMessageEvent):
        """/jmspace — 查看本群群文件空间使用情况"""
        self.ensure_clean_task()
        async for r in self._handle_jmspace(event):
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
                "• /jmspace — 查看本群群文件空间\n"
                "• /del-files — 清理 Bot 上传的过期群文件\n"
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

    # region 群文件管理指令
    async def _require_group_file_ready(self, event: AstrMessageEvent):
        """群文件管理指令的公共前置检查。

        Returns:
            (client, group_id, error_message) — 出错时 client/group_id 为 None
        """
        self._refresh_jm_config()

        group_id = event.get_group_id()
        if not group_id:
            return None, None, "❗ 该指令仅支持在群聊中使用"

        if not self.jm_group_file_ready:
            return None, None, (
                "⛔ 未配置「机器人QQ号」(bot_qq)，群文件管理功能已禁用\n"
                "请在插件配置中填写 Bot 自身登录的 QQ 号后重试"
            )

        client = await get_onebot_client(event, self.context)
        if client is None:
            return None, None, "⛔ 无法获取 QQ 客户端，请稍后重试"

        return client, group_id, ""

    async def _check_del_files_permission(self, event, client, group_id):
        """按 del_files_audience（可多选）判定 /del-files 权限。

        勾选多项时满足任意一项即可。群角色通过协议端查询；
        查询失败一律拒绝，不静默放行。
        """
        audience = set(self.jm_del_files_audience or [])
        if not audience:
            return False, "⛔ 未配置 /del-files 可用人群，指令已禁用"

        sender_id = str(event.get_sender_id() or "").strip()
        if not sender_id:
            return False, "⛔ 无法识别你的账号，指令已拒绝"

        # 「Bot管理员」不依赖群角色，先判定，避免多余的接口调用
        if "Bot管理员" in audience:
            admin_qq = str(self.jm_admin_qq or "").strip()
            if admin_qq and sender_id == admin_qq:
                return True, ""

        if not (audience & {"群主", "群管理员", "群员"}):
            return False, "⛔ 该指令仅限 Bot 管理员使用"

        role = await get_member_role(client, group_id, sender_id)
        if not role:
            return False, "⛔ 无法确认你的群身份，指令已拒绝"

        if role == "owner":
            if audience & {"群主", "群管理员"}:
                return True, ""
        elif role == "admin":
            if "群管理员" in audience:
                return True, ""
        elif "群员" in audience:
            return True, ""

        allowed_text = "、".join(self.jm_del_files_audience)
        return False, f"⛔ 你不在该指令的允许人群内\n当前允许: {allowed_text}"

    async def _handle_del_files(self, event: AstrMessageEvent):
        """清理本群中 Bot 上传的过期群文件"""
        self._refresh_jm_config()

        if not self.jm_del_files_enabled:
            yield event.plain_result("⛔ /del-files 指令已被管理员关闭")
            return

        client, group_id, error = await self._require_group_file_ready(event)
        if error:
            yield event.plain_result(error)
            return

        allowed, error = await self._check_del_files_permission(event, client, group_id)
        if not allowed:
            yield event.plain_result(error)
            return

        if self.jm_auto_clean_days <= 0:
            yield event.plain_result(
                "⛔ 当前群文件保留天数为 0（不删除）\n"
                "请在插件配置中设置「群文件保留天数」后再试"
            )
            return

        yield event.plain_result(
            f"🧹 正在清理本群中 {self.jm_auto_clean_days} 天前 Bot 上传的文件，请稍候..."
        )
        deleted, failed, freed = await clean_bot_files(
            client, group_id, self.jm_auto_clean_days, self.jm_bot_qq
        )
        yield event.plain_result(
            f"🧹 群文件清理完成\n"
            f"{'─' * 20}\n"
            f"删除: {deleted} 个 | 失败: {failed} 个\n"
            f"释放空间: {format_size(freed)}"
        )

    async def _handle_jmspace(self, event: AstrMessageEvent):
        """查看本群群文件空间使用情况"""
        self._refresh_jm_config()

        if not self.jm_jmspace_enabled:
            yield event.plain_result("⛔ /jmspace 指令已被管理员关闭")
            return

        client, group_id, error = await self._require_group_file_ready(event)
        if error:
            yield event.plain_result(error)
            return

        yield event.plain_result("📊 正在统计群文件空间，请稍候...")

        # 协议端 used_space/total_space 为硬编码值，已用空间需自行按文件大小统计
        stats = await compute_used_space(client, group_id)
        if stats is None:
            yield event.plain_result("❌ 获取群文件列表失败，无法统计空间")
            return

        yield event.plain_result(
            format_space(
                stats,
                self.jm_group_file_quota_gb,
                self.jm_space_include_temp_files,
            )
        )

    # endregion

    # region 生命周期
    async def terminate(self):
        """插件卸载/重载时停止后台定时清理任务"""
        await self.stop_clean_task()

    # endregion


# endregion