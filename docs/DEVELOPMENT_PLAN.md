# Collusion (共谋) 完整设计书 v3.0

当前版本：v0.4.0 | 代码版本：v3.2

## 一、项目概述

Collusion 是一个多 Agent 协作引擎，专为技术方案设计而生。通过多对象代言、交叉审查、可行性强制收束和投票评分，解决单 Agent 在方案设计阶段易出现的"意图丢失"、"过度设计"和"关键环节缺失"等问题。

**核心原则**：寄生而非替代（增强现有 AI 工具）、缓存无损（不破坏宿主缓存机制）、零额外配置（复用宿主 API Key）、流程决定质量（多样性来自协作流程）。

已在真实项目盲评中以 6:1 胜出 Superpowers，完成多平台适配验证。

## 二、已实现核心能力

| 模块 | 说明 |
|------|------|
| 多对象代言 | 安全专家、性能架构师、UX/产品专家三视角独立生成完整方案 |
| 环节共识与缺失补全 | 自动识别并补全用户需求中遗漏的关键环节 |
| 交叉审查 | 每个 Agent 轮流审查其他方案，一处修改，防止重复 |
| 可行性强制收束 | 工程对象代言人对过度设计做减法，确保方案务实 |
| 多维度投票评分 | 正确性/完整性/可行性/创新性/业务对齐 5 维打分，输出 Top 3 |
| MCP Server | 支持 stdio 和 SSE 传输，可接入任何支持 MCP 的宿主 |
| 异步编排状态追踪 | brainstorm_status / brainstorm_result 可查询进度 |
| HTML 报告 | 单文件离线可用，含雷达图、架构分层图、风险标注、MVP 标记 |
| 资产库索引 | 废弃方案自动入库，支持关键词检索和分支合并复用 |
| 跨平台分发 | Skill 模式（Claude Code/Cursor）、MCP Sampling 模式（Reasonix）、回退模式（通用） |
| Key 零配置 | 自动读取 DEEPSEEK_API_KEY → OPENAI_API_KEY → LLM_API_KEY → Reasonix 配置 |
| Skill 7 种模式 | scheme/enhance/review/plan/diagnose/choose/scout，纯 Claude Code Agent 驱动 |

## 三、多平台整合架构

Collusion 采用分层适配策略，在不同平台上以最优方式集成，共享同一套协作协议：

| 宿主类型 | 代表工具 | 集成模式 | 缓存影响 | 多 Agent 实现 |
|----------|---------|---------|---------|-------------|
| 原生多 Agent 宿主 | Claude Code, Cursor | Skill 模式（纯 Markdown 定义流程） | 无影响 | 宿主自身 Agent 并行 |
| 单 Agent 高缓存宿主 | Reasonix | MCP Sampling 模式（委托宿主调用 LLM） | 99% 命中率完全保留 | 外挂 Collusion 引擎 |
| 通用单 Agent 宿主 | Trae Solo 等 | 回退模式（Python 直接调 API） | 需自行优化 | Python 内部调度 |

### MCP Sampling 模式（Reasonix）数据流

```
用户输入 /collusion scheme
  → Reasonix 调用 Collusion MCP 工具
  → Collusion 分解任务、调度 Agent
  → 当需要 LLM 生成时，通过 sampling/createMessage 请求 Reasonix 代劳
  → Reasonix 用自己的 API Key + 缓存完成调用
  → 结果返回 Collusion 继续审查、收束、投票
  → 最终方案展示在 Reasonix 终端
```

### 纯 Skill 模式（Claude Code）使用

安装后输入 `/collusion scheme <任务>` 即可自动触发内部多 Agent 协作。零 pip install，零配置。

## 四、编排流水线（7 阶段）

| 阶段 | 名称 | 描述 | 模型 |
|------|------|------|------|
| Phase 1 | 任务解构 | 强模型将用户需求拆解为环节清单 | R1 |
| Phase 2 | 环节共识 | 各 Agent 审查清单，补全缺失环节 | Flash |
| Phase 3 | 并行提案 | 3 Agent 各自独立生成完整方案 | Flash |
| Phase 4 | 交叉审查 | 轮流审查他人方案，每次修改一个环节 | Flash |
| Phase 4.5 | 可行性收束 | 工程对象代言人做减法，防止过度设计 | Flash |
| Phase 4.6 | Owner 整合 | 每个方案的 Owner 融合所有修改建议 | Flash |
| Phase 6 | 投票评分 | 强模型 5 维打分，输出 Top 3 | R1 |
| Phase 7 | 渲染输出 | 生成 HTML 报告和 Markdown 蓝图 | - |

## 五、用户交互模式

### 5.1 主动触发（已实现）

