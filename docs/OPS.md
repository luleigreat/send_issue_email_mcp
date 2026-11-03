# 运维

一个镜像，一个进程。`arko-chat-agent` 在后台填写本服务的地址，访客留邮箱后由它调用工具 `send_issue_email`。本服务自己连 SMTP，收件人固定，不接受调用方传来的收件人。

副本数保持 1。日志在一块只能挂给一个 Pod 的盘上，多副本会抢这块盘。

## 镜像

| 镜像 | 进程 | 容器端口 | 做什么 |
|---|---|---|---|
| `send-issue-email` | `python server.py` | 8765 | MCP（`POST /`）和健康检查（`GET /health`） |

构建在仓库根目录执行。`TAG` 默认是当前 git 短提交号。推到 ECR 前先登录。

```bash
aws ecr get-login-password --region REGION | docker login --username AWS --password-stdin ACCOUNT.dkr.ecr.REGION.amazonaws.com
REGISTRY=ACCOUNT.dkr.ecr.REGION.amazonaws.com TAG=$(git rev-parse --short HEAD) PUSH=1 ./deploy/build-images.sh
```

只在本机构建、不推送时，去掉 `REGISTRY` 和 `PUSH=1`。

ECR 里需要一个仓库：`send-issue-email`。

## 对外入口

Service 是 NodePort，节点端口 `30404`，容器端口仍是 `8765`。不要再给这个服务做公网 Ingress。`POST /` 必须带 `Authorization: Bearer <MCP_AUTH_TOKEN>`，令牌和 `arko-chat-agent` 后台的 MCP Token 用同一个值。`GET /health` 不校验，探针不用带令牌。

工作负载在命名空间 `arko`。`arko-chat-agent` 和本服务在同一集群时，后台「MCP」填：

```text
http://send-issue-email.arko.svc.cluster.local:8765/
```

末尾的 `/` 要留着。工具名填 `send_issue_email`。后台的 MCP Token 填和 Secret 里 `MCP_AUTH_TOKEN` 相同的值。留空时客服发信会全部 401。

出网要能连 `smtp.larksuite.com:465`（SSL）。

## 安全

容器端口不是公网端口。外网不应该直接打到 8765。`arko-chat-agent` 从集群内部调用。

密钥不要打进镜像。`SMTP_USERNAME`、`SMTP_PASSWORD` 和 `MCP_AUTH_TOKEN` 只放在 `arko` 命名空间的 `send-issue-email-secret`。SMTP 密码是飞书邮箱的 SMTP 密码。HTTP 模式下令牌为空时进程拒绝启动。stdio 模式不读这个令牌。

## 配置

本地 `.env` 的键与集群配置的对应关系如下。非密钥的当前值在 `deploy/k8s/configmap.yaml`。

| `.env` | 放到 | 当前值 |
|---|---|---|
| `SMTP_USERNAME` | `send-issue-email-secret` | `agent@arko.global` |
| `SMTP_PASSWORD` | `send-issue-email-secret` | 飞书 SMTP 密码，只放 Secret |
| `MCP_AUTH_TOKEN` | `send-issue-email-secret` | 一长串随机值。与后台 MCP Token 相同。HTTP 模式为空时进程拒绝启动 |
| `TARGET_EMAIL` | `send-issue-email-config` | `services@arko.global`。调用方改不了 |
| `SMTP_HOST` | `send-issue-email-config` | `smtp.larksuite.com` |
| `SMTP_PORT` | `send-issue-email-config` | `465` |
| `SMTP_USE_SSL` | `send-issue-email-config` | `true`（465 SSL）。`false` 为 STARTTLS（587） |
| `SENDER_EMAIL` | `send-issue-email-config` | `agent@arko.global` |
| `SENDER_NAME` | `send-issue-email-config` | `ARKO Chat Agent` |
| `MCP_TRANSPORT` | `send-issue-email-config` | `streamable-http` |
| `MCP_HOST` | `send-issue-email-config` | `0.0.0.0` |
| `MCP_PORT` | `send-issue-email-config` | `8765` |
| `MCP_PATH` | `send-issue-email-config` | `/`。改掉后 `arko-chat-agent` 的 `POST` 会对不上 |

Secret 在命名空间 `arko`，至少包含这三项：

```yaml
stringData:
  SMTP_USERNAME: agent@arko.global
  SMTP_PASSWORD: <飞书 SMTP 密码>
  MCP_AUTH_TOKEN: <与后台 MCP Token 相同>
```

`deploy/k8s/kustomization.yaml` 里的 `namespace` 仍是 `send-issue-email`。执行 `kubectl apply -k` 前改成 `arko`，否则 Kustomize 会把 ConfigMap 和 Deployment 改到 `send-issue-email`。

改配置：

```bash
kubectl -n arko edit configmap send-issue-email-config
kubectl -n arko edit secret send-issue-email-secret
kubectl -n arko rollout restart deployment/send-issue-email
```

Secret 和 ConfigMap 都要重启后才进进程。

## 第一次部署

1. 按上面的表填 `deploy/k8s/secret.example.yaml`，另存为 `deploy/k8s/secret.yaml`。命名空间用 `arko`。`MCP_AUTH_TOKEN` 换成随机值，并填进 `arko-chat-agent` 后台的 MCP Token。这个文件不要提交。
2. `deploy/k8s/configmap.yaml` 已是当前飞书发信配置。要改收件人或发件人时改这一份。
3. 把 `deploy/k8s/kustomization.yaml` 里的 `newTag` 改成刚才构建的 `TAG`。镜像在 ECR 时，给这一项加上 `newName`，例如 `ACCOUNT.dkr.ecr.REGION.amazonaws.com/send-issue-email`。
4. 执行：

```bash
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -k deploy/k8s
```

看进程：

```bash
kubectl -n arko get pods
kubectl -n arko logs deploy/send-issue-email
kubectl -n arko exec deploy/send-issue-email -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/health').read().decode())"
```

`/health` 返回 `{"status":"ok"}` 只说明进程活着，不说明 SMTP 能登录。

然后在 `arko-chat-agent` 管理端把 MCP 地址改成上一节那个集群地址，工具名 `send_issue_email`，Token 填和 Secret 里 `MCP_AUTH_TOKEN` 相同的值。用调试对话留一个邮箱，确认本服务日志里出现 `SMTP send_message 成功`。不带令牌访问 `GET /health` 仍应返回 `{"status":"ok"}`。

## 日常动作

发布新版本：构建新 `TAG`，改 `kustomization.yaml` 的 `newTag`，再 `kubectl apply -k deploy/k8s`。

看日志。标准输出是 INFO。按天的明细在容器 `/app/log/YYYY-MM-DD.log`，启动时会删掉超过 30 天的文件。

```bash
kubectl -n arko logs -f deploy/send-issue-email
kubectl -n arko exec deploy/send-issue-email -- ls /app/log
```

不要把副本调到 1 以上。日志盘是 ReadWriteOnce 的 10Gi EFS（`storageClassName: efs-sc`，PV `send-issue-email-logs-pv`）。

删掉这块 PVC 会丢掉还没过期的日志，不会丢掉已经发出去的邮件。邮件在收件邮箱里。

一次发信含 SMTP 往返，常见要数秒到十几秒。`arko-chat-agent` 等待 MCP 的时间要盖住这段，不要只留几秒。
