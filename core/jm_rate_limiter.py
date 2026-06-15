# region 群级限速器
"""按群组 ID 限制下载频率，防止短时间内大量请求"""
import time


class RateLimiter:
    """群级下载限速器"""

    def __init__(self):
        self._last_download: dict[str, float] = {}

    def check_and_use(self, group_id: str, interval_seconds: int = 60) -> tuple[bool, float]:
        """检查是否允许下载

        Args:
            group_id: 群组 ID
            interval_seconds: 两次下载之间最少间隔秒数

        Returns:
            (是否允许, 剩余等待秒数)
        """
        if interval_seconds <= 0:
            return True, 0.0

        now = time.time()
        last = self._last_download.get(group_id, 0)
        elapsed = now - last

        if elapsed < interval_seconds:
            remaining = interval_seconds - elapsed
            return False, remaining

        self._last_download[group_id] = now
        return True, 0.0

    def release(self, group_id: str) -> None:
        """重置指定群的限速状态（下载失败时允许立即重试）"""
        self._last_download.pop(group_id, None)


# endregion