| 命令 | 功能 | 状态 |
|------|------|------|
| `/collusion scheme` | 方案设计 | ✅ Skill + MCP |
| `/collusion enhance` | 方案增强 | ✅ Skill |
| `/collusion review` | 代码审查 | ✅ Skill |
| `/collusion plan` | 任务拆解 | ✅ Skill |
| `/collusion diagnose` | 问题诊断 | ✅ Skill |
| `/collusion choose` | 技术选型 | ✅ Skill |
| `/collusion scout` | 项目侦察 | ✅ Skill |

### 5.2 用户反馈回路（当前）

Skill 模式下：用户在对话中直接提出修改 → 3 Agent 审查 → 全票通过则更新方案。

MCP 模式下：HTML 报告嵌入修改输入区 → 提交后触发 3 Agent 审查 → 返回认可/隐患/创新反馈。

### 5.3 原生多 Agent 人机协作（v1.0.0+）

**3Agent 护航模式**：Agent 1 主执行、Agent 2 分身监听、Agent 3 分支探索，共享上下文，不中断用户工作流。

**"黑板+顾问"模式（Reasonix 专属）**：
- 主 Agent 维护上下文摘要并发布到共享黑板文件
- 三个子 Agent 静默运行，读取摘要独立设计，遇疑问通过黑板向主 Agent 询问
- 主 Agent 保持可交互，回答询问后子 Agent 继续
- 所有子 Agent 完成后，合并器自动运行，输出最终方案
- 子 Agent 推荐混合模型：架构师用 Max R1 追求创新与质量，安全/UX 用 Flash 控制成本

## 六、当前输出物

| 文件 | 格式 | 用途 |
|------|------|------|
| report.html | 单文件 HTML | 给人看的可视化报告，含雷达图、架构图、风险标注、MVP 标记 |
| report.md | Markdown | 给 AI 执行的施工蓝图，含结构化任务清单（兼容 Superpowers） |
| state JSON | JSON | 编排状态持久化，支持断点续传 |

## 七、路线图

| 版本 | 核心交付 | 状态 |
|------|---------|------|
| v0.4.0 | 多平台分发、HTML 报告、资产库、Key 零配置、Skill 7 模式 | ✅ 已交付 |
| v0.5.0 | Reasonix 一键集成、MCP 增强模式（6 工具）、宿主自动检测 | 📋 规划中 |
| v0.6.0 | 项目侦察 Python 引擎、标签机制、废案语义检索 | 📋 规划中 |
| v0.7.0+ | 动态 Agent 调度、社区角色市场、YAML 配置化 | 📋 远期 |
| v1.0.0+ | 3Agent 护航、黑板+顾问原生集成、共享上下文引擎 | 📋 远期 |

## 八、成本模型（单次方案设计）

| Agent 数量 | 模型组合 | 成本 | 耗时 |
|-----------|---------|------|------|
| 1 Agent | Flash | ¥0.04 | ~2 分钟 |
| 3 Agent | 全 Flash | ¥0.16 | ~4.5 分钟 |
| 3 Agent | 混合（架构 R1，安全/UX Flash） | ¥0.20-0.30 | ~5 分钟 |
| 3 Agent | 全 R1 | ¥0.30-0.50 | ~6-8 分钟 |

启用宿主缓存后成本可降低 40%-80%。

## 九、性能基线

| 指标 | 值 |
|------|-----|
| 单元测试 | 51 个，100% 通过 |
| 方案多样性（3 Agent） | 8,000+ 字，无同质化 |
| HTML 报告大小 | 约 117KB（零外部依赖） |
| API Key 配置 | 零手动，自动读取环境变量 |
| 缓存兼容性 | Reasonix 99% 命中率无损 |
| Skill 模式 | 零依赖，纯 Claude Code Agent |

## 十、竞品差异化

| 维度 | Collusion | Superpowers | OpenSpec | gstack |
|------|-----------|-------------|----------|--------|
| 多 Agent 协作 | ✅ 原生 | ❌ 单 Agent | ❌ | ❌ |
| 可行性强制收束 | ✅ | ❌ | ❌ | ❌ |
| 缺失环节自动补全 | ✅ | ❌ | ❌ | ❌ |
| 多平台适配 | ✅ | ❌ | ❌ | ✅ |
| 缓存无损集成 | ✅ | ❌ | ❌ | ❌ |
| 产出可视化报告 | ✅ HTML | ❌ | ❌ | ❌ |

Collusion 填补的空白：在执行之前，提供可靠的多视角技术决策支持，这是其他工具未覆盖的关键环节。

## 十一、社区与生态

- **开源协议**：MIT
- **分发渠道**：GitHub、PyPI、MCP Market
- **贡献指南**：Bug Report / Feature Request 模板，CONTRIBUTING.md
- **推广策略**：Reasonix 社区首发、技术博客、与 Superpowers/gstack 的联合演示
