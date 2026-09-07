# Current Status

Current Phase: Phase 1 - 单 Provider Performance MVP  
Current Task: P1-T04 - 请求开始、响应结束与总延迟  
Status: Completed

# Completed

- 检查工作区、依赖和现有目录；确认项目为新建目录且没有 Git 仓库
- 建立 PROJECT、PLAN、PROGRESS、DECISIONS、EXPERIMENTS 文档
- 明确系统总体架构、模块职责、非目标和 Raw Data 原则
- 拆解 Phase 1 的 12 个独立任务并定义完成标准
- 建立零依赖静态仪表盘原型，演示数据与未开放模块均有明确标识
- 完成桌面 / 移动端视觉验收、核心交互验证和移动端溢出修复
- 初始化独立 Git 仓库并建立敏感运行数据忽略规则
- 定义 `performance-request.v1` 与 `performance-result.v1` JSON Schema
- 固定 TTFT、总延迟、Tokens/s 的计时和派生口径
- 定义 DNS、连接、TLS、超时、HTTP、Provider、协议、解析、内部和取消错误分类
- 增加 4 个有效样例、2 个无效样例、3 个运行时变异用例及零依赖契约验证器
- 选择 Python 3.12 + FastAPI + HTTPX + Pydantic 后端栈，并用 uv 锁定 42 个包
- 初始化 `backend/` src-layout 工程、`GET /health`、OpenAPI 和代码质量配置
- 接入完整 JSON Schema Draft 2020-12 验证，建立 5 个后端自动测试
- 完成真实 Uvicorn 启动、HTTP 健康检查和正常关闭验证
- 实现版本化 Provider TOML 加载、严格字段校验、唯一 ID 检查和环境设置读取
- 实现 `env:` 凭证引用解析，API Key 仅以 `SecretStr` 存在于运行内存
- 实现结构化值、自由文本、Bearer Token、日志参数和异常文本脱敏
- 增加 Provider 配置示例、安全边界文档和 8 个配置 / 脱敏测试
- 实现非流式 OpenAI-compatible Chat Completions 请求与 `stream=false` 参数映射
- 实现成功响应、Usage、原始正文、响应哈希、字节数和脱敏 Header 捕获
- 实现 timeout、connect、HTTP、Provider、parse 和 protocol 基础错误映射
- 限制生产 Endpoint 使用 HTTPS，并禁止 URL 凭证、查询参数和 fragment
- 通过真实 loopback TCP 服务验证完整 HTTP 请求，而非只使用进程内 Mock
- 对 Provider 回显的请求凭证执行保存前脱敏，并在契约中记录 `credential_redacted`
- 新增单调时钟请求计时器，UTC 墙钟只用于开始 / 完成审计时间
- 在普通请求成功及 timeout、connect、HTTP、Provider、parse、protocol 错误上统一挂载计时结果
- 将总延迟固定为请求开始到完整响应体返回或传输失败的 `response_completed_ms`
- 使用可控时钟证明 10 秒墙钟差不影响 250.25 ms 单调耗时，并拒绝负单调耗时

# In Progress

- 无

# Next

**P1-T05：实现流式请求和首个有效内容事件识别。**

# Blockers

- 无

# Known Issues

- 当前页面只使用演示数据，尚未连接真实 Provider
- “运行检测”仅模拟前端状态流转，不代表 Phase 1 已实现
- Benchmark、行为一致性与综合评分按路线图处于未开放状态
- 当前 Provider 配置仅支持 `env:` 凭证引用；Secret Manager 留待规模化阶段
- 非流式 Adapter 尚未使用用户的外部 Provider、模型和凭证完成烟雾测试
- `max_output_tokens -> max_tokens` 是当前通用映射；特定 Provider 方言尚未实现
- HTTPX 当前未提供可直接持久化的 DNS、Connect、TLS、响应头和首字节分段时间，不能从总延迟反推

# Last Verification

执行：`uv run pytest -W error`、`uv run ruff check .`、`uv run ruff format --check .`、`uv lock --check`、`uv run python ../scripts/verify_contract.py`，并解析全部契约 JSON。

结果：**PASS**。23 个测试零警告通过；真实 loopback HTTP 请求与计时验证成功；可控时钟和错误计时测试通过；Ruff 检查与格式检查通过；锁文件一致；v1 Schema 与全部契约样例通过。

## 2026-09-06 16:37

### Goal

完成 P1-T04：准确记录请求开始、响应结束和总延迟。

### Changes

新增可注入时钟、系统单调时钟、请求计时器和不可变计时结果；在 HTTPX 请求执行边界记录 UTC 开始 / 完成时间与单调耗时，并把计时结果统一附加到普通请求成功和全部类型化错误路径。总延迟保留三位小数，UTC 墙钟回拨不会制造逆序审计时间。

### Verification

运行 23 个后端测试并将 warning 视为 error；使用真实 loopback TCP 验证正数耗时和 UTC 时间顺序；用可控时钟证明 10 秒墙钟差不改变 250.25 ms 单调耗时；覆盖 connect、timeout、Provider、protocol、parse 错误计时和负单调耗时拒绝；执行 Ruff、格式、锁文件与完整契约验证。

### Result

PASS。成功和失败记录均有同口径的请求开始、响应完成和总延迟；总延迟不由 UTC 时间戳相减。外部 Provider 未测试，因此不声称真实供应商已通过计时验证。

### Discovery

当前运行环境的系统代理会转发 loopback HTTP 并返回 502；集成测试必须关闭环境代理继承才能稳定验证本地真实 TCP 服务。HTTP Header 大小写也不能作为协议断言依据。HTTPX 普通 `post()` 的结束点是完整响应体已读取，但不能据此伪造未暴露的网络阶段时间。

