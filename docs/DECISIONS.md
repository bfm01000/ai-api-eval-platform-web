# Architectural Decision Records

## ADR-001：初始化阶段使用零依赖静态前端

### Context

当前工作区没有 Node.js / npm，项目还未选择后端技术栈，但需要尽快验证产品信息架构和核心工作流。

### Options

**方案 A：等待完整框架选型后再开发界面**

优点：技术栈统一。缺点：产品反馈延后，且容易在需求未校准前投入框架工程。

**方案 B：先建立 HTML、CSS、JavaScript 静态原型**

优点：无安装成本，可直接运行，便于验证信息密度、术语和交互。缺点：后续需要迁移到正式前端工程。

### Decision

采用方案 B。静态页面只承担交互原型职责，所有指标明确标注为演示数据，不声称已具备真实检测能力。

### Why

它以最低成本验证产品工作流，同时不提前锁定后端与前端框架。

### Consequences

- 原型可直接打开和验收。
- 正式实现时复用信息架构与视觉规范，不把当前 DOM 结构视为长期 API。
- 真实检测能力必须由 Phase 1 后端垂直链路提供。

## ADR-002：性能、Benchmark、指纹研究与评分分离

### Context

四类能力的数据条件、执行成本和结论强度不同。混合实现会让性能数据、能力判断和模型身份猜测彼此污染。

### Options

**方案 A：统一测试任务和评分管线**

优点：早期代码少。缺点：边界模糊，实验难复现，指标语义容易失真。

**方案 B：共享任务与原始存储基础设施，领域逻辑分离**

优点：各自可验证、可演进，结论可追溯。缺点：需要明确契约。

### Decision

采用方案 B。共享 Provider Adapter、任务元数据和 Raw Storage；分别实现 Metrics、Benchmark、Fingerprint Research 与 Scoring。

### Why

性能是直接测量，Benchmark 是有标准答案或评分器的能力测试，指纹是需要对照组的实验研究，评分则是派生决策层，四者证据等级不同。

### Consequences

- Phase 1 只实现性能垂直链路。
- 指纹特征进入产品前必须记录实验并通过四项验证。
- 评分算法变化不要求重新调用 Provider。

## ADR-003：原始结果是事实源

### Context

指标口径和评分算法会变化，只保存聚合分数会失去审计与重算能力。

### Decision

持久化请求参数、原始响应、Provider / 模型、时间点、Token Usage、错误和执行版本；指标与评分均作为派生数据。

### Consequences

- 必须设计敏感字段脱敏、数据保留周期和访问控制。
- 指标算法升级后可重算历史结果。
- 存储成本会增加，需要在规模化阶段制定分层保留策略。

## ADR-004：墙钟用于关联，单调时钟用于延迟

### Context

性能记录既要能跨服务按时间关联，也要避免 NTP 校时、系统时间回拨等因素破坏毫秒级延迟测量。

### Options

**方案 A：所有时间都使用 UTC 墙钟**

优点：容易记录和阅读。缺点：墙钟可能跳变，两个时间戳相减不适合作为可靠延迟。

**方案 B：同时记录 UTC 墙钟和单调时钟累计偏移**

优点：兼顾审计关联与稳定计时。缺点：实现时必须保证所有偏移来自同一个起点和同一个进程。

### Decision

采用方案 B。`execution.started_at/completed_at` 使用 UTC RFC 3339；`timing.*_ms` 使用同一次执行内的单调时钟，记录从请求开始到各事件的累计偏移。

### Why

墙钟回答“何时发生”，单调时钟回答“耗时多久”，两类语义不应混用。

### Consequences

- 总延迟直接取 `response_completed_ms`，不由两个 UTC 时间戳相减。
- DNS / Connect / TLS 在连接复用或客户端未暴露时必须为 `null`，不能伪造为 0。
- Streaming TTFT 只认首个完成协议解码的非空文本内容，不认角色、空白、usage 或 `[DONE]` 事件。

## ADR-005：后端使用 Python 3.12、FastAPI、HTTPX 与 uv

### Context

项目需要异步 HTTP / Streaming、严格数据校验、可测试 API，并在后期支持 Benchmark、统计分析和模型行为研究。机器仅有已停止安全支持的系统 Python 3.9.6，但已安装 Homebrew。

### Options

**方案 A：Go 后端，Python 仅用于研究**

优点：并发和部署表现稳定。缺点：早期需要维护跨语言数据模型，Benchmark 与研究工具链割裂，且本机没有 Go。

**方案 B：Python 3.12 标准库后端**

优点：依赖最少。缺点：需要自行维护路由、OpenAPI、请求校验和测试基础设施，不利于小步验证。

**方案 C：Python 3.12 + FastAPI + HTTPX + Pydantic**

