# astrbot_plugin_jm_downloader

> ⚠️ **重要警告**
>
> - 本插件会**不可避免地使 Bot 发送 R18 内容**，请严格遵守相关法律法规及平台使用条款
> - 目前仅支持**单线程下载**，大群使用请**慎重**，建议善用**白名单功能**限制可用群组
> - Bot 账号因使用本插件被封禁，**与插件开发者无关**，使用者需自行承担风险

AstrBot 禁漫天堂（JMComic）本子下载插件，支持 QQ 群内通过 `.jm` 指令触发，自动下载并发送为加密 ZIP 压缩包和/或 PDF 文件，附带合并转发图片预览。

## 特性

- **`.jm <ID> [章节范围]`** 指令下载，支持全量和分段
- **≤30 章自动全量**，>30 章需分段（每段最多 30 章）
- **合并转发预览**：本子信息 + 前 N 张预览图（N 可配置）
- **输出格式**：加密 ZIP（webp→jpg 自动转换）/ PDF（体积优化）/ 两者
- **缓存机制**：图片源文件、PDF、ZIP 均缓存，同章节范围重复请求直接发送
- **缓存分离**：不同章节范围的输出文件独立命名，不会串内容
- **群组限速**：同一群组默认 60 秒间隔，可配置
- **自动清理**：按保留天数 + 总大小上限自动清理过期缓存
- **手动清理**：`.jm del-cache` 指令

## 指令列表

| 指令 | 说明 |
|------|------|
| `/jm 350234` | 下载本子 350234（≤30章全量） |
| `/jm 350234 1-30` | 下载第 1-30 章 |
| `/jm 350234 31-60` | 下载第 31-60 章 |
| `/jm del-cache` | 手动清除所有缓存 |
| `/jmhelp` | 以合并转发形式显示帮助 |
| `/jmpic` / `/jmzip` / `/jmpdf` / `/jmall` | 快捷模式（覆盖默认设置） |
| `.` 前缀同样兼容 | — |

## 安装

### 依赖

```
python >= 3.10
```

安装 Python 依赖：

```bash
pip install -r requirements.txt
```

`pyminizip` 需要系统安装 `zlib` 开发库：

```bash
# Ubuntu/Debian
sudo apt install zlib1g-dev

# CentOS/RHEL
sudo yum install zlib-devel
```

### 安装插件

1. 在 AstrBot WebUI → 插件管理 → 上传插件 zip 包
2. 或手动克隆到 `AstrBot/data/plugins/astrbot_plugin_jm_downloader/`
3. 重启 AstrBot 或重新加载插件

### 打包

```bash
bash pack.sh
```

## 配置项

在 AstrBot WebUI 插件配置中可修改以下参数：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `output_mode` | 下拉 | 压缩包 | 发送模式：压缩包 / PDF / 两者 |
| `proxy` | 字符串 | (空) | HTTP 代理地址 |
| `jm_cookies` | 字符串 | (空) | JM 网站 Cookie |
| `zip_password` | 字符串 | FloatSakura | ZIP 加密密码，留空则不加密 |
| `max_total_images` | 整数 | 20 | 合并转发预览图片数量 |
| `cache_retention_days` | 整数 | 3 | 缓存保留天数 |
| `cache_max_size_gb` | 浮点数 | 3.0 | 缓存总大小上限 (GB) |
| `rate_limit_seconds` | 整数 | 60 | 每群限速间隔 (秒) |
| `merge_send_as_sender` | 布尔 | false | 合并转发显示为发送者 |
| `allow_private_chat` | 布尔 | true | 允许私聊下载（关闭则仅群聊） |
| `admin_qq` | 字符串 | (空) | 管理员QQ，留空提示「请联系本群管理员」 |
| `error_notify_mode` | 下拉 | 通知 | 错误通知模式：静默 / 通知 |

## 工作流程

