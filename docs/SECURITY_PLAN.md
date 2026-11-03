# 安全扫描修改方案

依据 Codebuddy Security 报告 `send_issue_email_mcp-main-report-2026-10-30-10-18-32`（2026-10-30）。报告共 15 条：高危 1、中危 9、低危 5。扫描包早于当前仓库：当时内存限制写成 512Mi、日志盘 1Gi、命名空间 `send-issue-email`。现在 `deploy/k8s/mcp.yaml` 已在 `arko` 命名空间，内存上限 2048Mi，日志盘是 10Gi EFS。方案按当前代码写。

15 条里有多条是同一处缺陷被拆开报的。按改动合并成 7 项。不把 NodePort 改回 ClusterIP：30404 是按 `arko-chat-agent` 的接入方式定的。

## 合并关系

| 方案项 | 报告编号 | 严重度（报告） | 实际改什么 |
|---|---|---|---|
| 1. HTTP 鉴权 | 001、007、008 | 高危、中危、中危 | 同一件事：谁都能 `POST /` 发信。007 建议改回 ClusterIP，本方案不采纳 |
| 2. 日志不可伪造 | 002、004、005、006、015 | 低危×4、中危×1 | 用户输入里的换行和控制字符原样进日志 |
| 3. 限制单次输入 | 003 的超长邮箱部分、012 | 低危、中危 | `email` / `question` 没有长度上限 |
| 4. 限制并发和日志体积 | 003、009 | 低危、中危 | 每次调用新建关闭线程；日志文件没有大小上限 |
| 5. 收紧 HTML | 010、014 | 中危、中危 | 标签正则可被构造出长时间匹配；调用方 HTML 原样进邮件 |
| 6. SMTP 校验证书 | 013 | 中危 | SSL 分支没有传入校验上下文，STARTTLS 分支已经有 |
| 7. 错误回包不带内部细节 | 011 | 中危 | 失败原因和收件地址直接返回给调用方 |

## 1. HTTP 鉴权

`streamable-http` 启动时没有校验调用方。NodePort `30404` 在节点上监听，能访问该端口的人可以调用 `send_issue_email`，用配置里的 SMTP 账号往 `TARGET_EMAIL` 发信。

改 `server.py`：

- 增加 Bearer 中间件，放在现有 Accept 兼容中间件之前。
- `GET /health` 不校验，探针继续可用。
- 其它 HTTP 请求必须带 `Authorization: Bearer <MCP_AUTH_TOKEN>`。用 `hmac.compare_digest` 比较。
- `MCP_TRANSPORT` 不是 `stdio` 且 `MCP_AUTH_TOKEN` 为空时，进程拒绝启动。避免再以无鉴权方式对外跑。
- stdio 模式不读这个令牌，本机拉起不受影响。

配置：

- `.env.example`、`deploy/k8s/secret.example.yaml` 增加 `MCP_AUTH_TOKEN`。示例文件只放占位符。
- `docs/OPS.md` 写明：令牌放 Secret，和 `arko-chat-agent` 后台的 MCP Token 使用同一个值。该后台已支持 Token 非空时带 Bearer。
- 不删除 `nodePort: 30404`。鉴权上线后，知道地址仍不够发信。

## 2. 日志不可伪造

`send_email` 在校验邮箱之前就把原始 `email` 写入日志。中间件把 ASGI 的 `path` 原样写入日志。`_preview` 只把 `\n` 换成可见形式，`\r` 和 ESC 仍能拆行或影响终端显示。

改一处，所有落日志的外部字符串都走它：

- `_preview` 把 C0/C1 控制字符（含 `\r`、`\n`、`\t`、`\x00`、`\x1b`）转成 `\xNN`，再按现有 200 字截断。
- `email`、校验失败时的邮箱、邮件主题、工具返回值、HTTP 的 method/path 都经过 `_preview`。

不为此新增第二套转义函数。

## 3. 限制单次输入

- 邮箱超过 254 个字符直接返回 `错误：`，不进入 SMTP，也不把全文写入日志。
- `question` 超过 10000 个字符同样直接返回 `错误：`。客服工单正文足够，挡掉用超大 JSON 撑内存的请求。
- 返回文案仍以 `错误：` 开头，`arko-chat-agent` 现有的失败判断不用改。

## 4. 限制并发和日志体积

