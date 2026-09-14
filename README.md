# astrbot_plugin_jm_downloader

> **测试环境说明**
>
> 本插件仅在以下环境测试通过，未验证其他环境下的兼容性，如遇问题可能需要自行修改适配：
> - **系统**：Ubuntu 24.04.4 LTS x86_64
> - **部署方式**：AstrBot 与 NapCat 分别部署在独立 Docker 容器中，通过网络连接通信
> - **系统**：Windows 11 专业版 25H2 x86_64，AstrBot 与 NapCat 本地部署（AstrBot v4.26.0-beta.4）

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
- **群文件空间感知**：群聊发送文件前检查群文件剩余空间，低于阈值时提醒（**仅提醒，仍会继续发送**）
- **群文件自动清理**：按保留天数删除 **Bot 自己上传** 的群文件，不会删除群成员上传的文件
- **群文件管理指令**：`/jmspace` 查看空间占用，`/del-files` 手动清理 Bot 上传的过期文件

## 指令列表

| 指令 | 说明 |
|------|------|
| `/jm 350234` | 下载本子 350234（≤30章全量） |
| `/jm 350234 1-30` | 下载第 1-30 章 |
| `/jm 350234 31-60` | 下载第 31-60 章 |
| `/jm del-cache` | 手动清除所有缓存 |
| `/jmhelp` | 以合并转发形式显示帮助 |
| `/jmpic` / `/jmzip` / `/jmpdf` / `/jmall` | 快捷模式（覆盖默认设置） |
| `/jmspace` | 查看本群群文件空间使用情况 |
| `/del-files` | 清理本群中 Bot 上传的过期群文件 |
| `.` 前缀同样兼容 | — |

## 安装

### 依赖

```
python >= 3.10
```

### 安装插件

1. 在 AstrBot WebUI → 插件管理 → 安装插件：
   - **上传 zip 包**：选择打包好的 zip 文件上传
   - **从链接安装**：输入仓库地址 `https://github.com/FloatSakura/astrbot_plugin_jm_downloader`
2. 重启 AstrBot 或重新加载插件

> **打包注意事项**：压缩包内文件应位于**根目录**，不要包含外层文件夹（即 `main.py`、`metadata.yaml` 等在 zip 根目录下，而非 `astrbot_plugin_jm_downloader/main.py`）。

### 手动安装依赖（可选）

> ⚠️ **仅在 zip 包上传或从链接安装失败时需要手动安装依赖**

在 AstrBot WebUI → 左侧 **平台日志** → 右上角 **安装pip库**，逐行输入以下内容并执行：

```
jmcomic>=2.0.0
httpx>=0.24.0
reportlab>=4.0.0
Pillow>=10.0.0
pyzipper>=0.3.0
cryptography
```

### 打包

```bash
cd astrbot_plugin_jm_downloader
zip -r ../astrbot_plugin_jm_downloader.zip . -x ".git/*" "__pycache__/*" "*.pyc"
```

## 配置项

在 AstrBot WebUI 插件配置中可修改，已按用途分为 6 组：

<details open>
<summary><b>下载与输出</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `output_mode` | 下拉 | 压缩包 | 发送模式：不发送 / 压缩包 / PDF / 两者 |
| `zip_password` | 字符串 | FloatSakura | ZIP 加密密码，留空则不加密 |
| `max_chapters_per_segment` | 整数 | 30 | 单次 `.jm` 最多下载章节数 |
| `preview_images_group` | 整数 | 5 | 群聊预览图片数量 |
| `preview_images_private` | 整数 | 100 | 私聊预览图片数量 |
| `merge_send_as_sender` | 布尔 | false | 合并转发显示为发送者 |
| `file_merge_forward_enabled` | 布尔 | true | 群聊 PDF/ZIP 以合并转发发送 |

</details>

<details>
<summary><b>网络与代理</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `proxy` | 字符串 | (空) | HTTP 代理地址 |
| `jm_cookies` | 字符串 | (空) | JM 网站 Cookie |

</details>

<details>
<summary><b>访问控制</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `whitelist_enabled` | 布尔 | true | 开启群白名单 |
| `whitelist_groups` | 字符串 | (空) | 白名单群号，英文逗号分隔 |
| `allow_private_chat` | 布尔 | true | 允许私聊下载 |
| `admin_qq` | 字符串 | (空) | 管理员QQ，留空提示「请联系本群管理员」；同时作为 `/del-files` 的「Bot管理员」 |

</details>

