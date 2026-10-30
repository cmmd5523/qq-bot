# 私有后台

提供服务状态与控制、QQ登录二维码、Telegram发送者白名单、群组自主参与、临时上下文清理、记忆审批/编辑、只写入密钥、提示词、DeepSeek用量、脱敏日志、账号权限、审计及配置备份恢复。

后台与机器人使用独立Python虚拟环境。`app.py`以非特权账号运行；`helper.py`由root持有，只开放固定动作，不能执行任意命令或选择任意容器。后台不挂载Docker socket。默认目录是`/opt/qq-bot-deploy/app`，如调整部署布局，应审查并同步修改助手常量。

## 安装与隔离

1. 创建非登录服务账号和独立数据目录；安装`admin/requirements.txt`到独立虚拟环境。不要升级或覆盖机器人的Pydantic环境。
2. 将本目录部署为root所有、服务账号只读。将helper.py安装至`/usr/local/sbin/qqbot-admin-helper`，root:root、0755；禁止服务账号修改该文件。
3. sudoers只允许该服务账号无参数执行上述助手，使用命令后的`""`限制参数；不要授权通配shell或docker命令。用visudo验证。
4. 为机器人私有.env配置随机`ADMIN_BRIDGE_TOKEN`（至少32字符），启用admin_bridge插件并重启机器人。管理桥只使用现有回环接口，不应单独暴露到公网。
5. 设置`QQBOT_ADMIN_DATA`为仅服务账号可写的目录；用`python app.py --init`从标准输入读取`{"username":"...","password":"..."}`初始化所有者。密码至少12位，不要作为命令行参数或写入仓库。
6. 用uvicorn运行`app:app --host 127.0.0.1 --port 5710`。通过你自己的LAN SSH转发访问，不创建公网域名或公网监听。

会话8小时有效，HttpOnly、SameSite=Strict。HTTP内网入口使用非Secure Cookie；如果部署为HTTPS入口，应设置`QQBOT_ADMIN_SECURE_COOKIE=true`。所有写操作核对CSRF；8次失败登录后同源地址锁定15分钟。所有者可创建管理员和只读账号；只读账号无法修改机器人配置。

## 数据边界

- 密钥只显示是否配置，可替换或明确清除，接口不回传原值。
- 提示词与日志是私有数据，只有已登录账号可读。常见Token和已配置密钥会脱敏，不能将后台设为匿名访问。
- DeepSeek/Kimi调用统计从管理插件启用后开始，包含辅助模型调用；估算费用依据项目配置，不代表供应商账单。记录不包含提示词或回复。
- 上下文、记忆的清理仅影响机器人本地数据，不删除QQ或Telegram平台历史。
- 配置备份含真实凭据，仅存服务器私有目录；恢复前自动保留当前配置快照，不接受任意文件上传或自定义路径。

## 检查

`python -m unittest discover -s tests -p test_admin.py`验证登录、CSRF、只读权限、会话撤销、限速、操作白名单、密钥脱敏、备份恢复和SQL参数绑定。测试使用临时目录，不控制生产服务。

## 多模态与思考设置

后台模型页显示各模型支持的图片、视频能力及主对话思考开关。辅助判断和自主插话保留快速模式。联网统一使用DS新Search/Fetch API。详细限制见docs/模型与媒体.md。
