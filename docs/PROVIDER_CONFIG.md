# Provider Configuration v1

Phase 1 将 Provider 元数据与凭证值分离：TOML 文件保存可审计配置，API Key 只从运行环境解析到内存中的 `SecretStr`。

## Files

- 示例：`backend/config/providers.example.toml`
- 默认本地配置：`backend/config/providers.toml`（已被 Git 忽略）
- 应用设置示例：`backend/.env.example`

## Provider Metadata

```toml
schema_version = "provider-config.v1"

[[providers]]
provider_id = "northstar_demo"
display_name = "Northstar Demo"
endpoint = "https://api.example.com/v1/chat/completions"
protocol = "openai-chat-completions"
credential_ref = "env:NORTHSTAR_API_KEY"
adapter = "openai-compatible"
default_model = "gpt-5.2"
timeout_ms = 30000
```

Phase 1 只支持 `env:` 凭证引用。`credential_ref` 保存的是环境变量名称，不是密钥值。`provider_id` 在同一个 Catalog 中必须唯一。

## Loading Flow

```text
AppSettings
    -> provider_config_path
    -> load TOML and validate ProviderCatalog
    -> select ProviderDefinition
    -> resolve env reference
    -> ResolvedProvider(api_key=SecretStr)
```

配置语法或字段错误只报告字段位置，不回显输入值。缺少凭证时报告 Provider ID 和环境变量名称，不输出其他环境变量或密钥。

## Redaction Rules

结构化日志中以下字段无条件替换为 `[REDACTED]`：

- Authorization / Proxy-Authorization
- API Key / X-API-Key
- Access Token
- Cookie / Set-Cookie

对于非结构化文本，系统同时遮蔽已解析密钥的精确值、Bearer Token 和常见密钥赋值形式。`RedactingFormatter` 在日志最终格式化后执行，因此异常文本和日志参数也会经过处理。

脱敏是防御措施，不是保存敏感日志的许可。默认原则仍然是：不记录请求鉴权头、不记录环境变量集合、不把 `SecretStr` 转成普通字符串。

## Verification

```bash
cd backend
uv run pytest -W error tests/test_config.py tests/test_redaction.py
```

覆盖：环境设置、TOML 加载、唯一 ID、环境变量解析、缺失凭证、配置错误不回显、对象序列化脱敏、嵌套结构脱敏和日志格式化脱敏。
