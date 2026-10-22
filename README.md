<div align="center">

<img src="logo.png" alt="阿里云TTS助手" width="128" height="128" />

# 阿里云TTS助手

基于阿里云百炼语音服务的 AstrBot 插件，支持音色创建、文字朗读、随机语音回复与在线试听。

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![AstrBot](https://img.shields.io/badge/AstrBot-plugin-7C3AED)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](LICENSE)

[安装](#安装) · [配置](#配置) · [使用方法](#使用方法) · [常见问题](#常见问题) · [更新日志](CHANGELOG.md)

</div>

## 功能

- **独立语音配置**：自行配置 API Key、业务空间、模型与音色，支持多份配置保存和切换。
- **音色创建**：上传 WAV 参考录音，创建云端音色、查询状态并试听。
- **生成与试听**：在 WebUI 调整语速、音调、音量等参数，生成音频并播放或下载。
- **聊天朗读**：通过命令或自然语言工具朗读文字，支持汉语、日语和英语。
- **随机语音回复**：按概率将 AI 回复转为语音，可同时发送原文，并设置会话范围与冷却。
- **朗读与发音控制**：支持固定、混合、动态参数模式，单句语气指导、分段停顿和分语言持久词典。
- **翻译模式**：调用 AstrBot 已登记的聊天模型，将文本翻译成汉语、日语或英语后朗读。

## 支持平台与分类

分类标签：**语音合成 · 音色复刻 · AI工具 · 多媒体**。

插件通过 AstrBot 通用 `Record` 组件发送音频，不依赖某一家聊天平台。以下声明依据 2026-10-22 检查的 AstrBot 官方适配器源码与本插件调用方式推断，尚未逐平台进行真实账号联调；使用较旧 AstrBot 版本时请核对对应适配器实现。

| 平台 | `support_platforms` 标识 | 使用条件与表现 |
| --- | --- | --- |
| QQ / OneBot v11 | `aiocqhttp` | OneBot 实现须支持发送 record；音频转码由适配器或协议端处理 |
| QQ 官方机器人 | `qq_official`、`qq_official_webhook` | 依赖官方接口的语音消息能力与权限，不能据此保证所有频道场景；适配器执行 Silk 转码 |
| Telegram | `telegram` | 适配器发送语音，并提供音频格式回退处理 |
| Discord | `discord` | 适配器发送音频/语音消息；不是在语音频道中实时播报 |
| 飞书 / Lark | `lark` | 适配器将音频转为 Opus，需要可用的转码工具及上传权限 |
| 钉钉 | `dingtalk` | 适配器转为 OGG/AMR，需媒体上传权限与转码能力 |
| 企业微信 | `wecom` | 适配器转为 AMR 后上传语音，需相应应用权限与转码能力 |
| 企业微信智能机器人 | `wecom_ai_bot` | 需配置可发送语音的群机器人 Webhook；仅文本流式连接不足以发送语音 |
| 微信公众号 | `weixin_official_account` | 适配器转为 AMR，受公众号接口权限和消息发送规则限制 |
| Slack | `slack` | 以音频文件上传，需文件上传权限 |
| KOOK | `kook` | 以音频卡片发送，需资源上传权限 |
| Satori | `satori` | 适配器输出 audio 元素，最终是否播放取决于所连接的协议实现；使用 WAV |
| LINE | `line` | 音频须可通过公网 HTTPS 访问，建议 MP3；需配置 AstrBot 文件服务和可访问的回调地址 |
| Mattermost | `mattermost` | 以音频附件上传，需文件上传权限 |

AstrBot 内置 WebChat 的适配器也支持音频播放，建议使用默认 WAV；它不在官方文档列出的 `support_platforms` 键列表中，因此未作为市场平台标签填写。WebUI“生成与试听”独立于聊天平台。

未声明的平台包括 `weixin_oc`（本次检查的发送分支未处理 Record）、`misskey`（未确认 Record 发送实现）及未核实的外部适配器。不同平台对格式、时长和大小的限制仍以其接口为准，平台标签表示具备对应代码路径，不表示所有格式和会话类型均已实测。

平台字段及图标遵循 [AstrBot 插件开发规范](https://docs.astrbot.app/dev/star/plugin-new.html)，分类字段遵循[插件发布规范](https://docs.astrbot.app/dev/star/plugin-publish.html)。

## 环境要求

- AstrBot：完整 WebUI 需要支持插件可视化视图、`AstrBotPluginView` bridge、`astrbot.api.web` 与插件上传接口。旧版请先升级；接口适配记录见[进阶说明](docs/advanced.md#分类-webui-与实时调参)。
- 阿里云百炼：可用的 API Key、业务空间域名，以及相互匹配的语音模型和音色。
- 聊天平台：适配器需要支持发送语音消息。WebUI 试听不依赖聊天适配器。
- 自然语言工具、翻译与动态参数规划需要可用的聊天模型；命令朗读和普通页面试听无需这些能力。

插件使用 Python 标准库调用语音 HTTP 接口，无需安装额外语音 SDK；安装时无需 Node.js 或构建前端。

## 安装

在 AstrBot 管理面板的插件管理中选择从仓库链接安装，填入：

```text
https://github.com/Shennoter/astrbot_plugin_qwen_audio_tts
```

也可将仓库克隆到 AstrBot 的 `data/plugins/` 目录，然后重启或重载插件：

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/Shennoter/astrbot_plugin_qwen_audio_tts.git
```

确认 `data/plugins/astrbot_plugin_qwen_audio_tts/` 下直接包含 `main.py` 和 `metadata.yaml`。

## 配置

1. 打开插件配置中的“语音服务配置”，填写以下信息并保存。
2. 重载插件，从插件详情或侧边栏打开 **阿里云TTS助手**。
3. 在“生成与试听”中输入短句，点击“生成音频”确认配置可用。
4. 后续在 WebUI 调整配置后，点击右上角“保存全部设置”，将设置应用到聊天。

| 配置 | 说明 |
| --- | --- |
| API Key | 自己账户的百炼 API Key；WebUI 不回显已保存的密钥，留空保存表示保留 |
| 业务空间 | 完整域名，例如 `ws-xxx.cn-beijing.maas.aliyuncs.com` 或 `ws-xxx.ap-southeast-1.maas.aliyuncs.com` |
| 模型 | 填写语音模型 ID，例如 `qwen-audio-3.1-tts-plus` 或 `cosyvoice-v3.5-plus`，以服务端实际支持为准 |
| 音色 | 填写同一账户、同一业务空间内，与所选模型匹配的音色 ID |

模型 ID 支持自由输入；支持范围取决于对应 HTTP 协议及云端服务。CosyVoice HTTP 合成目前限北京地域。没有音色时，可以先保存服务连接，再进入“音色创建”。

### WebUI 分类

| 页面 | 用途 |
| --- | --- |
| 生成与试听 | 输入文本、设置本次朗读指导、生成并试听 |
| 音色创建 | 上传参考录音、创建音色、查询状态、填入并试听 |
| 服务与音色 | 管理服务连接、模型音色、多份配置及音频保存 |
| 合成参数 | 设置格式、采样率、语速、音量、音调等 |
| 朗读指导 | 选择固定、混合或动态参数模式，配置分段限额 |
| 随机语音 | 设置概率、字数、冷却与回复处理 |
| 发音与词典 | 设置单次发音模式，编辑汉语、日语、英语词典 |
| 会话范围 | 设置私聊、群聊、平台及会话白名单或黑名单 |
| 朗读与工具 | 设置自然语言调用、翻译模式及语音与原文同时发送 |

试听使用当前页面草稿；保存后才用于后续聊天。API Key 随配置存放在服务端，不存入浏览器存储。

## 使用方法

### 命令朗读

| 命令 | 说明 |
| --- | --- |
| `/说话 zh 你好，很高兴认识你。` | 汉语朗读 |
| `/说话 en Hello, nice to meet you.` | 英语朗读 |
| `/说话 ja こんにちは。` | 日语朗读 |
| `/说话参数 ja {"text":"鳳凰院凶真","hot_fix":{"replace":[{"鳳凰院凶真":"ほうおういん きょうま"}]}}` | 本次指定读音，受发音模式控制 |
| `/语音设置 概率 30` | 管理员修改随机语音概率 |

`/说话` 的别名是 `/朗读`、`/qvoice`。指定语言默认不会翻译文本；需要翻译时，请在“朗读与工具”中开启翻译模式。会话白名单使用 AstrBot 内置 `/sid` 返回的完整 UMO。

### 自然语言调用

在插件中启用“自然语言调用”，并在 AstrBot 工具管理中允许 `qwen_speak`、`qwen_voice_control` 和 `qwen_voice_dictionary`。聊天模型需要支持并实际执行工具调用，例如：

```text
用温柔的语气读这句话：今天也辛苦了。
用英语说“Hello, nice to meet you.”。
以后都把“鳳凰院凶真”读成“ほうおういん きょうま”，加入日语词典。
```

修改全局设置或持久词典需要管理员权限；单次朗读要求不会自动写入持久配置。详细参数与模式说明见[进阶使用](docs/advanced.md)。

### 创建音色

1. 在“服务与音色”中保存 API Key 和业务空间。
2. 进入“音色创建”，选择 WAV 并点击“上传并校验”。支持 16-bit PCM、单/双声道、16～192 kHz、5～60 秒、最大 10 MiB；建议使用 10～20 秒清晰单人录音。
3. 填写目标模型、音色前缀、语言等参数，点击“创建云端音色”。这一步才会发起云端创建请求。
4. 刷新任务并查询云端状态，状态为 `OK` 后点击“填入并试听”，确认效果后保存设置。

音色绑定创建时的目标模型，切换模型需要匹配的音色。创建超时或结果未知时，请先在百炼控制台核查结果。

### 随机语音回复

默认转换概率为 20%，最多 200 字，同一会话间隔至少 10 秒。超过字数、处于冷却、包含非文字内容或合成失败时保留原消息。使用时请关闭 AstrBot 原生“自动 TTS”和聊天流式输出。

## 常见问题

| 问题 | 排查方法 |
| --- | --- |
| 无法合成或音色不可用 | 核对 API Key、地域、业务空间、模型和音色归属；切换模型时同步切换音色 |
| 找不到 WebUI 或打开失败 | 更新 AstrBot，确认已登录 Dashboard 且拥有插件权限，再重载插件 |
| 自然语言没有生成语音 | 检查聊天模型是否支持工具调用，以及插件开关、人设和会话的工具权限 |
| 发音不准确 | 在对应语言词典中设置替换读音；单次参数需选择合适的发音模式 |
| 配置改了但聊天没变 | 点击“保存全部设置”；试听草稿不会自动保存 |
| 随机回复没有变语音 | 检查概率、最大字数、冷却、会话范围及原生自动 TTS 冲突 |
| 音频会保存在本地吗 | 默认不长期保存；可在“服务与音色”开启，默认保留 24 小时，详见[音频保存说明](docs/advanced.md#是否保存生成音频) |

语音合成、音色创建，以及翻译/动态规划的模型调用可能产生费用。插件不会自动重试已发送的收费合成或音色创建请求。公开部署时的上传限制和网关设置见[部署说明](docs/advanced.md#上传接口安全分析与部署边界)。

## 开发与反馈

- [提交问题](https://github.com/Shennoter/astrbot_plugin_qwen_audio_tts/issues)：请提供 AstrBot 版本、插件版本、复现步骤及脱敏日志。
- [前端构建](frontend/README.md)：React 与 Material UI 的源码、锁文件及构建方法。
- [进阶说明](docs/advanced.md)：发音模式、分段控制、完整参数与部署细节。
- [更新日志](CHANGELOG.md)：版本变更记录。

在插件目录运行离线测试，无需 API Key，不调用真实语音服务：

```bash
python run_tests.py
```

## 许可证

本项目采用 [GNU AGPL v3.0](LICENSE)（`AGPL-3.0-only`）。Copyright (C) 2026 Shennoter。

随插件打包的第三方组件保留各自许可证，详见 [THIRD_PARTY_LICENSES.txt](views/preview/THIRD_PARTY_LICENSES.txt)。
