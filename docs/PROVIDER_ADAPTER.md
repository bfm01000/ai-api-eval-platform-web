# OpenAI-Compatible Provider Adapter

当前 Adapter 实现 Phase 1 的普通 Chat Completions 请求。它验证请求构造、鉴权边界、响应解析、原始正文保留、基础错误映射和总延迟测量；不包含 Streaming、重试或持久化。

## Request Mapping

Provider Endpoint 必须是完整的 Chat Completions URL。生产 Endpoint 必须使用 HTTPS，且不得包含 userinfo、查询参数或 fragment；HTTP 只允许 localhost / loopback 测试服务。

请求固定发送：

```json
{
  "model": "provider default or request override",
  "messages": [],
  "stream": false
}
```

通用参数 `temperature`、`top_p`、`seed`、`stop` 原名发送；契约字段 `max_output_tokens` 在 OpenAI-compatible Chat Completions 请求中映射为 `max_tokens`。不同 Provider 如果需要其他参数名，必须通过后续明确的 Adapter 能力扩展处理，不能静默猜测。

鉴权头只在 `complete_standard()` 的发送边界由 `SecretStr` 临时展开，不进入请求模型、结果模型、异常消息或脱敏响应头。

## Timing Boundary

Adapter 在 HTTPX 开始执行请求前启动计时，在完整响应体返回或 HTTPX 抛出传输异常后立即结束计时。成功结果和所有类型化错误都携带同一结构的 `RequestTiming`：

- `request_started_at` 和 `response_completed_at` 是 UTC 墙钟时间，仅用于审计与关联
- `response_completed_ms` 使用 `perf_counter_ns()` 单调时钟计算，并保留三位小数
- `total_latency_ms` 与 `response_completed_ms` 是同一指标
- 若墙钟在请求期间回拨，完成审计时间不会早于开始时间；延迟值仍只由单调时钟决定

HTTPX 普通 `post()` 在返回前会消费完整响应体，因此结束点表示响应体消费完成，不是仅收到响应头。当前客户端没有可靠暴露 DNS、Connect、TLS、响应头和首字节分段计时，这些值不能由总耗时猜测或填成 0。普通请求没有 TTFT；Streaming 与首个有效内容事件由后续任务实现。

## Success Response

普通请求成功必须满足：

- HTTP 状态为 2xx
- 响应是 UTF-8 JSON
- 存在 `id`、`model` 和至少一个 Choice
- 首个 Choice 含文本 `message.content`

结果保留 Completion ID、实际模型名、文本、finish reason、可选 Usage，以及经过敏感 Header 脱敏的原始 HTTP 响应。正文如果回显当前凭证，会在解析和保存前替换为 `[REDACTED]` 并设置 `credential_redacted=true`；SHA-256 针对保存文本计算，`bytes_received` 保留脱敏前的内容字节数。

## Error Mapping

| Condition | Category | Code |
| --- | --- | --- |
| HTTPX timeout | `timeout` | `TIMEOUT.REQUEST` |
| HTTPX transport failure | `connect` | `CONNECT.REQUEST_FAILED` |
| 非 2xx，存在结构化 `error` | `provider` | `PROVIDER.ERROR_RESPONSE` |
| 非 2xx，无结构化 Provider error | `http` | `HTTP.NON_SUCCESS` |
| 非 UTF-8 正文 | `parse` | `PARSE.NON_UTF8_RESPONSE` |
| 2xx 但 Completion 结构无效 | `protocol` | `PROTOCOL.INVALID_COMPLETION` |

错误消息只包含稳定代码和安全描述。非 2xx 或协议错误仍挂载 `RawProviderResponse`，供 P1-T08 保存；原始正文可能包含用户数据，因此不应直接写入普通日志。

## Verification Boundary

自动集成测试会启动真实 loopback TCP HTTP 服务，由 HTTPX 完成实际连接、鉴权头发送、JSON 请求与响应读取，并验证正数总延迟和 UTC 时间顺序。测试客户端关闭环境代理继承，确保 loopback 请求不会被系统代理转发。可控时钟测试另外证明：即使 UTC 墙钟相差 10 秒，250.25 ms 单调时钟耗时仍保持 250.25 ms；传输异常也保留结束时间和耗时。

这些验证证明了本地协议与计时语义，不代表任何外部 Provider 已通过。

外部 Provider 烟雾测试需要用户提供自己的 Endpoint、模型和环境变量凭证；在完成前，项目不得声称已验证真实供应商兼容性。
