# region 群文件管理
"""OneBot 群文件能力封装：空间查询、文件列举、Bot 自有文件清理

设计要点：
- 所有 call_action 统一超时与结果校验，失败返回 (False, None, err)，不向上抛异常；
- 只删除 uploader == bot_qq 的文件，绝不按文件名猜测，避免误删群成员文件；
- 拿不到 uploader 或上传时间的条目一律跳过，宁可漏删不可误删。
"""
import asyncio
import time

from astrbot.api import logger

# OneBot API 调用统一超时（秒），防止协议端无响应导致协程永久挂起
ONEBOT_CALL_TIMEOUT = 20.0

_BYTES_PER_GB = 1024 ** 3
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


def normalize_action_client(candidate):
    """把候选对象归一化为「可直接 call_action 的对象」，失败返回 None。

    AstrBot 文档中 aiocqhttp 的调用形态是 client.api.call_action()，
    部分版本/适配器则直接暴露 client.call_action()，这里统一处理。
    """
    if not candidate:
        return None
    if hasattr(candidate, "call_action"):
        return candidate
    api = getattr(candidate, "api", None)
    if api is not None and hasattr(api, "call_action"):
        return api
    return None


async def get_onebot_client(event=None, context=None):
    """获取可调用 OneBot 的客户端。

    三级回退：event.bot → context.platform_manager 中的平台实例。
    定时任务没有 event，只能走 context 这条路。
    """
    client = normalize_action_client(getattr(event, "bot", None)) if event else None
    if client:
        return client

    if context is None:
        return None

    try:
        pm = getattr(context, "platform_manager", None)
        if pm is None:
            return None
        if hasattr(pm, "get_insts"):
            platforms = pm.get_insts() or []
        else:
            platforms = list(getattr(pm, "_platforms", {}).values())
        for platform in platforms:
            if hasattr(platform, "get_client"):
                candidate = platform.get_client()
                if asyncio.iscoroutine(candidate):
                    candidate = await candidate
                client = normalize_action_client(candidate)
                if client:
                    return client
            for attr in ("client", "bot", "api"):
                client = normalize_action_client(getattr(platform, attr, None))
                if client:
                    return client
    except Exception as exc:
        logger.debug(f"[JM群文件] 从 platform_manager 获取客户端失败: {exc}")
    return None


async def call_action_safe(client, action: str, **kwargs):
    """带超时的 OneBot 调用，返回 (ok, data, error)。"""
    try:
        result = await asyncio.wait_for(
            client.call_action(action, **kwargs), timeout=ONEBOT_CALL_TIMEOUT
        )
    except asyncio.TimeoutError:
        return False, None, f"{action} 超时（协议端 {ONEBOT_CALL_TIMEOUT:.0f}s 无响应）"
    except Exception as exc:
        return False, None, str(exc)

    if not isinstance(result, dict):
        return True, result, ""

    status = str(result.get("status", "")).lower()
    retcode = result.get("retcode", 0)
    if status == "failed" or (isinstance(retcode, int) and retcode != 0):
        message = result.get("message") or result.get("wording") or "调用失败"
        return False, None, str(message)
    return True, result.get("data", result), ""


async def _list_group_entries(client, group_id, count: int = 100, folder_id=None):
    """获取群文件条目，返回 (files, folders)；失败返回 None。

    folder_id 为空时取根目录，否则取指定文件夹。
    """
    if folder_id:
        action = "get_group_files_by_folder"
        ok, data, err = await call_action_safe(
            client,
            action,
            group_id=str(group_id),
            folder_id=str(folder_id),
            file_count=count,
        )
    else:
        action = "get_group_root_files"
        ok, data, err = await call_action_safe(
            client, action, group_id=str(group_id), file_count=count
        )

    if not ok:
        logger.warning(f"[JM群文件] {action} 获取群 {group_id} 文件列表失败: {err}")
        return None

    if isinstance(data, dict):
        files = data.get("files") or []
        folders = data.get("folders") or []
    elif isinstance(data, list):
        files, folders = data, []
    else:
        files, folders = [], []

    return (
        [item for item in files if isinstance(item, dict)],
        [item for item in folders if isinstance(item, dict)],
    )