1. 用户发送 `.jm <ID> [范围]`
2. 群组限速检查 → 通过后获取本子元数据
3. 缓存检查（按范围精确匹配）
4. 如果有缓存 → 直接发送
5. 否则逐章下载图片 → 合并转发预览 → 生成 PDF/ZIP → 发送
6. 缓存自动清理检查

## 项目结构

```
astrbot_plugin_jm_downloader/
├── main.py                  # 插件入口，指令监听
├── metadata.yaml            # 插件元信息
├── requirements.txt         # Python 依赖
├── _conf_schema.json        # WebUI 配置项
├── README.md
└── core/
    ├── __init__.py           # 包声明 + 正则模式
    ├── jm_handler.py         # 核心下载 + 合并转发逻辑
    ├── jm_tools.py           # PDF/ZIP 生成 + webp→jpg 转换
    ├── jm_paths.py           # 缓存路径管理
    ├── jm_cache.py           # 缓存清理策略
    └── jm_rate_limiter.py    # 群级限速器
```

## 依赖库

- [jmcomic](https://github.com/hect0x7/JMComic-Crawler-Python) — JM 下载核心
- [Pillow](https://python-pillow.org/) — 图片格式转换与压缩
- [reportlab](https://www.reportlab.com/) — PDF 生成
- [pyminizip](https://github.com/smihica/pyminizip) — ZIP 加密
- [httpx](https://www.python-httpx.org/) — HTTP 客户端

## 版本历史

### v1.2.1
- 新增私聊下载总开关（allow_private_chat），关闭后仅允许群聊使用
- admin_qq 默认值改为空，留空时提示"请联系本群管理员"而非硬编码 QQ 号
- README 开头新增 R18 内容警告、单线程提示及封号免责声明

### v1.2.0
- 新增快捷模式指令: .jmpic / .jmzip / .jmpdf / .jmall，优先级高于插件设置
- 新增"不发送"输出模式（仅预览，不生成PDF/ZIP）
- 修复跨场景缓存预览图污染（.preview.jpg 被错误收集）
- .jmhelp 新增快捷模式说明

### v1.1.5
- 私聊/群聊预览图片数分开配置（preview_images_group 默认5，preview_images_private 默认100）

### v1.1.4
- 私聊 PDF/ZIP 始终以单独文件发送（修复合并转发失效）
- 群聊 PDF/ZIP 合并转发可配置开关（file_merge_forward_enabled，默认开启）
- 下载开始消息增加预览图发送失败警告提示

### v1.1.3
- 新增群白名单功能（whitelist_enabled / whitelist_groups），默认开启
- 新增管理员QQ配置（admin_qq），白名单拒绝和帮助中显示
- 支持 `/jm` 和 `.jm` 两种指令前缀
- .jmhelp 帮助内容更新

### v1.1.2
- 预览图 webp→jpg 自动转换，修复 QQ 合并转发 webp 随机失败
- 缓存命中时提示前缀（💾 缓存命中 / 📦 图片缓存）
- 预览图数量默认降至 5 张

### v1.1.1
- PDF/ZIP 改为合并转发发送（File 节点）
- 章节数上限可配置（max_chapters_per_segment，默认30）
- WebUI hint 全面更新

### v1.1.0
- webp 自动转 jpg 后打包 ZIP
- ZIP 加密支持，默认密码 FloatSakura
- PDF 体积优化（Pillow 预处理压缩）
- `.jm del-cache` 手动清缓存
- `.jmhelp` 帮助指令
- 缓存检查与章节范围严格对齐

### v1.0.0
- 初始版本
- `.jm` 指令下载
- 合并转发预览
- 压缩包/PDF 输出
- 缓存与自动清理

## 致谢

本项目基于以下开源项目构建：

- **[JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)** — 禁漫天堂漫画下载 Python 爬虫库，提供了核心的 JM 下载 API。感谢作者 [hect0x7](https://github.com/hect0x7) 的开源贡献。

## 许可证

MIT

## 作者

FloatSakura

## PS

第一次用AI写的插件也是第一次在GitHub上传内容，可能不太会用，多多包涵。
Email：FloatSakura@Outlook.com
