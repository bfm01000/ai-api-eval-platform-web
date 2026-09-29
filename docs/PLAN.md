# Roadmap

## Phase 0：项目基础设施与架构

### Goal

建立任何新会话都能恢复的项目事实来源，明确系统边界，并用产品界面原型校准信息架构。

### Why

先统一目标、数据原则和模块职责，避免后续性能、Benchmark 与指纹逻辑相互污染。

### Tasks

- [x] 建立长期项目文档
- [x] 定义第一版系统架构和模块职责
- [x] 拆分 Phase 1 小任务与完成标准
- [x] 创建可操作的静态仪表盘原型
- [x] P0-T05：选择并初始化后端技术栈

### Definition of Done

- 项目目标、非目标、架构、路线和关键决策已入库
- Phase 1 可按独立任务逐项验证
- 界面原型可展示目标工作流且演示数据标识清楚
- 已明确进入 Phase 1 的唯一下一任务

## Phase 1：单 Provider Performance MVP

### Goal

对一个 OpenAI-compatible Provider 完成一次可复现的普通与流式请求，保存原始结果并计算基础性能指标。

### Why

性能检测是最短、最客观的端到端价值链路，也是后续调度、对比和评分的数据基础。

### Tasks

- [x] P1-T01：定义测试请求、原始结果、计时点和错误的数据契约
- [x] P1-T02：实现 Provider 配置读取与密钥脱敏
- [x] P1-T03：实现一次非流式 OpenAI-compatible 请求
- [x] P1-T04：准确记录请求开始、响应结束和总延迟
- [x] P1-T05：实现流式请求和首个有效内容事件识别
- [x] P1-T06：计算 TTFT，并用可控流式服务验证计时语义
- [x] P1-T07：记录 input/output tokens，定义 Tokens/s 口径
- [x] P1-T08：持久化请求参数、原始响应、时间点、Usage 和错误
- [ ] P1-T09：支持同一配置的顺序重复测试
- [ ] P1-T10：由原始记录计算 P50、P95、P99 和成功率
- [ ] P1-T11：提供最小任务 API 和命令行完整运行入口
- [ ] P1-T12：补齐单元测试、集成测试与脱敏日志验证

### Definition of Done

- 支持至少一个 OpenAI-compatible Provider
- 支持普通请求与 Streaming
- 能记录总延迟、TTFT、Token Usage 和 Tokens/s
- 能区分网络、HTTP、Provider、解析和超时错误
- 请求参数、原始响应、元数据和时间点可持久化
- 支持重复测试并计算 P50、P95、P99 与成功率
- 关键计算有自动测试，敏感信息不会写入日志或结果
- 可以从单一入口完整执行并查询一次测试任务

## Phase 2：多 Provider、调度与历史指标

引入 Provider Adapter、多线路配置、预算约束的 Scheduler、历史趋势和横向对比。

## Phase 3：Benchmark Framework

建立版本化题集、运行器、评分器和可复现的能力评测流程。

## Phase 4：Reference Model Dataset

以相同 Prompt、参数和采样策略采集官方参考模型数据，并记录成本和版本。

## Phase 5：Behavior Feature Research

逐项验证候选特征的可重复性、可分离性、鲁棒性和成本效率。

## Phase 6：Fingerprint / Consistency Prototype

组合经过验证的特征，研究最小 Probe Set，并输出带置信说明的参考相似度。

## Phase 7：综合评分

根据稳定指标和用户决策需求设计可解释、可追溯、可重新计算的评分体系。

## Phase 8：Dashboard / Ranking

产品化报告、历史趋势、Provider / 模型排行和决策视图。

## Phase 9：成本、稳定性与规模化

完善并发、限流、隔离、告警、成本治理、权限和生产运维能力。