async def list_group_files(client, group_id, count: int = 100):
    """列出群文件根目录下的文件。失败返回 None（区别于「空列表」）。"""
    entries = await _list_group_entries(client, group_id, count)
    return None if entries is None else entries[0]


def _is_temp_file(file_info: dict) -> bool:
    """判断是否为临时文件（聊天中发送、会过期的那类）。

    依据是 dead_time：QQ 的永久群文件该字段为 0，临时文件带过期时间戳。
    字段缺失或非法时按「永久」处理——只有拿到明确的过期时间才算临时，
    避免把永久文件误判为临时而少算占用。
    """
    try:
        dead = int(file_info.get("dead_time"))
    except (TypeError, ValueError):
        return False
    return dead > 0


async def compute_used_space(client, group_id, count: int = 1000):
    """按文件大小累加统计群文件占用（根目录 + 一级子文件夹）。

    协议端不返回真实容量（NapCat 硬编码 used_space=0 / total_space=10GB），
    且 get_group_files_by_folder 不返回嵌套 folders，故更深的嵌套无法统计。

    永久文件与临时文件分别累计，便于调用方按需排除临时文件。

    Returns:
        统计字典；取不到列表时返回 None
    """
    entries = await _list_group_entries(client, group_id, count)
    if entries is None:
        return None

    files, folders = entries
    seen: set = set()
    stats = {
        "total_bytes": 0,
        "total_count": 0,
        "perm_bytes": 0,
        "perm_count": 0,
        "temp_bytes": 0,
        "temp_count": 0,
    }

    def _accumulate(items):
        for info in items:
            file_id = info.get("file_id")
            key = file_id if file_id else id(info)
            if key in seen:
                continue
            seen.add(key)

            size = _file_size(info)
            stats["total_bytes"] += size
            stats["total_count"] += 1
            if _is_temp_file(info):
                stats["temp_bytes"] += size
                stats["temp_count"] += 1
            else:
                stats["perm_bytes"] += size
                stats["perm_count"] += 1

    _accumulate(files)

    # 一级子文件夹；更深的嵌套无法通过接口发现
    for folder in folders:
        folder_id = folder.get("folder_id") or folder.get("folder")
        if not folder_id:
            continue
        sub_entries = await _list_group_entries(
            client, group_id, count, folder_id=folder_id
        )
        if sub_entries is None:
            continue
        _accumulate(sub_entries[0])

    return stats


def select_used(stats: dict, include_temp: bool = False):
    """按配置选出计入「已用」的 (字节数, 文件数)。"""
    if include_temp:
        return stats["total_bytes"], stats["total_count"]
    return stats["perm_bytes"], stats["perm_count"]


async def delete_group_file(client, group_id, file_id) -> bool:
    """按 file_id 删除群文件。"""
    ok, _, err = await call_action_safe(
        client, "delete_group_file", group_id=str(group_id), file_id=str(file_id)
    )
    if not ok:
        logger.warning(f"[JM群文件] 删除群文件失败 file_id={file_id}: {err}")
        return False
    return True


async def list_joined_groups(client):
    """获取 Bot 已加入的群列表，失败返回空列表。"""
    ok, data, err = await call_action_safe(client, "get_group_list")
    if not ok:
        logger.warning(f"[JM群文件] 获取群列表失败: {err}")
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        groups = data.get("groups") or []
        return [item for item in groups if isinstance(item, dict)]
    return []


async def get_member_role(client, group_id, user_id) -> str:
    """查询成员在群里的角色（owner/admin/member），失败返回空串。

    注意：调用失败必须由调用方按「拒绝」处理，不能当作普通成员放行。
    """
    ok, data, err = await call_action_safe(
        client,
        "get_group_member_info",
        group_id=str(group_id),
        user_id=str(user_id),
        no_cache=False,
    )
    if not ok or not isinstance(data, dict):
        logger.warning(f"[JM群文件] 获取群成员角色失败({group_id}/{user_id}): {err}")
        return ""
    return str(data.get("role", "") or "")


def _file_upload_time(file_info: dict) -> int:
    """取文件上传时间戳；取不到返回 0（调用方据此跳过删除）。"""
    for key in ("upload_time", "modify_time", "create_time"):
        try:
            timestamp = int(file_info.get(key))
        except (TypeError, ValueError):
            continue
        if timestamp > 0:
            return timestamp
    return 0


