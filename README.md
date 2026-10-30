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
