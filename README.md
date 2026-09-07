# AI API 评测平台

面向 AI API 与中转服务的质量检测、能力评测和选择决策平台。

当前仓库已完成 **Phase 0：项目基础设施与架构**，正在推进 **Phase 1：单 Provider Performance MVP**。根目录的静态页面是产品界面原型，数据均为演示数据，不代表真实 API 检测结果。

## 查看原型

直接打开 `index.html`，或在本目录启动任意静态文件服务器。

## 项目文档

- `docs/PROJECT.md`：长期产品定义与架构边界
- `docs/PLAN.md`：路线图、Phase 1 拆解与完成标准
- `docs/PROGRESS.md`：当前真实进度和唯一下一任务
- `docs/DECISIONS.md`：架构决策记录
- `docs/EXPERIMENTS.md`：Benchmark / Fingerprint 实验记录
- `docs/PROVIDER_CONFIG.md`：Provider 配置、凭证边界与日志脱敏规则

## 数据契约

Phase 1 的单次性能检测请求与原始结果契约位于 `contracts/`，计时和错误语义见 `docs/PERFORMANCE_CONTRACT.md`。

验证契约与样例：

```bash
python3 scripts/verify_contract.py
```

## 后端

后端使用 Python 3.12、FastAPI 和 HTTPX，由 `uv` 管理隔离环境与锁文件：

```bash
cd backend
uv sync --all-groups
uv run pytest -W error
uv run ruff check .
```

当前提供 `GET /health`、契约验证、Provider 配置 / 密钥脱敏，以及非流式 OpenAI-compatible Adapter。Adapter 已通过真实 loopback HTTP 集成测试，但尚未通过外部 Provider 烟雾测试。