- 发信并发默认最多 4 路。超出返回 `错误：`，不新建 SMTP 连接。
- 同一进程默认 60 秒内最多接受 10 次真正开始发信的调用。超出同样返回 `错误：`。
- 关闭 SMTP 不再每次 `Thread.start()`。一个后台线程从有界队列里取连接并 `quit`。队列满了就在当前调用里直接 `close`，避免线程数跟着请求涨。
- 单条日志写入超过 8KiB 就截断。当天日志文件达到 64MiB 后，新记录丢掉并在进程日志里记一次告警。已有的 30 天删除保留。

数字放常量，必要时再用环境变量覆盖。第一版用常量即可。

## 5. 收紧 HTML

保留现在的 HTML 邮件。客服侧已经按 HTML 段落投递，标签不能整段丢掉。

- `_HTML_TAG_RE` 改为标签体内不允许再出现 `<`。现在的 `[^>]*` 在一长串没有 `>` 的 `<a<a<...` 上会反复扫描。合法的 `<p>`、`<br>`、`<strong>` 仍能识别。
- 拼进 `text/html` 之前做白名单：只留 `p`、`br`、`strong`、`b`、`ul`、`ol`、`li`。去掉全部属性。`script`、`iframe`、`img`、`a`、`style` 整段不进入邮件。文本节点做 `html.escape`。
- 这样段落和加粗还在，链接、图片追踪和脚本不会随企业发件账号送进收件箱。纯文本兜底逻辑不变。

## 6. SMTP 校验证书

`_open_smtp` 的 SSL 分支是 `smtplib.SMTP_SSL(host, port, timeout=15)`，没有 `context`。CPython 在未传入 context 时不校验服务端证书。STARTTLS 分支已经使用 `ssl.create_default_context()`。

SSL 分支改为同样传入 `ssl.create_default_context()`。证书主机名不匹配时走现有异常分支，不把账号密码发到未校验的连接上。`smtp.larksuite.com:465` 使用正规证书，正常登录不受影响。

## 7. 错误回包不带内部细节

调用方仍要能区分成功和失败，所以前缀保留。

- 成功：`成功：问题已发送。` 不再带 `TARGET_EMAIL`。
- SMTP、网络、证书失败：`错误：邮件发送失败。` 不再拼接异常原文。`log.exception` 继续把原因写入服务日志。
- 配置缺失：`错误：邮件服务未正确配置。` 不把缺哪一项回给调用方。

## 不做的事

- 不把 Service 改回 ClusterIP，不删 `nodePort: 30404`。
- 不在本服务里做来源 IP 白名单。调用方是集群内的 `arko-chat-agent`，也可能经节点端口访问；鉴权用令牌，不用 IP。
- 不关闭 HTML 邮件，只收紧允许的标签。

## 改动文件

| 文件 | 改动 |
|---|---|
| `server.py` | 鉴权中间件、日志转义、长度与并发限制、HTML 白名单、SMTP context、收紧返回文案 |
| `.env.example` | `MCP_AUTH_TOKEN` |
| `deploy/k8s/secret.example.yaml` | `MCP_AUTH_TOKEN` 占位 |
| `docs/OPS.md` | 令牌必填、健康检查仍匿名、NodePort 仍保留 |

`deploy/k8s/mcp.yaml` 的端口和命名空间不动。

## 上线顺序

1. 先生成一长串随机 `MCP_AUTH_TOKEN`，写入 Secret，并填进 `arko-chat-agent` 的 MCP Token。两边同时生效后再切流量。只改本服务、不改后台时，客服发信会全部 401。
2. 部署后用错误令牌访问 `POST /`，应得到 401，且日志里没有 SMTP 登录。
3. 用正确令牌调用 `send_issue_email`，确认飞书仍能收到信，正文段落和加粗还在，邮件里没有可点击的外链。
4. `GET /health` 不带令牌仍返回 `{"status":"ok"}`。

## 验收

| 项 | 怎么确认 |
|---|---|
| 鉴权 | 无令牌或错误令牌的 `tools/call` 返回 401，不发信 |
| 证书 | 连 `smtp.larksuite.com:465` 登录成功；把主机改成证书不匹配的地址时失败且日志有 `SSLError` |
| 日志 | `email` 或路径里带 `%0a`、`\r` 时，当天日志文件仍是一行一条，不出现伪造的 `SMTP send_message 成功` |
| 长度 | 邮箱 255 字、问题 10001 字都返回 `错误：`，进程内存不明显上涨 |
| HTML | `<p>`、`<strong>` 仍渲染；`<a href>`、`<img>`、`<script>` 不以标签形式出现在邮件里 |
| 回包 | 故意配错密码时，调用方只看到 `错误：邮件发送失败。`，服务日志里仍有异常栈 |
