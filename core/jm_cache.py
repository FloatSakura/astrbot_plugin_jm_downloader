# region 缓存管理
"""缓存清理策略：保留 N 天 + 总大小超过上限时清理旧文件"""
import os
import time
import weakref
from pathlib import Path

from astrbot.api import logger

from .jm_paths import get_jm_cache_path

# 缓存大小上限检查间隔（秒），避免每次请求都遍历目录
_size_check_cooldown: float = 0.0
_SIZE_CHECK_INTERVAL = 300.0  # 5 分钟查一次


def _get_dir_size(path: Path) -> int:
    """递归计算目录总大小（字节）"""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _remove_old_files(path: Path, cutoff_timestamp: float) -> int:
    """删除修改时间早于 cutoff_timestamp 的文件，返回删除数"""
    removed = 0
    try:
        for entry in path.rglob("*"):
            if not entry.is_file():
                continue
            try:
                if entry.stat().st_mtime < cutoff_timestamp:
                    entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass

    # 清理空目录
    _remove_empty_dirs(path)
    return removed


def _remove_empty_dirs(path: Path):
    """递归删除空目录"""
    try:
        for entry in sorted(path.rglob("*"), reverse=True):
            if entry.is_dir():
                try:
                    entry.rmdir()
                except OSError:
                    pass
    except OSError:
        pass


def ensure_cache_limits(
    retention_days: int = 3,
    max_size_gb: float = 3.0,
) -> None:
    """检查并执行缓存清理

    Args:
        retention_days: 文件保留天数，超出的文件将被删除
        max_size_gb: 缓存总大小上限(GB)，超出时清理超过12小时的旧文件
    """
    global _size_check_cooldown
    cache_dir = get_jm_cache_path()

    # 1. 清理超过 retention_days 天的文件
    cutoff_days = time.time() - retention_days * 86400
    removed_days = _remove_old_files(cache_dir, cutoff_days)
    if removed_days > 0:
        logger.info(f"🧹 JM缓存: 清理了 {removed_days} 个超过{retention_days}天的文件")

    # 2. 检查总大小（冷却检查，避免频繁遍历）
    now = time.time()
    if max_size_gb <= 0:
        return
    if now - _size_check_cooldown < _SIZE_CHECK_INTERVAL:
        return
    _size_check_cooldown = now

    total_bytes = _get_dir_size(cache_dir)
    total_gb = total_bytes / (1024**3)
    if total_gb > max_size_gb:
        logger.info(
            f"🧹 JM缓存: 总大小 {total_gb:.2f}GB 超过上限 {max_size_gb}GB，清理超过12小时的旧文件"
        )
        cutoff_hours = now - 12 * 3600
        removed_oversize = _remove_old_files(cache_dir, cutoff_hours)
        logger.info(f"🧹 JM缓存: 清理了 {removed_oversize} 个文件")


# endregion