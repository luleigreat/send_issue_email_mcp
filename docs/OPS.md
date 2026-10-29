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

8765 只在集群内部。不要给这个服务做公网 Ingress。进程没有登录，也没有 Bearer 校验，知道地址就能发信。

`arko-chat-agent` 和本服务在同一集群时，后台「MCP」填：

```text
http://send-issue-email.send-issue-email.svc.cluster.local:8765/
```

末尾的 `/` 要留着。工具名填 `send_issue_email`。本服务不校验 Token，后台的 MCP Token 留空。

出网要能连配置里的 SMTP。Gmail 是 `smtp.gmail.com:465`。

## 安全

容器端口不是公网端口。外网不应该直接打到 8765。`arko-chat-agent` 从集群内部调用。

密钥不要打进镜像。`SMTP_USERNAME` 和 `SMTP_PASSWORD` 只放在 `send-issue-email-secret`。Gmail 填应用专用密码，不是登录密码。

## 配置

本地 `.env` 的键与集群配置的对应关系如下。

| `.env` | 放到 | 说明 |
|---|---|---|
| `SMTP_USERNAME` | `send-issue-email-secret` | 登录 SMTP 的账号 |
| `SMTP_PASSWORD` | `send-issue-email-secret` | SMTP 密码或 Gmail 应用专用密码 |
| `TARGET_EMAIL` | `send-issue-email-config` | 固定收件邮箱。调用方改不了 |
| `SMTP_HOST` | `send-issue-email-config` | 例如 `smtp.gmail.com` |
| `SMTP_PORT` | `send-issue-email-config` | SSL 用 `465` |
| `SMTP_USE_SSL` | `send-issue-email-config` | `true` 为 SSL（465）；`false` 为 STARTTLS（587） |
| `SENDER_EMAIL` | `send-issue-email-config` | 发件人，通常等于 `SMTP_USERNAME` |
| `SENDER_NAME` | `send-issue-email-config` | 发件显示名 |
| `MCP_TRANSPORT` | `send-issue-email-config` | 保持 `streamable-http` |
| `MCP_HOST` | `send-issue-email-config` | 保持 `0.0.0.0` |
| `MCP_PORT` | `send-issue-email-config` | 保持 `8765` |
| `MCP_PATH` | `send-issue-email-config` | 保持 `/`。改掉后 `arko-chat-agent` 的 `POST` 会对不上 |

改配置：

```bash
kubectl -n send-issue-email edit configmap send-issue-email-config
kubectl -n send-issue-email edit secret send-issue-email-secret
kubectl -n send-issue-email rollout restart deployment/send-issue-email
```

Secret 和 ConfigMap 都要重启后才进进程。

## 第一次部署

1. 按上面的表填 `deploy/k8s/secret.example.yaml`，另存为 `deploy/k8s/secret.yaml`。这个文件不要提交。
2. 把 `deploy/k8s/configmap.yaml` 里的 `TARGET_EMAIL`、`SENDER_EMAIL` 和 SMTP 主机改成真实值。
3. 把 `deploy/k8s/kustomization.yaml` 里的 `newTag` 改成刚才构建的 `TAG`。镜像在 ECR 时，给这一项加上 `newName`，例如 `ACCOUNT.dkr.ecr.REGION.amazonaws.com/send-issue-email`。
4. 执行：

```bash
kubectl apply -f deploy/k8s/secret.yaml
kubectl apply -k deploy/k8s
```

看进程：

```bash
kubectl -n send-issue-email get pods
kubectl -n send-issue-email logs deploy/send-issue-email
kubectl -n send-issue-email exec deploy/send-issue-email -- python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8765/health').read().decode())"
```

`/health` 返回 `{"status":"ok"}` 只说明进程活着，不说明 SMTP 能登录。

然后在 `arko-chat-agent` 管理端把 MCP 地址改成上一节那个集群地址，工具名 `send_issue_email`，Token 留空。用调试对话留一个邮箱，确认本服务日志里出现 `SMTP send_message 成功`。

## 日常动作

发布新版本：构建新 `TAG`，改 `kustomization.yaml` 的 `newTag`，再 `kubectl apply -k deploy/k8s`。

看日志。标准输出是 INFO。按天的明细在容器 `/app/log/YYYY-MM-DD.log`，启动时会删掉超过 30 天的文件。

```bash
kubectl -n send-issue-email logs -f deploy/send-issue-email
kubectl -n send-issue-email exec deploy/send-issue-email -- ls /app/log
```

不要把副本调到 1 以上。日志盘是 ReadWriteOnce，默认 1Gi，用集群的默认 StorageClass。要指定存储类时，在 PVC 上加 `storageClassName`。

删掉这块 PVC 会丢掉还没过期的日志，不会丢掉已经发出去的邮件。邮件在收件邮箱里。

一次发信含 SMTP 往返，常见要数秒到十几秒。`arko-chat-agent` 等待 MCP 的时间要盖住这段，不要只留几秒。
