"""JM Downloader Plugin - Core Module"""

# 消息匹配正则：/jm 350234 或 /jm 350234 1-30
JM_MESSAGE_PATTERN = r"(?s).*(?:/[Jj][Mm]\s*(\d{5,8})(?:\s+(\d{1,3})-(\d{1,3}))?)"