<details>
<summary><b>缓存与限速</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `cache_retention_days` | 整数 | 3 | 缓存保留天数 |
| `cache_max_size_gb` | 浮点数 | 3.0 | 缓存总大小上限 (GB) |
| `rate_limit_seconds` | 整数 | 60 | 每群限速间隔 (秒) |
| `error_notify_mode` | 下拉 | 通知 | 错误通知模式：静默 / 通知 |

</details>

<details>
<summary><b>群文件空间</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `bot_qq` | 字符串 | (空) | **机器人自身 QQ 号**。留空则禁用全部群文件管理功能（清理 / `/del-files` / `/jmspace`），下载与发送不受影响 |
| `space_check_enabled` | 布尔 | true | 群聊发送前统计群文件占用（需 `bot_qq`） |
| `group_file_quota_gb` | 浮点数 | 10.0 | 本群群文件容量 (GB)，用于计算剩余空间。**必须手动填写**（见下方说明），0 表示不计算剩余 |
| `space_warn_remaining_gb` | 浮点数 | 0.5 | 剩余空间告警阈值 (GB)，仅提醒不阻止发送 |
| `space_warn_notify` | 布尔 | true | 低于阈值时是否在群内发送提醒 |
| `space_include_temp_files` | 布尔 | false | 空间统计是否计入「临时文件」（见下方说明） |
| `jmspace_enabled` | 布尔 | true | 启用 `/jmspace` 指令 |

</details>

<details>
<summary><b>群文件清理</b></summary>

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `auto_clean_enabled` | 布尔 | false | 群文件自动清理总开关（需 `bot_qq`） |
| `auto_clean_days` | 整数 | 30 | 删除 Bot 上传且超过 n 天的群文件，**0 表示永不删除** |
| `auto_clean_on_send` | 布尔 | true | 清理时机：发送前执行一次 |
| `auto_clean_scheduled` | 布尔 | false | 清理时机：插件加载后按间隔定时清理 |
| `auto_clean_interval_hours` | 整数 | 24 | 定时清理间隔 (小时) |
| `del_files_enabled` | 布尔 | true | 启用 `/del-files` 指令 |
| `del_files_audience` | 多选 | 群管理员 | `/del-files` 可用人群，可多选：群主 / 群管理员 / Bot管理员 / 群员 |

</details>

> **从旧版本升级**：配置项已由平铺的 `jm_settings` 拆分为上述分组。插件启动时会**自动把旧配置迁移到新分组**（只执行一次，迁移后旧键复位），因此无需手动重填。
> 旧键仍保留在配置文件中但已隐藏，请勿手动修改。

## 工作流程

1. 用户发送 `.jm <ID> [范围]`
2. 群组限速检查 → 通过后获取本子元数据
3. 缓存检查（按范围精确匹配）
4. （群聊）清理 Bot 上传的过期群文件 → 检查群文件剩余空间，低于阈值时提醒
5. 如果有缓存 → 直接发送
6. 否则逐章下载图片 → 合并转发预览 → 生成 PDF/ZIP → 发送
7. 缓存自动清理检查

## 群文件管理

群聊发送的 PDF/ZIP 会进入群文件系统并占用群文件空间。本插件提供了对应的管理能力，
但**必须先填写「机器人QQ号」(bot_qq)**——插件据此识别群文件列表中哪些文件是自己上传的：

- `/jmspace` — 查看本群群文件已用 / 总量 / 剩余空间与文件数
- `/del-files` — 手动清理本群中 Bot 上传且超过保留天数的文件
- 自动清理 — 分「发送前」与「定时」两种时机，可在配置中分别开关

**`/del-files` 可用人群可多选**（`del_files_audience`），勾选多项时满足任意一项即可：

| 选项 | 含义 |
|------|------|
| 群主 | 本群群主 |
| 群管理员 | 本群群主或管理员 |
| Bot管理员 | 「访问控制」中填写的 `admin_qq` |
| 群员 | 普通群成员（**不含**群主与群管理员） |

默认只勾选「群管理员」。若要放给所有人，需同时勾选「群主」「群管理员」「群员」。
一个都不勾表示谁都不能用。群角色通过协议端查询，查询失败会拒绝执行。

> ⚠️ 删除是破坏性操作，请先确认保留天数设置。清理**只会删除上传者是 Bot 自己的文件**；
> 拿不到上传者或上传时间的条目会被跳过，绝不按文件名猜测删除。
> 删除后，此前合并转发消息中的文件节点将无法再下载，属预期现象。

### 关于空间统计的说明

协议端（NapCat）的 `get_group_file_system_info` 接口**不返回真实空间数据**，其
`used_space` / `total_space` 是硬编码的 `0` / `10GB`，只有 `file_count` 是真实统计。
因此本插件的做法是：

