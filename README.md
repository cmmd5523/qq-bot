# QQ Bot

基于 Python、NoneBot2 和 OneBot V11 的机器人，使用 NapCat 接入 QQ，可选接入 Telegram。支持文字对话、联网搜索、记忆、QQ 语音识别与合成，以及原项目的直播、游戏、图片和权限管理插件。

这是移除私人配置、账号、数据库、登录状态和部署基础设施信息后的源码发布版。第三方功能需要自行配置，不保证所有插件开箱即用。

## 快速开始

推荐 Python 3.12。先安装系统 FFmpeg 和中文字体，再执行：

```sh
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-local.txt
python -m playwright install --with-deps chromium
cp sample_config.py config.py
cp .env.example .env
cp system_prompt.example.md system_prompt.md
```

填写 config.py 中自己的管理员 QQ 号和需要的 API Key。保持 `.env` 的 `HOST=127.0.0.1`、`PORT=5700`。不使用 Telegram 时留空其 Token；同一 Token 只运行一个长轮询实例。

配置好 NapCat，反向 WebSocket 指向 `ws://127.0.0.1:5700/onebot/v11/ws`，然后启动：

```sh
python qq_bot_core.py
```

QQ 登录后应看到 `Bot ... connected`。私聊可用 `/聊天助手 你好`，群聊可 @机器人提问。语音使用 `/语音 你好` 或向机器人发送语音；语音模型需自行下载至 `runtime/whisper-small`。NapCat 与机器人须共享相同绝对路径的音视频文件，否则只能使用文字功能。

## 配置和可选组件

- config.py、.env、system_prompt.md、config/、runtime/、数据库和日志均不应提交。
- `sample_config.py`、`.env.example`、`system_prompt.example.md` 仅包含空值或通用示例。
- 图片词云与弹幕报告可选使用思源黑体：自行从官方 Source Han Sans 项目获取字体与许可证，放入 `Services/util/SourceHanSansSC-Bold.otf`。本仓库不附带字体二进制。
- 仅附带通用游戏静态资源；数据库在首次运行时创建。
- HK/VPS 等实际服务器配置不包含在本仓库。
- Docker 部署见 [docs/部署说明.md](docs/部署说明.md)。

## 来源与许可证

本项目基于 [remiliacn/qqBot](https://github.com/remiliacn/qqBot)（原 README 另引用 [Lingye-Bot](https://github.com/remiliacn/Lingye-Bot) 文档），并加入聊天、记忆、搜索、语音和 Telegram 等功能。保留原有版权声明与 GNU AGPL v3 许可证，详见 [LICENSE](LICENSE)。NapCat、QQ 及其他第三方组件遵循各自许可证，未随本仓库分发。

## 群聊自主参与

默认关闭。群主、群管理员或机器人主人可在QQ群使用：

- `/自主聊天 开启`：启用本群的评分与自主参与。
- `/自主聊天 关闭`：取消待发送任务并清空临时聊天窗口。
- `/自主聊天 状态`：查看开关和当前分数。

按最近3分钟最多30条文字的提问、话题重合、观点邀请和多成员参与打分，65分起才交给模型决定是否发言。等8秒聊天停顿；发言/明确呼叫后的冷却180秒，每群每小时最多6次自动模型判定。重复、低信息、链接和命令跳过；直接@或命令仍按原触发处理。临时消息窗口不写文件，后台定期过期清理；仅开关持久化在被忽略的config目录中。

Telegram默认拒绝未授权用户。仅`.env`中`TELEGRAM_ALLOWED_USER_IDS`列出的数字用户ID可使用（英文逗号分隔）；为空或配置非法时拒绝所有人。未授权用户私聊/start或/help只会看到未授权提示及自己的ID，不会获得权限或调用模型。群聊同样检查发送者的用户ID，再判断是否@或回复机器人。修改配置后重启bot。私聊上下文按chat_id隔离，获准用户共用部署者API Key。

## 私有管理后台

新增独立管理网页：状态与服务控制、QQ/TG接入与白名单、群组自主参与、记忆、提示词与凭据、用量、脱敏日志、账号审计和配置备份恢复。默认仅回环监听，通过自己的LAN隧道访问。部署和安全边界见[admin/README.md](admin/README.md)。

QQ私聊现在直接发送普通文字即可对话，无需唤醒词；已有命令、语音处理和封禁规则保留。群聊仍使用@/回复/按群开启的自主参与，避免全群每条消息均触发。
