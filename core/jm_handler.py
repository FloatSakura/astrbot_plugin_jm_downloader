# region JM 下载处理器
"""JM 本子下载 + 合并转发预览 + 压缩包/PDF 发送的核心逻辑
- 固定只取前 N 张图片（跨所有章节）
- 图片源文件、PDF、ZIP 均缓存
- 下载前检查缓存，命中则跳过下载直接发送
- 合并转发: "{album_id}验车"（仅此一处带验车前缀） + 本子信息 + 前N张预览图
"""
import asyncio
import os
import re
import shutil
import time
import traceback
from pathlib import Path
from typing import AsyncGenerator

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import File, Image, Node, Nodes, Plain

from .jm_cache import ensure_cache_limits
from .jm_paths import get_jm_chapter_path, get_jm_cache_path, get_cached_output_path
from .jm_rate_limiter import RateLimiter
from .jm_tools import images_to_pdf_async, images_to_zip_async

_rate_limiter = RateLimiter()


class JmDownloadMixin:
    """JM 下载混入类，提供下载和发送能力"""

    # ---------- 配置获取 ----------
    def _get_config_value(self, key: str, default):
        keys = key.split(".")
        val = self.config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return default
            if val is None:
                return default
        return val

    def _refresh_jm_config(self) -> None:
        """刷新 JM 相关配置"""
        self.jm_proxy = str(self._get_config_value("jm_settings.proxy", "")).strip()
        self.jm_cookies = str(
            self._get_config_value("jm_settings.jm_cookies", "")
        ).strip()
        self.jm_preview_images_group = max(
            1, int(self._get_config_value("jm_settings.preview_images_group", 5))
        )
        self.jm_preview_images_private = max(
            1, int(self._get_config_value("jm_settings.preview_images_private", 100))
        )
        self.jm_cache_retention_days = max(
            1, int(self._get_config_value("jm_settings.cache_retention_days", 3))
        )
        self.jm_cache_max_size_gb = max(
            0.0, float(self._get_config_value("jm_settings.cache_max_size_gb", 3.0))
        )
        self.jm_rate_limit_seconds = max(
            0, int(self._get_config_value("jm_settings.rate_limit_seconds", 60))
        )
        self.jm_error_notify = str(
            self._get_config_value("jm_settings.error_notify_mode", "通知")
        ).strip()
        self.jm_output_mode = str(
            self._get_config_value("jm_settings.output_mode", "压缩包")
        ).strip()
        self.jm_merge_send_as_sender = bool(
            self._get_config_value("jm_settings.merge_send_as_sender", False)
        )
        self.jm_zip_password = str(
            self._get_config_value("jm_settings.zip_password", "")
        ).strip()
        self.jm_max_chapters = max(
            1, int(self._get_config_value("jm_settings.max_chapters_per_segment", 30))
        )
        self.jm_whitelist_enabled = bool(
            self._get_config_value("jm_settings.whitelist_enabled", True)
        )
        self.jm_whitelist_set = set()
        whitelist_raw = str(
            self._get_config_value("jm_settings.whitelist_groups", "")
        ).strip()
        if whitelist_raw:
            for g in whitelist_raw.split(","):
                g = g.strip()
                if g:
                    self.jm_whitelist_set.add(g)
        self.jm_admin_qq = str(
            self._get_config_value("jm_settings.admin_qq", "")
        ).strip()
        self.jm_file_merge_forward = bool(
            self._get_config_value("jm_settings.file_merge_forward_enabled", True)
        )
        self.jm_allow_private_chat = bool(
            self._get_config_value("jm_settings.allow_private_chat", True)
        )

    # ---------- 合并转发发送人 ----------
    def _get_merge_sender_uin(self, event: AstrMessageEvent) -> str:
        """根据配置决定合并转发使用的 uin"""
        if self.jm_merge_send_as_sender:
            sender_id = event.get_sender_id()
            if sender_id:
                return str(sender_id)
        return str(event.get_self_id())

    # ---------- 错误通知 ----------
    def _yield_error(self, event: AstrMessageEvent, message: str):
        """yield 错误信息"""
        if self.jm_error_notify != "通知":
            yield event.plain_result(f"❌ JM下载失败: {message[:200]}")
            return

        yield event.plain_result(f"❌ JM下载失败\n{'─' * 20}\n{message}")

    # ---------- 缓存检查 ----------
    def _check_cached_outputs(self, album_id: str, title: str, chapter_range: str = "") -> tuple[Path | None, Path | None]:
        """检查是否有缓存的 PDF 和 ZIP 输出文件。

        Args:
            chapter_range: 章节范围紧凑格式，如 "1-30"，用于区分不同范围的缓存

        Returns:
            (pdf_path, zip_path) — 不存在的为 None
        """
        pdf_path = get_cached_output_path(album_id, "pdf", title, chapter_range)
        zip_path = get_cached_output_path(album_id, "zip", title, chapter_range)

        pdf_exists = pdf_path.exists()
        zip_exists = zip_path.exists()

        return (
            pdf_path if pdf_exists else None,
            zip_path if zip_exists else None,
        )

    def _check_cached_images_exist(self, album_id: str, episode_list: list = None, range_start: int = None, range_end: int = None) -> bool:
        """检查指定章节范围是否所有图片都缓存完毕

        Args:
            episode_list: 全部章节列表，用于定位目标章节的 photo_id
            range_start: 起始章节序号（1-based）
            range_end: 结束章节序号（1-based，包含）

        Returns:
            bool: 范围内所有章节的图片目录均存在且有图片则为 True
        """
        if not episode_list or range_start is None or range_end is None:
            # 无范围时回退到检查任意图片存在
            album_dir = get_jm_cache_path() / str(album_id)
            if not album_dir.exists():
                return False
            for ext in ("*.webp", "*.jpg", "*.png"):
                if list(album_dir.rglob(ext)):
                    return True
            return False

        # 检查范围内每个章节的目录是否都存在且有图片
        for chapter_idx in range(range_start - 1, range_end):
            photo_id_str = episode_list[chapter_idx][0]
            chapter_dir = get_jm_chapter_path(album_id, photo_id_str)
            if not chapter_dir.exists():
                return False
            ext_found = False
            for ext in ("*.webp", "*.jpg", "*.png"):
                if list(chapter_dir.glob(ext)):
                    ext_found = True
                    break
            if not ext_found:
                return False

        return True

    # ---------- 合并转发预览 ----------
    def _build_merge_preview_nodes(
        self,
        event: AstrMessageEvent,
        album_id: str,
        title: str,
        author_str: str,
        tags: list[str],
        chapter_range: str,
        image_paths: list[Path],
        total_chapters: int = 0,
        total_images: int = 0,
    ) -> Nodes:
        """构建合并转发的 Nodes 对象

        Node[0]: "{album_id}验车"  ← 仅此一处带"验车"
        Node[1]: "标题 / 作者 / 章节 / 标签 / 共N张图片"
        Node[2..N+1]: 预览图片
        """
        nodes = Nodes([])
        sender_uin = self._get_merge_sender_uin(event)

        # Node[0]: "{album_id}验车"
        nodes.nodes.append(
            Node(uin=sender_uin, content=[Plain(f"{album_id}验车")])
        )

        # Node[1]: 本子信息
        tag_str = ", ".join(tags) if tags else "无"
        preview_count = len(image_paths)
        extra = ""
        if total_chapters and total_images:
            extra = f"\n共{total_chapters}章，{total_images}张图"
        info_text = (
            f"标题: {title}\n"
            f"作者: {author_str}\n"
            f"章节: {chapter_range}\n"
            f"标签: {tag_str}\n"
            f"当前发送前{preview_count}张预览{extra}"
        )
        nodes.nodes.append(
            Node(uin=sender_uin, content=[Plain(info_text)])
        )

        # Node[2..]: 预览图片（webp→jpg 转换，避免 QQ 不支持 webp）
        for img_path in image_paths:
            if not img_path.exists():
                continue
            # webp 转为 jpg 再发送
            display_path = img_path
            if img_path.suffix.lower() == ".webp":
                jpg_path = img_path.with_suffix(".preview.jpg")
                # 如果已有缓存转换文件则跳过
                if not jpg_path.exists():
                    try:
                        from PIL import Image as PILImage
                        img = PILImage.open(str(img_path))
                        if img.mode in ("RGBA", "P", "LA"):
                            img = img.convert("RGB")
                        img.save(str(jpg_path), "JPEG", quality=95)
                    except Exception:
                        jpg_path = img_path
                display_path = jpg_path
            nodes.nodes.append(
                Node(
                    uin=sender_uin,
                    content=[Image.fromFileSystem(str(display_path.resolve()))],
                )
            )

        logger.info(
            f"📋 合并转发构建完成: album_id={album_id}, "
            f"节点数={len(nodes.nodes)} (1验车+1信息+{len(image_paths)}图)"
        )
        return nodes

    # ---------- 异步生成器主入口 ----------
    async def handle_jm_async_gen(
        self,
        event: AstrMessageEvent,
        album_id: str,
        range_start: int | None,
        range_end: int | None,
        override_mode: str | None = None,
    ) -> AsyncGenerator:
        """handle_jm 的异步生成器版本，所有消息通过 yield 返回

        消息流程:
        1. "📥 开始下载「{title}」，请稍候..."
        2. 合并转发: "{album_id}验车" + 信息 + 前N张预览图
        3. "📄/📦 正在准备PDF/ZIP，请稍候..."
        4. 发送 PDF/ZIP 文件
        5. "✅ 下载完成: {title}\n成功: X章 | 失败: X章 | 耗时: X.Xs"
        """

        process_start = time.perf_counter()

        # 1. 刷新配置
        self._refresh_jm_config()

        # 2. 限速检查
        group_id = event.get_group_id() or "private"
        allowed, wait_seconds = _rate_limiter.check_and_use(
            group_id, self.jm_rate_limit_seconds
        )
        if not allowed:
            for result in self._yield_error(
                event,
                f"下载过于频繁，请等待 {wait_seconds:.0f} 秒后再试\n"
                f"本群限速间隔: {self.jm_rate_limit_seconds}秒",
            ):
                yield result
            return

        # 3. 输出模式（override_mode 优先级高于插件配置）
        if override_mode and override_mode != "jm":
            mode_map = {"jmpic": "不发送", "jmzip": "压缩包", "jmpdf": "PDF", "jmall": "两者"}
            output_mode = mode_map.get(override_mode, self.jm_output_mode)
        else:
            output_mode = self.jm_output_mode
        send_zip = output_mode in ("压缩包", "两者")
        send_pdf = output_mode in ("PDF", "两者")
        send_files = output_mode != "不发送"

        # 4. 获取本子元数据（始终获取，合并转发需要）
        try:
            from jmcomic import JmModuleConfig

            option = JmModuleConfig.option_class().default()
            client = option.build_jm_client()

            if self.jm_proxy:
                try:
                    client.set_proxy(self.jm_proxy)
                except Exception:
                    logger.warning(f"设置代理失败: {self.jm_proxy}")

            album = await asyncio.to_thread(client.get_album_detail, int(album_id))
        except Exception as exc:
            logger.error(f"获取本子信息失败: {exc}")
            _rate_limiter.release(group_id)
            for result in self._yield_error(
                event,
                f"获取本子信息失败\n"
                f"album_id: {album_id}\n"
                f"原因: {str(exc)[:200]}\n"
                f"请检查本子ID是否正确，或是否需要配置代理/Cookie",
            ):
                yield result
            return

        # 提取元数据
        title = getattr(album, "title", "未知标题") or "未知标题"
        authors = getattr(album, "authors", []) or []
        author_str = ", ".join(authors) if authors else "未知作者"
        tags = getattr(album, "tags", []) or []

        episode_list = getattr(album, "episode_list", []) or []
        total_chapters = len(episode_list)

        logger.info(
            f"📖 本子信息: {title} | 作者: {author_str} | 章节数: {total_chapters}"
        )

        if total_chapters == 0:
            _rate_limiter.release(group_id)
            for result in self._yield_error(
                event, f"本子 {album_id} 未找到任何章节"
            ):
                yield result
            return

        # 章节数校验：≤max_chapters章自动全量，>max_chapters要求指定范围且每段≤max_chapters
        limit = self.jm_max_chapters
        if range_start is None and range_end is None:
            if total_chapters > limit:
                _rate_limiter.release(group_id)
                for result in self._yield_error(
                    event,
                    f"本子共 {total_chapters} 章（超过{limit}章），不支持全量下载。\n"
                    f"请使用 .jm {album_id} 1-{limit} 分段下载，每段最多{limit}章。\n"
                    f"示例: .jm {album_id} 1-{limit}\n"
                    f"      .jm {album_id} {limit+1}-{limit*2}",
                ):
                    yield result
                return
        else:
            # 有指定范围，检查跨度是否超过限制
            span = range_end - range_start + 1
            if span > limit:
                _rate_limiter.release(group_id)
                for result in self._yield_error(
                    event,
                    f"章节跨度 {span} 章（超过{limit}章），每段最多{limit}章。\n"
                    f"请缩小范围后重试。",
                ):
                    yield result
                return

        # 章节范围
        if range_start is not None and range_end is not None:
            if range_start < 1 or range_end < 1:
                _rate_limiter.release(group_id)
                for result in self._yield_error(event, "章节范围必须是正整数"):
                    yield result
                return
            if range_start > range_end:
                _rate_limiter.release(group_id)
                for result in self._yield_error(
                    event,
                    f"起始章节({range_start})不能大于结束章节({range_end})",
                ):
                    yield result
                return
            if range_start > total_chapters:
                _rate_limiter.release(group_id)
                for result in self._yield_error(
                    event,
                    f"起始章节({range_start})超出总章节数({total_chapters})",
                ):
                    yield result
                return
            if range_end > total_chapters:
                range_end = total_chapters
        else:
            range_start = 1
            range_end = total_chapters

        chapter_range = f"第{range_start}章~第{range_end}章（共{total_chapters}章）"
        chapter_range_compact = f"{range_start}-{range_end}"

        # 5. 缓存检查（验证指定范围内的每个章节都已缓存）
        cached_pdf, cached_zip = self._check_cached_outputs(album_id, title, chapter_range_compact)
        images_cached = self._check_cached_images_exist(
            album_id, episode_list, range_start, range_end
        )

        need_zip = send_zip and cached_zip is None
        need_pdf = send_pdf and cached_pdf is None

        # 5a. 完全缓存命中：图片 + 所需输出文件都存在
        output_cache_hit = (
            (not need_zip and not need_pdf)
            or (send_zip and send_pdf and cached_pdf is not None and cached_zip is not None)
            or (send_zip and not send_pdf and cached_zip is not None)
            or (send_pdf and not send_zip and cached_pdf is not None)
        )

        if images_cached and output_cache_hit:
            logger.info(f"📦 JM缓存完全命中: album_id={album_id}, 跳过下载和生成")
            # 收集完整图片 list 用于文件发送
            all_image_paths = self._collect_all_cached_images(album_id)
            if not all_image_paths:
                _rate_limiter.release(group_id)
                for result in self._yield_error(event, f"本子 {album_id} 缓存图片为空"):
                    yield result
                return

            # 预览用图（限制数量）
            preview_paths = all_image_paths
            if len(preview_paths) > self._get_preview_limit(event):
                preview_paths = preview_paths[: self._get_preview_limit(event)]

            # 第一条消息
            yield event.plain_result(f"📥 开始下载「{title}」，请稍候...")

            # 发送合并转发预览
            nodes = self._build_merge_preview_nodes(
                event, album_id, title, author_str, tags,
                chapter_range, preview_paths,
                total_chapters=total_chapters,
                total_images=len(all_image_paths),
            )
            yield event.chain_result([nodes])
            await asyncio.sleep(1)

            # 文件生成提示（缓存路径5a，含缓存命中提示）
            zip_password_hint = self.jm_zip_password if send_zip else ""
            prompt_parts = []
            if send_pdf and send_zip:
                prompt_parts.append("💾 缓存命中，📄 正在准备PDF/ZIP，请稍候...")
            elif send_pdf:
                prompt_parts.append("💾 缓存命中，📄 正在准备PDF，请稍候...")
            else:
                prompt_parts.append("💾 缓存命中，📦 正在准备ZIP，请稍候...")
            if zip_password_hint:
                prompt_parts.append(f"🔑 ZIP密码: {zip_password_hint}")
            yield event.plain_result("\n".join(prompt_parts))

            # 缓存文件发送（含汇总）
            total_time = time.perf_counter() - process_start
            summary = (
                f"✅ 下载完成: {title}\n"
                f"成功: {len(list(range(range_start - 1, range_end)))}章 | 失败: 0章 | 耗时: {total_time:.1f}s"
            )
            is_group = bool(event.get_group_id())
            use_merge = is_group and self.jm_file_merge_forward

            if use_merge:
                merge_nodes = Nodes([])
                sender_uin = str(event.get_self_id())
                if send_pdf and cached_pdf and cached_pdf.exists():
                    merge_nodes.nodes.append(
                        Node(uin=sender_uin, content=[File(name=cached_pdf.name, file=str(cached_pdf))])
                    )
                if send_zip and cached_zip and cached_zip.exists():
                    merge_nodes.nodes.append(
                        Node(uin=sender_uin, content=[File(name=cached_zip.name, file=str(cached_zip))])
                    )
                merge_nodes.nodes.append(
                    Node(uin=sender_uin, content=[Plain(summary)])
                )
                if merge_nodes.nodes:
                    yield event.chain_result([merge_nodes])
            else:
                if send_pdf and cached_pdf and cached_pdf.exists():
                    yield event.chain_result([File(name=cached_pdf.name, file=str(cached_pdf))])
                    await asyncio.sleep(1)
                if send_zip and cached_zip and cached_zip.exists():
                    yield event.chain_result([File(name=cached_zip.name, file=str(cached_zip))])
                yield event.plain_result(summary)

            logger.info(f"📊 JM发送完成(缓存): album_id={album_id} | {title} | 耗时: {total_time:.1f}s")

            # 缓存清理
            try:
                ensure_cache_limits(
                    self.jm_cache_retention_days, self.jm_cache_max_size_gb
                )
            except Exception as exc:
                logger.warning(f"缓存清理失败: {exc}")
            return

        # 5b. 图片缓存存在但需要生成输出文件
        if images_cached and (need_zip or need_pdf):
            logger.info(
                f"📦 JM图片缓存存在，仅需生成输出文件: album_id={album_id}"
            )
            # 完整图片用于文件生成
            all_image_paths = self._collect_all_cached_images(album_id)
            if not all_image_paths:
                _rate_limiter.release(group_id)
                for result in self._yield_error(event, f"本子 {album_id} 缓存图片为空"):
                    yield result
                return

            # 预览用图（限制数量）
            preview_paths = all_image_paths
            if len(preview_paths) > self._get_preview_limit(event):
                preview_paths = preview_paths[: self._get_preview_limit(event)]

            # 第一条消息
            yield event.plain_result(f"📥 开始下载「{title}」，请稍候...")

            # 发送合并转发预览
            nodes = self._build_merge_preview_nodes(
                event, album_id, title, author_str, tags,
                chapter_range, preview_paths,
            )
            yield event.chain_result([nodes])
            await asyncio.sleep(1)

            # 生成并发送输出文件（含密码提示 + 图片缓存标记 + 汇总）
            self._cache_hint = "📦 图片缓存，"
            total_time = time.perf_counter() - process_start
            summary = (
                f"✅ 下载完成: {title}\n"
                f"成功: {len(list(range(range_start - 1, range_end)))}章 | 失败: 0章 | 耗时: {total_time:.1f}s"
            )
            logger.info(
                f"📊 JM下载完成(图片缓存): album_id={album_id} | "
                f"{title} | 图片: {len(all_image_paths)}张 | 耗时: {total_time:.1f}s"
            )
            async for result in self._generate_and_send_outputs(
                event, album_id, title, all_image_paths,
                chapter_range=chapter_range_compact,
                send_pdf=send_pdf, send_zip=send_zip,
                summary=summary,
            ):
                yield result

            # 缓存清理
            try:
                ensure_cache_limits(
                    self.jm_cache_retention_days, self.jm_cache_max_size_gb
                )
            except Exception as exc:
                logger.warning(f"缓存清理失败: {exc}")
            return

        # 5c. 正常下载流程
        logger.info(
            f"📥 JM下载请求: album_id={album_id}, range={range_start}-{range_end}"
        )

        yield event.plain_result(
            f"📥 开始下载「{title}」，第{range_start}-{range_end}章，请稍候...\n"
            f"⚠️ 合并转发预览图片可能因数量过多而发送失败，属正常现象"
        )

        # 6. 逐章完整下载所有图片
        all_image_paths: list[Path] = []
        success_count = 0
        fail_count = 0

        for chapter_idx in range(range_start - 1, range_end):
            chapter_num = chapter_idx + 1
            photo_id_str, episode_title, _ = episode_list[chapter_idx]
            chapter_label = (
                f"第{chapter_num}章 {episode_title}"
                if episode_title
                else f"第{chapter_num}章"
            )

            try:
                chapter_start = time.perf_counter()

                # 下载章节
                photo = await self._download_single_chapter(
                    client, photo_id_str, album_id
                )
                if photo is None:
                    fail_count += 1
                    logger.warning(f"⏭️ 跳过章节: {chapter_label} (下载失败)")
                    continue

                # 获取图片路径（完整获取，不截断）
                image_paths = await self._get_chapter_image_paths(
                    photo, album_id, photo_id_str
                )

                if not image_paths:
                    fail_count += 1
                    logger.warning(f"⏭️ 跳过章节: {chapter_label} (无图片)")
                    continue

                all_image_paths.extend(image_paths)
                success_count += 1

                chapter_time = time.perf_counter() - chapter_start
                logger.info(
                    f"✅ 章节下载完成: {chapter_label} | "
                    f"本张图片数: {len(image_paths)} | 累计: {len(all_image_paths)} | "
                    f"耗时: {chapter_time:.1f}s"
                )

            except asyncio.CancelledError:
                logger.info(f"♻️ JM下载任务被中断: {chapter_label}")
                raise
            except Exception as exc:
                fail_count += 1
                logger.error(
                    f"❌ 章节处理失败 [{chapter_label}]: {exc}\n{traceback.format_exc()}"
                )

        # 检查是否有图片
        if not all_image_paths:
            _rate_limiter.release(group_id)
            for result in self._yield_error(
                event, f"本子 {album_id} 未获取到任何图片"
            ):
                yield result
            return

        logger.info(
            f"🖼️ 共获取 {len(all_image_paths)} 张图片，准备发送合并转发"
        )

        # 预览用图（限制数量）
        preview_paths = all_image_paths
        if len(preview_paths) > self._get_preview_limit(event):
            preview_paths = preview_paths[: self._get_preview_limit(event)]

        # 7. 发送合并转发预览
        nodes = self._build_merge_preview_nodes(
            event, album_id, title, author_str, tags,
            chapter_range, preview_paths,
        )
        yield event.chain_result([nodes])
        await asyncio.sleep(1)

        # 8. 生成并发送输出文件（含密码提示 + 汇总，群聊合并）
        total_time = time.perf_counter() - process_start
        if send_files:
            is_group = bool(event.get_group_id())
            use_merge = is_group and self.jm_file_merge_forward
            summary = (
                f"✅ 下载完成: {title}\n"
                f"成功: {success_count}章 | 失败: {fail_count}章 | 耗时: {total_time:.1f}s"
            )
            async for result in self._generate_and_send_outputs(
                event, album_id, title, all_image_paths,
                chapter_range=chapter_range_compact,
                send_pdf=send_pdf, send_zip=send_zip,
                summary=summary,
            ):
                yield result
        else:
            yield event.plain_result(
                f"✅ 下载完成: {title}\n"
                f"成功: {success_count}章 | 失败: {fail_count}章 | 耗时: {total_time:.1f}s"
            )
        logger.info(
            f"📊 JM下载完成: album_id={album_id} | "
            f"{title} | 图片: {len(all_image_paths)}张 | "
            f"成功: {success_count}章 | 失败: {fail_count}章 | 耗时: {total_time:.1f}s"
        )

        # 11. 缓存清理
        try:
            ensure_cache_limits(
                self.jm_cache_retention_days, self.jm_cache_max_size_gb
            )
        except Exception as exc:
            logger.warning(f"缓存清理失败: {exc}")

    # ---------- 预览上限助手 ----------
    def _get_preview_limit(self, event: AstrMessageEvent) -> int:
        """根据群聊/私聊返回预览图片上限"""
        if event.get_group_id():
            return self.jm_preview_images_group
        return self.jm_preview_images_private

    # ---------- 收集缓存图片（预览用）----------
    def _collect_cached_images(self, album_id: str) -> list[Path]:
        """从缓存目录收集已下载的原始图片（最多 max_total_images 张，用于预览）"""
        all_images = self._collect_all_cached_images(album_id)
        # Note: called without event context, returns all images (capped by caller)
        return all_images

    # ---------- 收集全部缓存图片（文件生成用）----------
    def _collect_all_cached_images(self, album_id: str) -> list[Path]:
        """从缓存目录收集已下载的所有原始图片（不限制数量，用于 PDF/ZIP 生成）"""
        album_dir = get_jm_cache_path() / str(album_id)
        if not album_dir.exists():
            return []

        image_paths: list[Path] = []
        for ext in ("*.webp", "*.jpg", "*.png"):
            for f in sorted(album_dir.rglob(ext)):
                if ".preview." not in f.name:
                    image_paths.append(f)

        logger.info(
            f"📦 从缓存收集到 {len(image_paths)} 张图片 (album_id={album_id})"
        )
        return image_paths

    # ---------- 发送缓存输出文件 ----------
    async def _send_cached_outputs(
        self,
        event: AstrMessageEvent,
        album_id: str,
        cached_pdf: Path | None,
        cached_zip: Path | None,
        send_pdf: bool = True,
        send_zip: bool = True,
    ) -> AsyncGenerator:
        """发送缓存的输出文件"""
        if send_pdf and cached_pdf is not None and cached_pdf.exists():
            logger.info(f"📤 发送缓存PDF: {cached_pdf.name}")
            yield event.chain_result(
                [File(name=cached_pdf.name, file=str(cached_pdf))]
            )
            await asyncio.sleep(1)

        if send_zip and cached_zip is not None and cached_zip.exists():
            logger.info(f"📤 发送缓存ZIP: {cached_zip.name}")
            yield event.chain_result(
                [File(name=cached_zip.name, file=str(cached_zip))]
            )

    # ---------- 生成并发送输出文件 ----------
    async def _generate_and_send_outputs(
        self,
        event: AstrMessageEvent,
        album_id: str,
        title: str,
        image_paths: list[Path],
        chapter_range: str = "",
        send_pdf: bool = True,
        send_zip: bool = True,
        summary: str = "",
    ) -> AsyncGenerator:
        """根据图片列表生成 PDF/ZIP 并发送，生成的文件缓存到 _output 目录

        Args:
            chapter_range: 章节范围紧凑格式如 "1-30"，用于文件名区分不同范围缓存
        """
        if not image_paths:
            yield event.plain_result("⚠️ 没有图片可供生成文件")
            return

        # 文件生成提示（含密码，路径5b传入图片缓存提示）
        zip_password = self.jm_zip_password if send_zip else ""
        prompt_parts = []
        prefix = getattr(self, '_cache_hint', '')
        if send_pdf and send_zip:
            prompt_parts.append(f"{prefix}📄 正在准备PDF/ZIP，请稍候...")
        elif send_pdf:
            prompt_parts.append(f"{prefix}📄 正在准备PDF，请稍候...")
        else:
            prompt_parts.append(f"{prefix}📦 正在准备ZIP，请稍候...")

        if zip_password:
            prompt_parts.append(f"🔑 ZIP密码: {zip_password}")

        yield event.plain_result("\n".join(prompt_parts))

        # PDF — 仅生成，不单独发送
        pdf_ready = None
        if send_pdf:
            pdf_path = get_cached_output_path(album_id, "pdf", title, chapter_range)
            if pdf_path.exists():
                logger.info(f"📄 PDF缓存已存在: {pdf_path}")
                pdf_ready = pdf_path
            else:
                success = await images_to_pdf_async(image_paths, str(pdf_path))
                if success and pdf_path.exists():
                    logger.info(f"📄 PDF生成完成: {pdf_path.name}")
                    pdf_ready = pdf_path
                else:
                    logger.error(f"PDF 生成失败: album_id={album_id}")
                    yield event.plain_result("❌ PDF 生成失败")

        # ZIP — 仅生成，不单独发送
        zip_ready = None
        if send_zip:
            zip_path = get_cached_output_path(album_id, "zip", title, chapter_range)
            if zip_path.exists():
                logger.info(f"📦 ZIP缓存已存在: {zip_path}")
                zip_ready = zip_path
            else:
                success = await images_to_zip_async(image_paths, str(zip_path), password=zip_password)
                if success and zip_path.exists():
                    logger.info(f"📦 ZIP生成完成: {zip_path.name}")
                    zip_ready = zip_path
                else:
                    logger.error(f"ZIP 生成失败: album_id={album_id}")
                    yield event.plain_result("❌ ZIP 生成失败")

        # 发送文件：私聊单独发送，群聊合并一条转发（含摘要）
        is_group = bool(event.get_group_id())
        use_merge = is_group and self.jm_file_merge_forward

        if use_merge:
            merge_nodes = Nodes([])
            sender_uin = str(event.get_self_id())
            if pdf_ready:
                merge_nodes.nodes.append(
                    Node(uin=sender_uin, content=[File(name=pdf_ready.name, file=str(pdf_ready))])
                )
            if zip_ready:
                merge_nodes.nodes.append(
                    Node(uin=sender_uin, content=[File(name=zip_ready.name, file=str(zip_ready))])
                )
            if summary:
                merge_nodes.nodes.append(
                    Node(uin=sender_uin, content=[Plain(summary)])
                )
            if merge_nodes.nodes:
                yield event.chain_result([merge_nodes])
            elif summary:
                yield event.plain_result(summary)
        else:
            if pdf_ready:
                yield event.chain_result([File(name=pdf_ready.name, file=str(pdf_ready))])
                await asyncio.sleep(1)
            if zip_ready:
                yield event.chain_result([File(name=zip_ready.name, file=str(zip_ready))])
            if summary:
                yield event.plain_result(summary)

        # 不清理原始图片 — 保留所有缓存

    # ---------- 单章下载 ----------
    async def _download_single_chapter(
        self, client, photo_id_str: str, album_id: str
    ):
        """下载单个章节，返回 photo 对象"""
        from jmcomic import JmModuleConfig

        cache_chapter_dir = get_jm_chapter_path(album_id, photo_id_str)
        download_base = str(cache_chapter_dir.parent)

        logger.info(f"📂 下载目录: {download_base}")

        option = JmModuleConfig.option_class().construct({
            "dir_rule": {
                "rule": "Bd_Pid",
                "base_dir": download_base,
            },
            "download": {
                "image": {
                    "suffix": ".webp",
                },
            },
        })

        if self.jm_proxy:
            try:
                option.client.proxy = self.jm_proxy
            except Exception:
                pass

        dler = JmModuleConfig.downloader_class()(option)

        try:
            photo = await asyncio.to_thread(dler.download_photo, int(photo_id_str))
            if getattr(dler, "has_exception", False):
                exceptions = getattr(dler, "exceptions", [])
                logger.warning(
                    f"章节 {photo_id_str} 下载有异常: {len(exceptions)} 个"
                )
            logger.info(
                f"✅ 章节下载完成: {photo_id_str} -> {download_base}"
            )
            return photo
        except Exception as exc:
            logger.error(f"章节 {photo_id_str} 下载失败: {exc}")
            return None

    # ---------- 清空所有缓存 ----------
    async def _handle_clear_cache(self, event: AstrMessageEvent) -> AsyncGenerator:
        """清理整个 jmcomic 缓存目录（包括图片、PDF、ZIP）"""
        cache_dir = get_jm_cache_path()
        if not cache_dir.exists():
            yield event.plain_result("📭 缓存目录不存在，无需清理")
            return

        try:
            # 统计大小
            total_bytes = 0
            file_count = 0
            for f in cache_dir.rglob("*"):
                if f.is_file():
                    try:
                        total_bytes += f.stat().st_size
                        file_count += 1
                    except OSError:
                        pass

            # 删除缓存目录
            shutil.rmtree(str(cache_dir))
            cache_dir.mkdir(parents=True, exist_ok=True)

            size_mb = total_bytes / (1024 * 1024)
            yield event.plain_result(
                f"🧹 JM缓存已清理\n"
                f"删除文件: {file_count} 个\n"
                f"释放空间: {size_mb:.1f} MB"
            )
            logger.info(f"🧹 JM缓存清理完成: 删除 {file_count} 个文件, {size_mb:.1f} MB")
        except Exception as exc:
            logger.error(f"❌ 缓存清理失败: {exc}")
            yield event.plain_result(f"❌ 缓存清理失败: {str(exc)[:200]}")

    # ---------- 获取章节图片路径 ----------
    async def _get_chapter_image_paths(
        self, photo, album_id: str, photo_id_str: str
    ) -> list[Path]:
        """从下载的 photo 对象中获取本地图片路径列表（仅在该 album 目录下搜索）"""
        image_paths = []

        # 仅在当前 album 专属目录下搜索
        cache_chapter_dir = get_jm_chapter_path(album_id, photo_id_str)
        search_dir = cache_chapter_dir.parent  # {cache_root}/jmcomic/{album_id}/
        logger.debug(
            f"🔍 搜索图片: search_dir={search_dir}, exists={search_dir.exists()}"
        )

        if search_dir.exists():
            for ext in ("*.webp", "*.jpg", "*.png"):
                for f in sorted(search_dir.rglob(ext)):
                    image_paths.append(f)
                if image_paths:
                    break

        if image_paths:
            logger.info(
                f"✅ 找到 {len(image_paths)} 张图片: "
                f"{image_paths[0].parent} ..."
            )
        else:
            logger.warning(
                f"⚠️ 未在 album {album_id} 目录下找到任何图片: {search_dir}"
            )

        return image_paths


# endregion