### Next

P1-T05：实现流式请求和首个有效内容事件识别。

## 2026-09-06 15:53

### Goal

完成 P1-T03：实现一次非流式 OpenAI-compatible 请求。

### Changes

新增 OpenAI-compatible Adapter、请求 / 响应模型、原始响应捕获和类型化错误；新增真实本地 HTTP 集成测试与 Adapter 语义文档；强化 Endpoint 安全校验。为防止 Provider 回显请求凭证，在进入结果前执行精确脱敏，并为原始结果契约新增 `credential_redacted`。

### Verification

运行 18 个后端测试并将 warning 视为 error；使用 ThreadingHTTPServer 在 loopback TCP 上验证真实 POST、Authorization、`stream=false`、参数映射、响应解析、Usage、Header / 正文脱敏、哈希与错误路径；执行 Ruff、格式、锁文件和完整契约验证。

### Result

PASS。成功、401 Provider 错误和 2xx 无效协议响应均按预期处理；密钥不出现在结果序列化或异常中。外部 Provider 未测试，因此不声称真实供应商已兼容。

### Discovery

“原始响应优先”不能覆盖凭证安全：上游可能回显 Authorization 值。契约现明确将凭证脱敏列为 Raw Data 的强制例外，同时分别记录接收字节数、保存文本哈希和是否发生凭证脱敏。

### Next

P1-T04：准确记录请求开始、响应结束和总延迟。

## 2026-09-05 21:19

### Goal

完成 P1-T02：实现 Provider 配置读取与密钥脱敏。

### Changes

新增 Provider TOML 示例、AppSettings、ProviderCatalog、环境凭证解析、SecretStr 运行时模型、结构化 / 文本脱敏和 RedactingFormatter；新增 ADR-006 与 Provider 配置文档。

### Verification

运行 13 个后端测试并将 warning 视为 error；执行 Ruff、格式、锁文件和领域契约检查。测试覆盖配置成功 / 失败、重复 ID、缺失环境变量、错误不回显、序列化、嵌套结构、Bearer Token、日志参数和异常脱敏。

### Result

PASS。全部测试和质量门禁通过，敏感输入未出现在配置错误、完整异常堆栈、模型表示、JSON 序列化或格式化日志中。

### Discovery

测试首次使用了错误的重复 Provider TOML 夹具，只触发语法错误；修正为合法 TOML 后确认唯一 ID 规则确实生效。配置验证异常链可能保留原始 Pydantic 输入，现已主动切断；日志脱敏必须在最终 Formatter 层执行，才能覆盖参数插值和异常堆栈。

### Next

P1-T03：实现一次非流式 OpenAI-compatible 请求。

## 2026-09-05 16:11

### Goal

完成 P0-T05：选择后端技术栈并初始化最小可测试工程。

### Changes

安装 uv；由 uv 配置隔离的 Python 3.12.14；新增 FastAPI 后端、健康检查、OpenAPI、完整 JSON Schema 测试、pytest / Ruff 配置与依赖锁；新增 ADR-005 记录选型。

### Verification

运行 5 个后端测试并将 warning 视为 error；执行 Ruff 检查和格式检查；校验依赖锁；启动真实 Uvicorn 进程并分别请求健康检查和 OpenAPI。

### Result

PASS。所有自动测试和质量门禁通过；真实服务返回预期状态与契约版本；没有遗留监听端口。

### Discovery

系统 Python 3.9.6 已不适合作为长期基线。uv 可以在不替换系统 Python 的情况下提供项目隔离的 Python 3.12。上游 FastAPI TestClient 触发弃用警告，测试已改用 HTTPX ASGI transport 并实现零警告。

### Next

P1-T02：实现 Provider 配置读取与密钥脱敏。

## 2026-09-05 14:02

### Goal

完成 P1-T01：定义一次性能样本的请求、原始结果、计时点和错误数据契约。

### Changes

新增两份 JSON Schema、契约语义文档、成功 / 失败 / 普通 / Streaming 样例和标准库验证器；新增 ADR-004 固定时钟选择。

### Verification

运行契约验证器、Python 语法检查和全量 JSON 解析；验证响应体 SHA-256、字节数、UTC 时间、计时顺序、状态 / 错误、模式 / TTFT、Token 守恒与凭证字段禁用规则。

### Result

PASS。4 个有效样例接受，2 个无效样例和 3 个无效变异用例按预期拒绝，2 个 Schema 可解析。

### Discovery

性能契约可以在不绑定后端语言的情况下先稳定数据语义。正式实现前仍需完成后端技术栈选型；完整 JSON Schema 标准验证可随依赖管理一起接入。

### Next

P0-T05：选择后端技术栈并初始化最小可测试工程。

## 2026-09-03 21:27

### Goal

完成项目初始化分析、长期文档、架构、Phase 1 拆解和第一版网站原型。

### Changes

创建项目文档和零依赖产品仪表盘；在设计上区分真实能力、演示数据和规划能力。

### Verification

检查文件结构和资源引用；用真实浏览器分别在 1280 × 800 与 390 × 844 视口加载页面；操作趋势范围、筛选、模拟检测和报告弹窗；检查控制台。

### Result

PASS。所有页面资源成功加载，核心交互按预期工作，移动端根页面横向滚动问题已修复，浏览器控制台无 warning / error。

### Discovery

工作区没有 Node.js / npm；Python 3.9.6 可用于本地静态服务。静态原型适合校准产品工作流，但不能替代 Phase 1 的真实性能测量链路。

### Next

P1-T01：定义测试请求、原始结果、计时点和错误的数据契约。
