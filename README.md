# send_issue_email MCP

把用户转人工时留下的**联系邮箱**和**问题描述**，通过 SMTP 发到固定服务邮箱。

Agent 只看到一个工具：`send_issue_email(email, question)`。

## 三个邮箱分别是什么

| 角色 | 来源 | 用途 |
|------|------|------|
| 发件人 `From` | `.env` 的 `SENDER_EMAIL` / `SMTP_USERNAME` | 登录 SMTP 真正寄出的账号 |
| 收件人 `To` | `.env` 的 `TARGET_EMAIL` | 你们固定用来收问题的服务邮箱 |
| 用户邮箱 | 工具参数 `email` | 写入主题、正文，并设为 `Reply-To`，方便客服直接回复用户 |

`.env` 是基础设施，部署一次即可。`email` / `question` 每次对话不同，由 Agent 从对话变量（如 `$邮箱`、`$问题`）传入。

## 环境要求

- Python 3.10+
- 能访问所用 SMTP（本机直连 Gmail 需 465/587 出网）

## 安装

```bash
cd send_issue_email_mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

按下面说明填好 `.env`，**不要把 `.env` 提交到仓库**。

## 配置 `.env`

### 必填：收件与 SMTP

```env
TARGET_EMAIL=issues@example.com

SMTP_HOST=smtp.gmail.com
SMTP_PORT=465
SMTP_USERNAME=your_account@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx
SMTP_USE_SSL=true

SENDER_EMAIL=your_account@gmail.com
SENDER_NAME=问题收集机器人
```

| 变量 | 说明 |
|------|------|
| `TARGET_EMAIL` | 问题收件箱，必填 |
| `SMTP_HOST` / `SMTP_PORT` | SMTP 服务器 |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | 登录凭据。Gmail 必须用[应用专用密码](https://myaccount.google.com/apppasswords)，不是登录密码 |
| `SMTP_USE_SSL` | `true` = SSL（465）；`false` = STARTTLS（587） |
| `SENDER_EMAIL` | 发件人，默认等于 `SMTP_USERNAME` |
| `SENDER_NAME` | 发件显示名 |

Gmail 两套等效组合：

| 方式 | `SMTP_PORT` | `SMTP_USE_SSL` |
|------|-------------|----------------|
| SSL（推荐） | `465` | `true` |
| STARTTLS | `587` | `false` |

Workspace / 学校 Google 账号可能被管理员关掉 SMTP，那种情况接不了。

### MCP 传输（远程客户端）

智齿会对**你填的 URL 根路径**发 `POST /`，因此远程默认如下：

```env
MCP_TRANSPORT=streamable-http
MCP_HOST=0.0.0.0
MCP_PORT=8765
MCP_PATH=/
```

| 变量 | 说明 |
|------|------|
| `MCP_TRANSPORT` | `streamable-http`：远程（智齿）；`stdio`：本机 Agent 拉进程；`sse`：旧版 GET `/sse`（智齿当前不是这种） |
| `MCP_HOST` | 监听地址，远程填 `0.0.0.0` |
| `MCP_PORT` | 监听端口。本机 `8000` 常被其他项目占用，可用 `8765` |
| `MCP_PATH` | HTTP 端点路径。智齿必须是 `/` |

改完 `.env` 后，若终端里已经 `export` 过同名变量，`load_dotenv()` **不会覆盖**。新开终端，或先 `unset MCP_TRANSPORT TARGET_EMAIL` 再启动。

## 先验证发信（不启动 MCP）

```bash
source .venv/bin/activate
python test_email.py
```

成功会打印：`成功：问题已发送至服务邮箱 ...`

脚本里的第一个参数是**模拟用户联系邮箱**，不是发件人。建议改成你自己的真实邮箱再测，避免 `example.com` 被收件方拦进垃圾箱。

SMTP 成功只代表邮局收下了信。若目标是企业飞书等邮箱，再查发件箱「已发送」、有无退信、对方垃圾箱/隔离。

## 启动 MCP（智齿 / 远程）

```bash
source .venv/bin/activate
python server.py
```

日志里应出现类似：

```
transport 'streamable-http' (stateless) on http://0.0.0.0:8765/
```

**不要同时开两份**，否则会 `address already in use`。需要重启时先停掉旧进程再启动。

### 智齿里填的 URL

传输类型选 **Streamable HTTP / HTTP**（不要选 SSE）。

URL **不要带 `/sse` 或 `/mcp`**，填能打到这台机器的根地址：

```
http://<这台机器的可达地址>:<MCP_PORT>/
```

例如局域网：`http://192.168.x.x:8765/`

智齿若是云端 SaaS，访问不到内网 IP。需要公网 IP、域名或隧道，URL 同样是根路径，不要加后缀。

保存后重新连接。服务日志应为 `POST / HTTP/1.1" 200`，而不是 404。

### 本机自检

```bash
curl -sS -X POST http://127.0.0.1:8765/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"check","version":"0.0.1"}}}'
```

正常返回带 `"jsonrpc":"2.0"` 和 `"serverInfo":{"name":"issue-collector"...}` 的 JSON。

## 启动 MCP（本机 stdio）

`.env` 设 `MCP_TRANSPORT=stdio` 后，由 Cursor 等本地客户端用命令拉起，例如：

```json
{
  "mcpServers": {
    "issue-collector": {
      "command": "/绝对路径/send_issue_email_mcp/.venv/bin/python",
      "args": ["/绝对路径/send_issue_email_mcp/server.py"]
    }
  }
}
```

stdio 模式下不要把普通日志打到 stdout。

## 工具约定

`send_issue_email(email: str, question: str) -> str`

- `email`：用户联系邮箱
- `question`：问题/诉求
- 返回以 `成功：` 或 `错误：` 开头的字符串，供 Agent 分支

邮件主题：`[问题收集] 来自用户 {email} ({时间})`  
正文含用户邮箱、提交时间、问题描述；`Reply-To` 为用户邮箱。

`send_message` 成功后立即返回 MCP 结果，SMTP `QUIT`/关连接在后台线程进行，避免关 SSL 拖过智齿超时。

## 日志

执行日志写在项目下的 `log/`，按天一个文件，当天多次启动是追加：

```
log/2026-08-19.log
```

含 HTTP 请求起止、工具调用、SMTP 连接/登录/发送耗时、后台关闭结果。不含 SMTP 密码。

## 排障

| 现象 | 原因 | 处理 |
|------|------|------|
| 智齿「服务响应格式异常」；日志 `POST / 404` | 客户端 POST 根路径，服务却在 `/sse` 或未启动对应传输 | `MCP_TRANSPORT=streamable-http`，`MCP_PATH=/`，URL 不要带 `/sse` |
| `[Errno 48] address already in use` | 端口已被上一份 MCP 占用 | 停掉旧进程后再启动，不要同时开两份 |
| 日志 `POST /sse 404` | URL 多写了 `/sse` | 改成根路径 `/` |
| 脚本仍发到旧 `TARGET_EMAIL` | 终端里已有旧环境变量，`load_dotenv()` 不覆盖 | `unset TARGET_EMAIL` 或新开终端 |
| SMTP 成功但收件箱没有 | 进了垃圾箱/隔离，或看错了收件账号 | 查发件箱已发送、退信、对方垃圾箱 |
| Gmail `535` / Password not accepted | 用了登录密码或未开两步验证 | 改用应用专用密码 |

`load_dotenv()` 在 `server.py` 启动时执行一次。只改 `.env` 不重启进程，运行中的服务不会读到新值。
