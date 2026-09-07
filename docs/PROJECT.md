# Project Vision

通过可重复、可量化、可验证的数据，建立一个能够客观评价 AI API 与中转服务质量的平台，帮助用户判断服务是否稳定、快速、能力是否符合宣传，以及综合成本后是否值得使用。

# Product Positioning

产品是“AI API 服务质量评估与决策平台”，不是给模型真假下绝对结论的检测器。它提供性能、可靠性、能力、行为一致性和成本的证据，并保留结论所依赖的原始数据。

# Target Users

- 需要选择或持续监控 AI API 供应商的开发者和技术团队
- 运营多个上游 Provider、需要验证线路质量的平台方
- 研究模型行为、能力与路由变化的评测人员
- 需要用可追溯数据支持采购和迁移决策的负责人

# Core Problems

1. 服务是否可用、稳定，延迟与吞吐是否满足业务要求？
2. 服务实际提供的模型能力是否符合其宣传？
3. 行为与官方参考模型的一致程度如何，是否出现显著变化？
4. 综合性能、可靠性、能力、一致性与成本后应如何选择？

# Core Capabilities

1. **API 性能检测**：总延迟、TTFT、输出速度、成功率、错误率、分位数、抖动和并发能力。
2. **模型能力 Benchmark**：Coding、Math、Reasoning、指令遵循、长上下文、结构化输出与工具调用。
3. **模型行为一致性研究**：基于有对照组、可重复实验的特征向量，输出与参考模型的相似度及证据。
4. **综合评价**：形成可解释、可追溯、可重新计算的评分、排名、趋势与检测报告。

# Non-Goals

- 当前阶段不判断模型“真假”，也不承诺识别模型身份。
- 当前阶段不确定最终评分权重。
- 不在缺少成本估算和用户确认时执行大规模付费 API 调用。
- 不在 Phase 0 构建分布式队列、复杂权限系统或生产级多租户架构。
- 界面原型中的演示数据不作为真实测量结果。

# Architecture

```text
Frontend
   |
API Service ------------------------------ Report / Query API
   |
Scheduler -> Task Queue -> Worker -> Provider Adapter -> AI Provider
                           |
                           +-> Raw Result Storage
                           +-> Metrics Pipeline -> Aggregated Metrics
                           +-> Benchmark Engine
                           +-> Fingerprint Research Pipeline
                                      |
                                      +-> Scoring (later phase)
```

## Current Implementation Baseline

- **Frontend prototype**：零依赖 HTML / CSS / JavaScript，仅用于产品工作流验证
- **Backend**：Python 3.12、FastAPI、Pydantic，由 uv 管理环境与锁文件
- **Provider HTTP client**：HTTPX；普通 OpenAI-compatible Chat Completions 与单调时钟总延迟测量已实现
- **Phase 1 storage**：SQLite，保持原始记录优先；生产数据库留待真实负载验证
- **Quality gates**：pytest、Ruff、JSON Schema Draft 2020-12 与领域契约验证器

## Responsibilities

- **Frontend**：配置检测、查看运行状态、比较指标和阅读可追溯报告；不自行计算权威指标。
- **API Service**：校验请求、管理 Provider 配置、创建任务、查询结果；不执行长时间检测。
- **Scheduler**：根据计划生成可审计的测试任务，控制频率、预算和重试策略。
- **Worker**：执行任务、记录时间点、收集响应与错误，并保证单个任务可复现。
- **Provider Adapter**：隔离不同 Provider 的鉴权、请求协议、流式事件和错误格式。
- **Storage**：优先保存请求参数、原始响应、时间信息、Token Usage、错误和版本信息。
- **Metrics**：从原始数据派生 TTFT、总延迟、Tokens/s、分位数和稳定性指标。
- **Benchmark**：管理能力题集、评分器、运行条件与结果，不和性能探测逻辑混合。
- **Fingerprint Research**：管理参考数据集、特征提取和实验验证；只输出有实验依据的相似度。
- **Scoring**：后期基于稳定指标生成可解释评分，可从原始数据重新计算。

# Principles

1. 可运行优先于看起来完整。
2. 可验证优先于主观判断，数据优先于猜测。
3. 小步闭环，单任务可实现、可测试、可复现。
4. 原始数据优先保留，评分是可重新计算的派生数据。
5. 性能检测、Benchmark、指纹研究和评分保持清晰边界。
6. 不提前抽象，不用 Mock 成功冒充真实功能成功。
7. 模型行为特征必须验证可重复性、可分离性、鲁棒性和成本效率。

# Current Phase

**Phase 1：单 Provider Performance MVP。**

Phase 0 已完成：项目文档、总体架构、产品界面原型、后端技术栈和可测试工程均已建立。Phase 1 已完成单次性能检测数据契约、Provider 配置与密钥脱敏、非流式 OpenAI-compatible Adapter 和总请求耗时测量，当前进入 Streaming 与首个有效内容事件识别。