优点：异步 Streaming 支持成熟、契约与 OpenAPI 清晰，并能直接复用 Python 评测生态。缺点：依赖更多，CPU 密集型任务后期需要独立 Worker 或进程隔离。

### Decision

采用方案 C。使用 `uv` 固定 Python 3.12、隔离虚拟环境并生成 `uv.lock`；FastAPI 承担 API 边界，HTTPX 用于 Provider HTTP 调用，Pydantic 管理配置和运行时数据校验，pytest / Ruff 作为基础质量门禁。Phase 1 本地持久化优先使用 Python 标准库 SQLite，出现真实并发和查询压力后再评估 PostgreSQL。

### Why

它能以较低工程成本建立可验证的垂直链路，同时与后续 Benchmark、数据分析和指纹实验保持同一语言生态。Python 3.12 仍处于官方支持周期，避免把新项目建立在 EOL 的系统 Python 3.9 上。

### Consequences

- 所有后端命令通过 `uv run` 执行，不依赖系统 Python 环境。
- 依赖升级必须更新并验证 `uv.lock`。
- API、Worker、Provider Adapter 先在同一 Python 工程中保持模块边界；只有真实负载证明需要时才拆服务。
- SQLite 是 Phase 1 的简单持久化选择，不代表最终生产数据库决策。
- 性能结论基于外部 API 网络耗时；框架自身开销仍需通过测试监控，但当前不做无依据优化。

## ADR-006：Provider 元数据与凭证值分离

### Context

性能任务需要持久化 Provider、Endpoint、模型与 Adapter 配置，但 API Key 不应进入可提交配置、数据契约、对象序列化、异常或日志。

### Options

**方案 A：在 Provider TOML 中直接保存 API Key**

优点：读取简单。缺点：极易误提交或随配置、错误和调试输出泄露，不可接受。

**方案 B：TOML 保存凭证引用，运行时从环境变量解析**

优点：元数据可审计，凭证不进入仓库；适合 Phase 1 本地与单 Worker 运行。缺点：环境变量管理不适合大规模多租户密钥治理。

**方案 C：立即接入外部 Secret Manager**

优点：权限、轮换和审计能力更强。缺点：在单 Provider MVP 阶段增加部署和供应商依赖。

### Decision

Phase 1 采用方案 B。TOML 只允许 `env:VARIABLE_NAME`，解析后密钥只存在于 `SecretStr`。结构化数据、文本和最终日志格式统一使用脱敏模块；配置验证错误只输出字段位置，不回显输入值。

### Why

它满足当前安全边界且保持实现简单。契约已为未来 `keychain:`、`vault:` 等引用保留版本演进空间，但未实现的引用不能被配置加载器接受。

### Consequences

- `backend/config/providers.toml` 和 `.env` 被 Git 忽略，示例文件不包含密钥值。
- 缺少凭证时可以报告 Provider ID 和环境变量名称，但不得输出环境变量集合。
- HTTP Adapter 不得接收普通字符串形式的持久化密钥；需要鉴权头时在请求边界临时读取 `SecretStr`。
- 脱敏是纵深防御，调用代码仍应避免记录鉴权头和完整环境。
- 多 Worker 或多租户阶段引入 Secret Manager 时必须新增 ADR，不能静默改变引用语义。

## ADR-007：公共自测使用临时结果与受限出站连接

### Context

本地中转 Key 只允许负责人在本机使用；公众只能提交自己的 Key。公开历史接口、任意 Endpoint 和默认校验错误均不满足这一边界。

### Decision

公共自测结果只在单个请求内存和当前浏览器页面保留，关闭公共历史查询。保留本地 SQLite 原始结果，不将访客结果与本地库混存。公共页面允许用户自行导出本页报告。

由 HTTPcore 的网络后端在实际 TCP 连接前检查全部 DNS 地址，固定已验证公网 IP，保留原 Host、TLS SNI 与证书验证。公共目标限定 HTTPS 443，拒绝重定向及压缩响应；统一丢弃校验错误的原始输入。

### Why

无需先引入账号体系就能避免访客之间读取测试结果。校验与连接使用同一 IP，消除二次 DNS 解析产生的绕过窗口；IPv6 映射和转换地址一并拒绝。

### Consequences

- 公共请求默认限制为每轮 3 个样本、单样本 20 秒、整轮 75 秒及单响应 1 MiB；另有 IP 和全站配额、并发与入站大小限制。
- 首版限流依赖单 worker / 单实例内存状态，重启清零；扩容前必须改为共享限流。
- 公共自测不持久化是原始数据保存原则的明确例外；本地研究和私有任务仍保存原始结果。
- Fake-IP DNS、非 443 端口及必须压缩响应的服务商不支持公共自测，不能为兼容性放开内网连接。
- Docker 镜像通过允许列表排除本地 Key，生产 Compose 固定公共模式。上线验收与回滚流程见 DEPLOYMENT.md。
