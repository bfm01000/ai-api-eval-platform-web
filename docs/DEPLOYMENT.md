# v0.1 公共版部署

公共版仅测试访问者自己的 Key，支持 OpenAI-compatible 普通 / Streaming 请求，每轮 1–3 个样本。
结果在请求内存中聚合，只保留在当前页面，可导出报告；不开放公共历史查询，不保存公共 Key、Endpoint 或结果。
本机中转 Key 仍仅用于本地模式。

## 环境要求

- 可直接访问目标服务商的 Linux 服务器，建议至少 1 核 / 1 GB 内存。
- Docker Engine 和 Docker Compose v2。
- 已解析到服务器的域名，放行 TCP 80 / 443。Caddy 自动申请和续期 HTTPS 证书。
- 使用独立、干净的源码目录，不上传本机 `.env`、`api-key`、Provider 配置或数据库。

`.dockerignore` 使用允许列表，Dockerfile 仅复制源码、契约和页面。API 容器采用非 root 用户、只读文件系统，不暴露主机 8000 端口。

## 首次启动

在仓库根目录创建部署参数文件：

```bash
cp deploy.env.example deploy.env
```

填写 `SITE_DOMAIN`（仅域名，不含协议或路径）、`ACME_EMAIL`（证书联系邮箱）和 `RELEASE_TAG`。
此文件已被 Git 忽略，不应包含 Provider Key。

```bash
docker compose --env-file deploy.env config --quiet
docker compose --env-file deploy.env run --rm --no-deps web caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose --env-file deploy.env build --pull
docker compose --env-file deploy.env up -d
docker compose --env-file deploy.env ps
```

Compose 强制使用 `API_OBSERVATORY_PUBLIC_MODE=true`，不要改为 false。
首次签发证书要求 DNS 正确且服务器 80 / 443 端口可从互联网访问。

## 发布验收

将下面地址换成真实域名：

```bash
python3 scripts/smoke_public.py https://eval.example.com
```

脚本检查健康、公共模式、私有接口关闭、IPv6 回环拒绝和错误脱敏，不调用真实 Provider，但消耗 2 次公共请求配额。
然后使用可撤销、低额度的自有 Key，分别执行一次普通和 Streaming 测试，检查结果、Key 清空、刷新无历史及导出无 Key。
GitHub Actions 已配置后端检查、Docker 构建、Caddy 校验和只读容器冒烟测试；正式发布须以 CI 和目标服务器 HTTPS 验收通过为准。

## 默认限制

| 项目 | 限制 |
| --- | --- |
| 每轮样本 | 1–3 |
| 单 IP 频率 / 日额度 | 5 轮每分钟 / 30 轮每 24 小时窗口 |
| 全站日额度 | 1000 轮每 24 小时窗口 |
| 并发公共请求 | 4 |
| 入站 JSON / 接收时限 | 16 KiB / 10 秒 |
| 每个上游样本总时限 | 20 秒，包含 DNS、连接、响应读取 |
| 整轮执行时限 | 75 秒 |
| 每个上游响应 | 1 MiB，拒绝压缩响应 |
| 目标地址 | HTTPS 443，无 URL 凭证、查询串、fragment |

DNS 在实际 TCP 连接时解析，所有返回地址必须为公网地址；连接固定到已校验 IP，原域名用于 Host、TLS SNI 和证书校验。
不跟随重定向，不使用环境代理，拒绝内网、回环、保留 IP 以及 IPv6 映射 / 转换地址。
使用 Fake-IP DNS 的本机代理可能被拒绝，这是预期行为；生产服务器应使用真实 DNS 和直接出站网络，不可通过放开内网地址来绕过。

限流是单进程内存状态，重启会清零。首版必须保持 **1 个 worker、1 个 API 实例**；多实例前须增加共享限流存储。
IP 配额记录只存内存、数量有上限，不落库。不合法的请求也计入配额。
调整额度时修改 Compose 的 `API_OBSERVATORY_PUBLIC_*` 环境变量并重建服务。

## 代理边界

只有固定地址 `172.30.0.2` 的 Caddy 可传递客户端 IP，Caddy 会覆盖传入的 X-Forwarded-For。
不要暴露 API 主机端口，也不要把 `--forwarded-allow-ips` 改为 `*`。
若 `172.30.0.0/29` 与服务器现有网段冲突，须同步修改网段、两个服务地址和受信代理地址。
若另加 CDN，须先配置清楚受信代理链，否则限流会按 CDN 出口 IP 合并计算。

## 运维与回滚

```bash
docker compose --env-file deploy.env logs --tail 100 api web
docker compose --env-file deploy.env restart api
```

容器退出后自动重启；健康检查失败标记 unhealthy，需监控 `/health` 并处理告警。日志按大小轮转，未启用 HTTP 访问日志。
不要启用请求体、Authorization 或错误原始输入采集。公共版不使用持久化业务数据库。
备份保存证书的 `caddy_data` 卷，避免用 `down -v` 删除证书。

每次发布使用独立 `RELEASE_TAG`，保留旧镜像。回滚时将 `deploy.env` 中标签改回上一版，执行：

```bash
docker compose --env-file deploy.env up -d --no-build
python3 scripts/smoke_public.py https://eval.example.com
```

## 本地模式

本机 `backend/.env` 可设置 `API_OBSERVATORY_PUBLIC_MODE=false` 并配置中转 Key：

```bash
cd backend
uv sync --frozen --all-groups
uv run uvicorn api_observatory.app:app --host 127.0.0.1 --port 8000 --no-proxy-headers
```

只通过 localhost 访问，不通过反向代理公开本地模式。本地接口检查 loopback 客户端和 localhost Host。
私有模式 SQLite 数据位于 `backend/data/`，停服后可完整备份该目录。

## 本次验证边界

2026-10-17：本地后端安全回归、浏览器流程已验证；依赖审计未发现已知漏洞。
本机没有 Docker Engine，容器构建、Caddy 校验与真实域名证书签发需在 CI / 目标服务器执行。
本机外网 DNS 返回了被公网策略拒绝的地址，本轮未使用真实服务商 Key；真实 Provider 验收需在服务器正常出站网络下完成。
