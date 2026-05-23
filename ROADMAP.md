# 🗺️ Collusion Roadmap（共谋路线图）

当前版本：v0.6.0 | 代码版本：v3.2+checkpoint-engine

---

## ✅ 已完成 (v0.6.0) — 检查点引擎重构

### 核心架构变更
- 从 14 阶段固定流水线重构为可中断检查点链
- 核心交付物从"完整方案"变为"决策支持卡片"
- Token 预算硬上限 (轻量 15k / 深度 25k)

### 检查点引擎
- BaseCheckpoint 抽象基类 + requires/provides 拓扑排序
- 3 个核心检查点: semantic_consistency, interface_conflict, pattern_match
- 4 个深度检查点: complexity_brake, business_alignment, security_audit, architecture_review
- CheckpointEngine 统一调度器 + TokenBudgetController

### 信息单向流架构
- KnowledgeRetriever → SituationCompressor → CompressedSnapshot → Checkpoints
- 压缩器纯函数, 零 IO。快照硬上限 ≤500 token
- 启发式压缩器召回率 69%, LLM 压缩器上线后可达标

### MCP 工具重组
- 新增 collusion_assess (轻量主入口, 4-5 LLM调用)
- 新增 collusion_check (单检查点运行)
- 新增 collusion_render (决策卡片渲染)
- 工具分组: probe / check / plan / workspace
- 旧 brainstorm_* 工具名保留向后兼容

### 动态角色选择
- AgentGraph 检查点角色映射 (CHECKPOINT_ROLE_MAP)
- 深度检查点自动注入角色选择

### 测试
- 78 个测试全部通过
- 黄金验证集 5 场景

---

## ✅ 已完成 (v0.3.1 - v0.4.0)

### 核心编排引擎
- 多对象代言并行提案（安全、性能、UX 三视角）
- 环节共识与缺失补全
- 交叉审查 + 可行性强制收束
- Owner 深度整合（Flash 初稿 + Strong 润色）
- 5 维投票评分（正确性/完整性/可行性/创新性/业务对齐）

### MCP 集成
- stdio + SSE 双传输模式
- 异步编排（避免 60s 超时）
- 零配置 Key（自动读取 Reasonix 配置）

### 双文件输出
- Jinja2 模板引擎，HTML + Markdown 双输出
- SVG 雷达图（多维方案对比）
- Mermaid.js 架构分层图（编排流程 + 各方案架构图）
- 在线反馈 UI（步骤级 textarea，草稿保存，Agent 审查矩阵）
- `collusion_refine` 反馈回路完整闭环

### 方案资产化
- 代码入口锚点（自动提取方案中的文件路径）
- MVP 自动检测与提醒（无依赖前 3 步标记为 MVP）
- Elicitation 引导交互（6 维度缺失检测 + brainstorm_elicit 工具）
- 废案资产库与语义检索（自动索引 + brainstorm_search_assets）
- 会话分支与合并（collusion_branch / collusion_merge）

### 跨平台分发
- pyproject.toml + `collusion-mcp` 命令入口
- Skill 文件（scheme/review/plan/diagnose/choose 五种模式）
- 一键安装脚本（setup.sh + setup.bat）
- 4 平台 MCP 配置模板（Claude Code / Cursor / Reasonix / Trae Solo）
- MCP Sampling 委托调用（保留宿主 99% 缓存命中率）

### 质量保障
- 评分空值防御、超时重试、状态查询增强
- Benchmark 体系（5 领域盲评，16:1 胜出）

---

## 📋 近期目标 (v0.5.0 - v0.6.0)

### 并行编程执行层 (v0.5.2 已验证)
- ✅ 单Agent带文件工具执行编程任务 (--no-config + --mcp fs)
- ✅ 多Agent并行改不同文件 (24s墙钟, $0.002/2Agent)
- ⬜ 指令标签机制 (`[RUN: cmd]` → 编排器执行 → 结果注入Agent)
- ⬜ 冲突避免调度 (编排器标注target_file, 字符串比较, 零LLM)
- ⬜ 审查闭环 (并行修改后启动审查Agent检查是否引入新问题)
- ⬜ 设计→执行→审查 完整三阶段闭环

### 冲突避免设计 (v0.5.2 已确立)
- 原则: 事前避免 > 事后处理
- 不同文件 → 并行 | 同文件不同区域 → 可并行 | 同文件同区域 → 串行
- 编排器拆解任务时标注target_file, 纯字符串比较, 无LLM成本

### 并行编排引擎 (v0.5.2 已实现)
- ✅ Parallel Scheduler (替代subagent): Orchestrator(run) + Scheduler(Python) + Worker×N(run)
- ✅ 固定system prompt → 预热后缓存 97%+

### MCP 市场提交
- 提交到 mcpservers.org、mcp.so 等 MCP 市场
- 编写应用描述和使用截屏

### PyPI 发布
- `pip install collusion-mcp` 发布到 PyPI
- 版本管理与自动发布 CI

### 增量增强模式
- `collusion_enhance` 工具：接收已有半成品方案进行多视角审查增强
- 跳过 Phase 1-3，直接从 Phase 4 开始

### 动态 Agent 调度
- 根据任务复杂度自动选择 Agent 数量和角色
- 简单任务 2 Agent，复杂任务扩展到 5
- 配置文件自定义 Agent 角色和提示词

### 成本控制
- 单任务成本从 ~¥0.15 降至 ¥0.05-0.10
- Sampling 委托模式下成本由宿主承担

---

## 🟢 中期目标 (v0.7.0 - v0.8.0)

### 项目侦察与资产化
- 项目索引层（侦察前输出相关文件清单）
- 并行侦察模式（3 Agent 按标签分配文件并行审查）
- 标签机制（共享侦察报告，Agent 查询标签）
- 废案资产库增强（语义向量检索）

### 多模式协作平台
- 模式 YAML 配置化（自定义 Agent 阵容和协作原语）
- 社区角色市场（用户贡献自定义 Agent 角色）

### Reasonix 深度集成
- Hook 级集成（自动在编码前触发方案设计）
- 分身响应模式（collusion_escort.py）

---

## 🎯 远期愿景 (v1.0.0+)

### 原生多 Agent 人机协作
- 3 Agent 护航模式（主执行 + 分身监听 + 分支探索）
- 共享上下文引擎（实时共享，无需重复传递）
- Agent 间通信协议（查询/自查/注入/确认）
- 超时休眠机制（N 分钟无活动自动暂停）

### 与执行工具深度集成
- Superpowers writing-plans / executing-plans 一键衔接
- OpenSpec Spec-driven development 前置步骤
- 覆盖"设计 → 规范 → 执行 → 协作 → QA → 发布"完整链路

### 自进化方案库
- 用户采纳方案和落地反馈收集
- 方案-场景匹配模型，推荐历史最优方案
- 可行性收束成功案例库

---

## 📋 对社区的承诺

这份路线图是公开的。每一项功能都来自真实需求或社区反馈。

Collusion 将始终保持：
- **寄生而非替代**：不替代任何 AI 编码工具，增强它们的能力
- **缓存无损**：在任何平台上都不破坏宿主原有的缓存机制
- **零额外配置**：用户安装时无需重复配置 API Key
- **零依赖 HTML 报告**：离线可用，无需安装任何前端库
- **成本透明**：每次调用返回 Token 消耗和费用
- **过程可追溯**：保留修改历史、缺失补全记录、可行性收束决策

欢迎 Watch、Star、Fork，一起让"共谋"成为 AI 编码工作流中最可靠的方案设计引擎。