- **已用空间**：由插件自行按群文件列表中每个文件的 `size` 累加得出
  （统计范围为根目录 + 一级子文件夹，更深的嵌套无法通过接口发现）；
- **容量**：由 `group_file_quota_gb` 配置项提供，需要你按本群实际情况填写
  （QQ 普通群通常为 10GB，与 NapCat 自身的默认假设一致），插件据此换算剩余空间；
- **临时文件**：QQ 群文件里那类「聊天中发送、会自行过期」的文件默认**不计入**已用空间
  （判定依据是协议端返回的过期时间字段 `dead_time`，缺失或非法时按永久文件处理）。
  如需计入，把 `space_include_temp_files` 打开。无论是否计入，`/jmspace` 都会把
  永久 / 临时两部分分别列出来，方便核对分类是否正确。

若后续协议端支持返回真实容量，本项配置会被忽略或可保持默认。

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
    ├── jm_group_files.py     # 群文件空间查询 / 文件列举 / Bot 文件清理
    ├── jm_tools.py           # PDF/ZIP 生成 + webp→jpg 转换
    ├── jm_paths.py           # 缓存路径管理
    ├── jm_cache.py           # 缓存清理策略
    └── jm_rate_limiter.py    # 群级限速器
```

## 依赖库

- [jmcomic](https://github.com/hect0x7/JMComic-Crawler-Python) — JM 下载核心
- [Pillow](https://python-pillow.org/) — 图片格式转换与压缩
- [reportlab](https://www.reportlab.com/) — PDF 生成
- [pyzipper](https://github.com/danifus/pyzipper) — ZIP 加密
- [httpx](https://www.python-httpx.org/) — HTTP 客户端

## 版本历史

> **测试环境说明**：本项目仅在 Ubuntu 24.04.4 LTS 与 Windows 11 专业版 25H2 环境下测试，未能验证其他环境兼容性。

### v1.2.5
- 新增群文件空间管理
  - `/jmspace` 指令：查看本群群文件的已用 / 容量 / 剩余空间与文件数
  - 群聊发送文件前统计占用，剩余空间低于阈值时在群内提醒（**仅提醒，仍会继续发送**）
  - 已用空间由插件按群文件大小自行统计（协议端不返回真实容量），
    容量由新增的 `group_file_quota_gb` 配置提供（默认 10GB）
  - QQ 群文件中的「临时文件」（聊天中发送、会自行过期的那类）默认不计入占用，
    永久 / 临时分别列出便于核对，可用 `space_include_temp_files` 改为计入
- 新增群文件自动清理
  - 按保留天数（`auto_clean_days`，默认 30 天）删除 **Bot 自己上传** 的群文件，
    不会删除群成员上传的文件；设为 0 表示永不删除
  - 支持「发送前」与「插件加载后定时」两种清理时机，可分别开关
  - 仅在上传者与上传时间都明确匹配时才删除，信息缺失则跳过，避免误删
- 新增 `/del-files` 指令：手动清理本群中 Bot 上传的过期群文件
  - 可开关；可用人群可多选（群主 / 群管理员 / Bot管理员 / 群员），默认「群管理员」
- 新增「机器人QQ号」(`bot_qq`) 配置：用于识别群文件列表中哪些文件是本插件上传的。
  未填写时禁用清理、`/del-files` 与 `/jmspace`，**下载与发送不受影响**
- 配置项由平铺的 `jm_settings` 拆分为 6 个分组（下载与输出 / 网络与代理 / 访问控制 /
  缓存与限速 / 群文件空间 / 群文件清理）；插件启动时自动迁移旧版配置，无需手动重填

### v1.2.4
- ZIP 加密从 pyminizip 替换为 pyzipper（纯 Python，全平台兼容，安装零门槛）
- 修复 pyzipper 加密实现，使用 AESZipFile + setpassword 正确实现 AES 加密
- ZIP 加密恢复默认开启（密码 FloatSakura），pyzipper 未安装时自动回退无密码 ZIP

### v1.2.3
- pyminizip 改为可选依赖，未安装时自动回退为无密码 ZIP（stdlib zipfile），全平台兼容
- ZIP 加密密码默认值清空（留空则不加密）
- README 新增测试环境说明、打包注意事项，更新安装步骤（WebUI pip库安装）
- 依赖清单去掉系统级 zlib 安装说明

### v1.2.2
- 修复 Windows 环境下中文标题漫画 ZIP 生成失败（pyminizip 中文路径 OSError -102），改为纯 ASCII 临时路径先生成再移动

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
