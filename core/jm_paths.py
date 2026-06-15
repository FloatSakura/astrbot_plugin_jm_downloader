# region 插件路径管理器
"""缓存路径管理，对齐 astrbot StarTools.get_data_dir() 模式"""
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

PLUGIN_NAME = "astrbot_plugin_jm_downloader"

# 路径缓存
_data_dir: Path | None = None


def _get_data_dir() -> Path:
    """获取插件数据目录（延迟初始化）"""
    global _data_dir
    if _data_dir is not None:
        return _data_dir

    try:
        from astrbot.api.star import StarTools
        _data_dir = StarTools.get_data_dir(PLUGIN_NAME)
    except Exception:
        # 回退到插件目录（开发/测试环境）
        _data_dir = Path(__file__).resolve().parents[1] / "data"
        _data_dir.mkdir(parents=True, exist_ok=True)

    return _data_dir


def _ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_path() -> Path:
    """获取缓存根目录"""
    return _ensure_dir(_get_data_dir() / "cache")


def get_jm_cache_path() -> Path:
    """获取 JM 图片缓存目录"""
    return _ensure_dir(get_cache_path() / "jmcomic")


def get_jm_chapter_path(album_id: str, photo_id: str) -> Path:
    """获取指定章节的缓存目录"""
    return _ensure_dir(get_jm_cache_path() / str(album_id) / str(photo_id))


def get_jm_cover_path(album_id: str) -> Path:
    """获取封面缓存目录"""
    return _ensure_dir(get_jm_cache_path() / str(album_id) / "_cover")


def get_jm_output_cache_dir() -> Path:
    """获取输出文件缓存目录（PDF/ZIP）"""
    return _ensure_dir(get_jm_cache_path() / "_output")


def _sanitize_filename(name: str) -> str:
    """清理文件名中的非法字符，保留中文/英文/数字/下划线"""
    import unicodedata
    cleaned = unicodedata.normalize("NFKC", name)
    result = []
    for ch in cleaned:
        if ch.isalnum() or ch in ("_", "-", ".", "(", ")", "[", "]", "（", "）", "【", "】"):
            result.append(ch)
        elif ch.isspace():
            result.append("_")
        else:
            result.append("-")
    sanitized = "".join(result)
    # 合并连续的下划线/横线
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    while "--" in sanitized:
        sanitized = sanitized.replace("--", "-")
    return sanitized[:80].strip("_-")


def get_cached_output_path(album_id: str, ext: str, title: str = "", chapter_range: str = "") -> Path:
    """获取缓存的输出文件路径（PDF/ZIP），ext 不含点，如 'pdf' 或 'zip'
    
    格式: {album_id}_{sanitized_title}_ch{chapter_range}.{ext}
    如: 350234_董卓_上下_ch1-2.pdf 或 350234_董卓_上下_ch1-30.zip
    """
    parts = [str(album_id)]
    if title:
        parts.append(_sanitize_filename(title))
    if chapter_range:
        parts.append(f"ch{chapter_range}")
    filename = "_".join(parts) + f".{ext}"
    return get_jm_output_cache_dir() / filename


# endregion
