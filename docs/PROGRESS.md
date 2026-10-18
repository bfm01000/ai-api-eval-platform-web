# Current Status

Current Phase: Phase 2–3 本地评测原型 + 公共版发布准备
Current Task: 题集、参考导入与报告验收；等待正式部署环境和官方参考样本
Status: 本地功能可测，尚未公网部署；不能宣称模型身份已验证

## 2026-10-18 更新

- 本地新增多 Provider 同提示词顺序对比、1h / 24h / 7d 原始结果派生指标，移除概览演示数字。
- 公共访客仍仅可使用自己的 Key；公共对比不落盘，本地密钥和历史接口继续隔离。
- 新增 `capability-core.v1` 六题版本化小题集、确定性评分、每题 1–3 次顺序执行、原始结果关联和历史 JSON 报告。
- 新增用户填写单价的费用预估与本机评测批次累计 100 元预估门槛。价格未经平台核验，其他测试入口不计入该累计值，**不是实际账单硬上限**。本轮没有发起真实付费采样。
- 新增同版本参考答案导入及回答一致率；来源由导入者声明，身份始终标为未验证。
- 164 项自动测试已通过；正式服务器、域名、官方 Key 暂无，公网部署和真实参考行为研究仍待完成。

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
- 实现增量 SSE 解析器（多行 data、注释、CRLF、跨 chunk 不完整事件保留）与流式状态累积
- 实现流式 Chat Completions 请求（`stream=true` + `stream_options.include_usage`）、增量 UTF-8 解码与 `[DONE]` 追踪
- 固定首个有效内容口径：角色声明、空白 delta、usage-only、keepalive、注释均不计 TTFT，仅非空文本 delta 触发首内容打点
- 为流式实现首内容单调时钟打点、`generation_duration_ms` 与 `tokens_per_second`（usage 与 TTFT 齐全且生成耗时为正时）
- 覆盖流式错误路径：非 2xx、流中 timeout / 断连、无效 JSON chunk、无 `[DONE]`、无内容、非 UTF-8，并保留截断的原始流文本
- 在真实 loopback TCP 流式服务上验证 TTFT 语义、内容拼装、Usage 提取、字节数与哈希
- 固定 Usage 口径：Provider 未提供时保持 unavailable（null），不用 0 代替未知
- 固定 Tokens/s 口径：以 `generation_duration_ms` 为窗口，usage 缺失、TTFT 缺失或生成耗时非正时为 null；output_tokens 为 0 时按契约计算为 0.0
- 实现 adapter 结果 / 错误到 performance-result.v1 文档的契约化构建，含 ULID 式时间排序 ID、execution / timing / raw / usage / error 全块映射
- 实现 SQLite 原始结果存储：`document_json` 为事实源，派生索引列覆盖 run / sample / status / category / provider / model / mode / 总延迟 / TTFT
- 在写入前用 JSON Schema Draft 2020-12（含跨文件 `$ref` 与 format 断言）验证结果文档，违反契约即拒绝落库
- 建立 Git 忽略的本地测试凭证文件与 Provider 配置，保留两个已验证有效的外部测试线路
- 新增 Provider 列表与单次真实测试 API，由后端托管现有仪表盘，浏览器不接触凭证值
- 将“发起检测”从模拟计时改为真实普通 / Streaming 请求，展示总延迟、TTFT、Usage 和响应摘要
- P1-T09：实现按样本计划顺序执行重复请求的执行器（`RunExecutor`、`RunPlan`、`RunSummary`）及单样本隔离执行与持久化
- P1-T10：实现基于原始结果记录的 P50、P90、P95、P99 分位数、成功率、错误分类统计与 Token/流式速率聚合模块（`metrics.py`）
- 在 `ResultStore` 中增加按 run_id 按需计算派生指标接口 `get_run_metrics`，保持原始数据为唯一事实源架构原则
- 在 `RunExecutor` 完成时自动派生挂载聚合指标到 `RunSummary.metrics`
- 新增 10 个测试用例，覆盖线性插值分位数算法、统计汇总、普通/流式成功聚合、混合错误与取消分类、存储与执行器集成

# In Progress

- 无

# Next

**在目标服务器完成 HTTPS 与真实 Provider 普通 / Streaming 验收；取得官方参考访问方式后采集同条件样本。**

# Blockers

- 尚未指定正式服务器和域名。本机无 Docker Engine；GitHub CI 已通过容器 / Caddy 检查，目标服务器验收待执行。
- 官方模型参考 Key 暂无，无法采集官方同条件样本并验证模型身份。

# Known Issues

- 当前能力题集仅为确定性小样本原型，不能代表广泛能力、长期稳定性或模型身份。
- 公共限流使用单进程内存状态，首版仅支持单 worker / 单实例，重启会重置配额。
- 行为指纹可分离性、带置信度的模型相似性和综合排行尚未完成。
- 当前 Provider 配置仅支持 `env:` 凭证引用；Secret Manager 留待规模化阶段
- 外部 Provider 仅完成 `gpt-5.5` 单次短请求烟雾测试，不能代表长期稳定性或性能分布
- `max_output_tokens -> max_tokens` 是当前通用映射；特定 Provider 方言尚未实现
- HTTPX 当前未提供可直接持久化的 DNS、Connect、TLS、响应头和首字节分段时间，不能从总延迟反推

