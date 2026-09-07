# Performance Sample Contract v1

本契约描述 Phase 1 中最小、可独立验证的执行单位：**向一个 Provider 发起的一次模型请求及其一次执行结果**。批量运行、重试策略、分位数和跨 Provider 聚合不属于 v1 原始记录。

机器可读定义：

- `contracts/performance-request.v1.schema.json`
- `contracts/performance-result.v1.schema.json`

## 1. Identity and Versioning

- `contract_version` 是持久化记录的一部分；破坏兼容性的变化必须发布新版本，不能静默修改旧记录语义。
- `run_id` 标识一组测试，`request_id` 标识组内一个样本，`result_id` 标识该样本的一次实际执行结果。
- `sample_index` 从 1 开始。同一个请求发生重试时保留 `request_id`，增加 `execution.attempt`，并生成新的 `result_id`。

## 2. Request Contract

请求记录必须包含：Provider、Endpoint、协议、凭证引用、Adapter、模型、模式、超时、完整消息、采样参数和创建时间。

### Credential boundary

`credential_ref` 只描述如何在运行时取得凭证，例如 `env:NORTHSTAR_API_KEY`。契约及结果中禁止出现凭证值，也禁止任何层级出现：

- `authorization`
- `proxy-authorization`
- `api-key` / `api_key` / `x-api-key`
- `cookie` / `set-cookie`
- `access-token` / `access_token`

Prompt 和原始响应仍可能包含用户敏感信息。它们不是凭证，但存储层必须执行访问控制和保留策略。

## 3. Raw Result Contract

结果记录包含不可变的 `request_snapshot`，使未来重算不依赖已变化的 Provider 配置。`raw_response.body_text` 保存 HTTP 内容解码后的响应体或 SSE 文本，`body_sha256` 对实际保存的 UTF-8 文本字节计算。`bytes_received` 是凭证脱敏及存储截断前、内容解码后的 UTF-8 总字节数，不是包含 TLS、HTTP framing 或压缩的线上传输字节。

如果响应正文回显了当前请求凭证，必须在进入结果模型前替换并设置 `credential_redacted=true`。这是 Raw Data 原则的强制安全例外；此时哈希针对脱敏后的已保存文本，字节数仍表示脱敏前接收量。如果存储层因限制截断内容，必须设置 `truncated=true`；哈希同样只针对已保存文本。

`usage` 原样保留 Provider Usage。Provider 未提供时，三个 Token 字段均为 `null` 且 `source=unavailable`，不能用 0 代替未知。

原始结果不保存 P50、P95、P99、成功率或综合评分。这些都是可由多条原始记录重新计算的派生数据。

## 4. Clock and Timing Semantics

`execution.started_at` 与 `completed_at` 使用 UTC RFC 3339 墙钟时间，用于排序、审计和跨服务关联。所有延迟测量使用同一进程内的单调时钟，防止 NTP 或系统时间校正造成负值。

所有 `timing.*_ms` 都是从“HTTP 客户端开始执行该请求”的单调时钟起点计算的累计偏移：

| 字段 | 语义 | 缺失时 |
| --- | --- | --- |
| `dns_completed_ms` | DNS 解析完成 | 客户端未暴露或复用连接时为 `null` |
| `connect_completed_ms` | TCP 连接完成 | 未暴露或复用连接时为 `null` |
| `tls_completed_ms` | TLS 握手完成 | HTTP 或未暴露时为 `null` |
| `response_headers_ms` | 完整响应头可用 | 此前失败时为 `null` |
| `first_byte_ms` | 首个响应体字节到达 | 此前失败或客户端未暴露时为 `null` |
| `first_content_ms` | 首个非空模型内容片段完成协议解码 | 普通请求或此前失败时为 `null` |
| `response_completed_ms` | 响应体消费结束、流结束或执行失败 | 必填 |

### Derived metrics

- `total_latency_ms = response_completed_ms`
- Streaming：`ttft_ms = first_content_ms`
- Standard：`ttft_ms = null`
- `generation_duration_ms = response_completed_ms - first_content_ms`
- `tokens_per_second = output_tokens / (generation_duration_ms / 1000)`

只有 `output_tokens` 和 `first_content_ms` 均存在且 `generation_duration_ms > 0` 时才能计算 Tokens/s，否则为 `null`。

### First content rule

角色声明、空字符串、空白内容、usage-only 事件、keepalive、注释行和 `[DONE]` 都不算首个内容。Tool Call 的首个有效增量是否计入 TTFT 将在 Tool Calling 阶段发布新口径；Phase 1 v1 只以非空文本内容为准。

## 5. Error Taxonomy

| Category | 用途 | 示例阶段 |
| --- | --- | --- |
| `dns` | 域名解析失败 | `dns` |
| `connect` | TCP / 路由连接失败 | `connect` |
| `tls` | TLS 握手或证书失败 | `tls` |
| `timeout` | 任一阶段超过超时限制 | 实际发生阶段 |
| `http` | 非 2xx 且无法解析为 Provider 错误 | `response_headers` / `response_body` |
| `provider` | Provider 返回结构化错误 | `response_body` |
| `protocol` | SSE / 协议语义不符合约定 | `protocol_decode` |
| `parse` | JSON 或内容解析失败 | `protocol_decode` |
| `internal` | Worker / Adapter 内部错误 | `internal` |
| `cancelled` | 用户或调度器取消 | 实际发生阶段或 `unknown` |

`code` 是稳定、可聚合的机器码；`message` 是脱敏后的人类说明；`retryable` 表示同条件重试是否可能成功。HTTP 状态和 Provider 错误码分别保存在 `http_status`、`provider_code`，不得把原始密钥或完整请求头写入错误消息。

## 6. Invariants

除 JSON Schema 结构约束外，验证器强制以下领域规则：

1. UTC 时间必须以 `Z` 或 `+00:00` 表示，完成时间不得早于开始时间。
2. 非空计时点按网络生命周期单调递增，且都不得晚于 `response_completed_ms`。
3. Streaming 成功必须有 `first_content_ms`；Standard 必须为 `null`。
4. 成功结果必须为 2xx 且 `error=null`；失败和取消结果必须有错误对象。
5. 三个 Token 数同时存在时，`total_tokens = input_tokens + output_tokens`。
6. `source=unavailable` 时三个 Token 字段必须都是 `null`。
7. 响应体存在时必须同时提供 SHA-256；响应体为空时 SHA-256 必须为 `null` 且 `credential_redacted=false`。
8. 凭证型字段名在任何深度都被拒绝。
9. `cancelled` 状态必须使用 `cancelled` 错误分类。

## 7. Verification

运行：

```bash
python3 scripts/verify_contract.py
```

验证器使用 Python 标准库，无需安装依赖。它检查 Schema 文件可解析、有效样例被接受、无效样例被拒绝，并执行上述跨字段规则。