def _file_size(file_info: dict) -> int:
    for key in ("size", "file_size"):
        try:
            size = int(file_info.get(key))
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return 0


async def clean_bot_files(client, group_id, days: int, bot_qq: str):
    """删除群内由 bot_qq 上传且超过 days 天的文件。

    Args:
        days: 保留天数；<= 0 表示不删除
        bot_qq: 机器人自身 QQ；为空则不删除任何文件

    Returns:
        (deleted, failed, freed_bytes)
    """
    if days <= 0 or not bot_qq:
        return 0, 0, 0

    files = await list_group_files(client, group_id)
    if files is None:
        return 0, 0, 0
    if not files:
        return 0, 0, 0

    cutoff = time.time() - days * 86400
    bot_qq_str = str(bot_qq).strip()
    deleted = failed = freed = 0
    skipped_unknown = 0

    for file_info in files:
        uploader = file_info.get("uploader")
        uploader_str = "" if uploader is None else str(uploader).strip()
        if not uploader_str:
            # 拿不到上传者就绝不删，避免误删群成员文件
            skipped_unknown += 1
            continue
        if uploader_str != bot_qq_str:
            continue

        upload_time = _file_upload_time(file_info)
        if upload_time <= 0:
            # 拿不到时间就无法判断是否过期，保守跳过
            skipped_unknown += 1
            continue
        if upload_time > cutoff:
            continue

        file_id = file_info.get("file_id")
        if not file_id:
            failed += 1
            continue

        if await delete_group_file(client, group_id, file_id):
            deleted += 1
            freed += _file_size(file_info)
            await asyncio.sleep(0.3)
        else:
            failed += 1

    if skipped_unknown:
        logger.warning(
            f"[JM群文件] 群 {group_id} 有 {skipped_unknown} 个文件缺少上传者/上传时间，"
            f"已跳过不删除"
        )
    return deleted, failed, freed


def format_size(num_bytes) -> str:
    """把字节数格式化为人类可读字符串。"""
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "未知"
    if value < 0:
        return "未知"

    index = 0
    while value >= 1024 and index < len(_SIZE_UNITS) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)}B"
    return f"{value:.2f}{_SIZE_UNITS[index]}"


def remaining_gb(used_bytes, quota_gb):
    """由「已用字节 + 配置容量」算出剩余 GB；参数非法时返回 None。"""
    try:
        return float(quota_gb) - float(used_bytes) / _BYTES_PER_GB
    except (TypeError, ValueError):
        return None


def format_space(stats: dict, quota_gb=0.0, include_temp: bool = False) -> str:
    """把群文件空间情况格式化为可直接发送的文本。

    Args:
        stats: compute_used_space 的返回值
        quota_gb: 配置的群容量(GB)；<=0 表示未配置，只显示已用
        include_temp: 是否把临时文件计入「已用」
    """
    used_bytes, used_count = select_used(stats, include_temp)

    lines = [
        "📊 群文件空间",
        "━━━━━━━━━━━━━━━━━━━━",
        f"已用: {format_size(used_bytes)}（{used_count} 个）",
        f"├ 永久: {format_size(stats['perm_bytes'])}（{stats['perm_count']} 个）",
        f"└ 临时: {format_size(stats['temp_bytes'])}（{stats['temp_count']} 个）"
        + ("" if include_temp else "，未计入"),
    ]

    try:
        quota = float(quota_gb)
    except (TypeError, ValueError):
        quota = 0.0

    if quota > 0:
        quota_bytes = quota * _BYTES_PER_GB
        remaining = quota_bytes - float(used_bytes)
        lines.append(f"容量: {format_size(quota_bytes)}（配置值）")
        if remaining < 0:
            lines.append(f"剩余: 已超额 {format_size(-remaining)}")
        else:
            lines.append(f"剩余: {format_size(remaining)}")
    else:
        lines.append("容量: 未配置（仅显示已用，可在配置中设置）")

    lines.append("注: 已用为按文件大小自行统计（根目录 + 一级子文件夹）")
    return "\n".join(lines)


# endregion