# Last Verification

执行：`uv run pytest -W error`、`uv run ruff check .`、`uv run ruff format --check .`、`uv lock --check`、`uv run python ../scripts/verify_contract.py`，并解析全部契约 JSON。

结果（2026-10-17）：**156 个测试零警告通过**，Ruff、格式、锁文件和契约验证通过；生产依赖 pip-audit 未发现已知漏洞。浏览器使用模拟上游验证普通 / Streaming、失败状态、提交清空 Key、刷新清空记录及真实 JSON 导出；导出不含测试 Key。390px 与 1280px 页面无整体横向溢出，控制台无错误。Docker / Caddy / 域名证书与真实公网 Provider 尚未完成本轮验收，不能把模拟上游结果视为真实服务商表现。

## 2026-10-17

### Goal

将公共版修复到可进行生产部署验收的程度，保持本地中转 Key 与访客 Key 隔离。

### Changes

新增连接层公网地址校验与 DNS 解析后 IP 固定，保留原始 TLS SNI / 证书验证，拒绝重定向与压缩响应；公共历史关闭，自带 Key 的任务只使用请求内存。校验错误不回显任何输入，公共请求增加次数、并发、16 KiB 入站、1 MiB 出站及总时限限制。本地 Provider 接口仅允许 loopback + localhost。

公共页面移除演示数据，增加真实报告导出、提交立即清空 Key、失败分类与手机布局修复。新增 Docker、Compose、Caddy HTTPS、GitHub Actions、生产冒烟脚本和部署 / 回滚文档。

### Verification

156 个自动测试及所有原有质量检查通过。浏览器与依赖审计结果见上方 Last Verification。模拟测试不使用本机真实 Key。

### Result

代码改造完成，发布候选可供部署验收；尚未发布到公网。部署边界见 `docs/DEPLOYMENT.md`。

### Next

指定服务器和域名，运行容器 CI 与生产冒烟，并用可撤销的自有 Key 完成真实服务商验收。

## 2026-09-27 23:04

### Goal

将已验证有效的测试 Key 安全接入本地网站，并允许用户选择一个 Provider 发起真实测试。

### Changes

从原始 Key 清单中提取两个有效凭证到 Git 忽略且权限为 600 的 `backend/.env`；新增 Git 忽略的 `backend/config/providers.toml`，只保存 Provider 元数据和环境变量引用。FastAPI 新增 Provider 列表与单次测试接口，并显式托管前端资源；网页从后端加载 YLAI Test / AIComing Test，支持模型覆盖和普通 / Streaming 模式，显示总延迟、TTFT、Token Usage、响应或安全错误信息。API 与页面均不返回凭证值。

### Verification

运行 81 个后端测试并将 warning 视为 error，执行 Ruff、格式、锁文件和完整契约验证；测试覆盖 Provider 元数据响应不含凭证、真实 Adapter 请求构造和静态首页。启动真实 Uvicorn 后在浏览器依次选择两个 Provider：YLAI Test 总延迟约 2.12 秒，AIComing Test 约 3.74 秒，均使用 `gpt-5.5` 返回 `OK`，Usage 均为 11 / 5 / 16。

### Result

PASS。网站可以选择两个有效测试线路并完成真实单次生成；本地凭证文件和 Provider 配置均被 Git 忽略，密钥未进入前端响应、测试输出或版本控制。

### Discovery

Pydantic Settings 只读取已声明字段，动态 Provider 凭证不会仅因存在 `.env` 就自动进入 `os.environ`；应用现显式加载本地 `.env` 并让进程环境变量覆盖同名值。单次延迟波动明显，只能作为连通性烟雾测试，不能替代 P1-T09 / P1-T10 的重复样本统计。

### Next

P1-T09：完成按样本计划顺序执行重复请求的执行器，并接入当前测试入口。

## 2026-09-20 23:05

### Goal

完成 P1-T08：把请求快照与原始结果按 performance-result.v1 契约持久化到 SQLite。

### Changes

新增 `results.py`：时间排序 ID 生成（`前缀 + 26 字符 Crockford base32`，48 位毫秒时间戳 + 80 位随机）；`build_request_snapshot` 固化请求快照（endpoint、协议、`credential_ref` 引用、模型、模式、超时、消息、参数、创建时间）；`build_success_document` / `build_failure_document` / `build_cancelled_document` 完成 execution / timing / raw_response / usage / error 全块映射。失败且无 HTTP 响应时按契约 fixture 口径置 raw 块全 null、`bytes_received=0`；失败 / 取消一律 `usage.source=unavailable`；分段计时（DNS / 连接 / TLS / 响应头 / 首字节）HTTPX 未暴露，诚实置 null。新增 `storage.py`：stdlib sqlite3，runs / results 两表，`document_json` 为事实源并派生查询索引列；写入前用 JSON Schema Draft 2020-12 验证器（跨文件 `$ref` registry + format checker）校验，违规抛 `ContractViolationError`；DB 默认落在 gitignored 的 `backend/data/`；外键约束开启，未知 run 拒绝插入。`ProviderCallError` 补充结构化 `message` 属性供契约错误块映射。

### Verification

运行 72 个后端测试并将 warning 视为 error；新增 17 个测试：ID 模式与时间戳单调、快照 Zulu 时间与引用边界、标准 / 流式成功文档契约校验、无响应 timeout 与 401 Provider 失败映射、取消文档、往返读写、派生列、缺失键 / 成功带错误 / 取消类别错误拒绝、外键与已关闭存储拒绝；以凭证字面量全文断言不出现在任何序列化文档；执行 Ruff、格式、锁文件与完整契约验证。

### Result

PASS。契约文档构建与持久化链路按 performance-result.v1 与 PERFORMANCE_CONTRACT.md §3 口径落地；密钥与凭证值不进入落库文档。外部 Provider 未测试，存储层未经过真实供应商数据检验。

### Discovery

契约 schema 的跨文件 `$ref` 需要 referencing registry 按 `$id` URI 注册两份 schema；jsonschema 的 format 断言（date-time / uri）需显式启用 `FORMAT_CHECKER`。失败样本的部分 Usage 不可信，统一按 unavailable 记录比"尽力解析"更符合 Raw Data 原则。

### Next

P1-T09：实现按样本计划顺序执行重复请求的执行器。

## 2026-09-20 22:10

### Goal

完成 P1-T07：记录 input/output tokens，固定 Tokens/s 口径。

### Changes

为普通与流式补齐 Usage 缺失路径断言；`tokens_per_second` 移除 output_tokens 正数前置条件，严格对齐 PERFORMANCE_CONTRACT.md §4：usage 与 `first_content_ms` 均存在且 `generation_duration_ms > 0` 即计算（0 输出按 0.0 记录），否则为 null。

### Verification

运行 55 个后端测试并将 warning 视为 error；新增 5 个 Usage/Tokens/s 测试覆盖：普通与流式无 Usage 时保持 unavailable、生成耗时为 0 时 Tokens/s 为 null、输出 0 tokens 时为 0.0、500 ms 总延迟中 100 ms 生成窗口得到 40.0 Tokens/s；执行 Ruff、格式、锁文件与完整契约验证。

### Result

PASS。Tokens/s 只由生成窗口决定，不由总延迟反推；Provider 未返回 Usage 时不虚构计数。

### Discovery

Adapter 内部曾额外要求 `output_tokens > 0` 才计算 Tokens/s，与契约文档字面口径不一致；以文档为准修正，零输出样本按 0.0 记录而非 null。

### Next

P1-T08：持久化请求参数、原始响应、时间点、Usage 和错误。

## 2026-09-20 21:40

### Goal

完成 P1-T05 / P1-T06：实现流式请求、首个有效内容事件识别与 TTFT 计时语义。

### Changes

新增增量 SSE 解析模块（事件块扫描、多行 data 拼接、注释与 CRLF 处理、不完整事件原文保留）；`RequestTimer` 增加单次首内容单调时钟打点，`RequestTiming` 增加 `first_content_ms` 与 `generation_duration_ms`；OpenAI-compatible Adapter 增加 `complete_streaming`，含增量 UTF-8 解码、`stream_options.include_usage`、首个非空文本 delta 打点、`[DONE]` 追踪与截断原始流保留；错误分类扩展 `internal` 类别与阶段以与契约枚举一致。

### Verification

运行 50 个后端测试并将 warning 视为 error；用可控时钟断言 TTFT 100 ms / 总延迟 300 ms / Tokens/s 20.0 的精确口径；用真实 loopback SSE 服务验证首内容前 50 ms 延迟、内容拼装、Usage 提取、字节数与 SHA-256；覆盖非 2xx、流中 timeout、断连、无效 JSON chunk、无 `[DONE]`、无内容、非 UTF-8 与首内容规则（角色声明、空白 delta、usage-only、keepalive 均不计 TTFT）；执行 Ruff、格式、锁文件与完整契约验证。

### Result

PASS。流式成功与全部错误路径按预期处理；密钥不出现在结果序列化或异常中。外部 Provider 未测试，因此不声称真实供应商已通过流式验证。

### Discovery

FakeClock 测试最初误给角色声明 / 空白 delta 分配了时钟推进——它们不消耗单调时钟，只有首内容打点和完成打点消耗；测试假值已按真实消费顺序修正。SSE 事件按字节 chunk 到达时可能跨事件分割，解析器必须基于原始文本块扫描而非按行状态机，否则不完整事件的行前缀会丢失。

### Next

P1-T07：固定 Token Usage 与 Tokens/s 计算口径。

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
