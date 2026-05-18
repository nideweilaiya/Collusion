# Issue 草稿: 提议增加外部 MCP 工具用于多 Agent 方案设计

**目标仓库**: https://github.com/esengine/DeepSeek-Reasonix
**标签建议**: `feature-request`, `mcp`, `community`

---

## 标题

[Feature Request] 提议增加一个外部 MCP 工具用于多 Agent 并行方案设计 (Collusion)

## 正文

### 这是什么？

Collusion 是一个多 Agent 并行方案设计引擎，以 MCP Server 形式运行。给定一个技术任务，3 个 AI Agent（UX/产品专家、性能架构师、安全专家）从各自视角独立生成完整方案、交叉审查、可行性收束、投票输出 Top 3。

不是代码生成器 — 而是在写代码之前，把技术方案想清楚、想周全。

### 解决什么问题？

单 Agent 在做方案设计时常见三种问题：
1. **意图丢失** — AI 给出了看起来很完美的方案，但落地时发现违背了核心约束
2. **过度设计** — 缺乏工程视角审查，方案倾向于引入不必要的复杂性
3. **关键环节缺失** — 用户描述遗漏了安全、合规、迁移等环节，AI 不会主动追问

Collusion 通过多视角并行提案 + 交叉审查 + 可行性强制收束解决这些问题。

### 如何与 Reasonix 集成？

**MCP Sampling 委托模式** — 这是关键。

Collusion 不直接调用 DeepSeek API。所有 LLM 调用通过 MCP 的 `sampling/createMessage` 委托给 Reasonix 执行：

```
用户输入 → Reasonix → Collusion MCP 工具
  → Collusion 分解任务、调度 3 个子 Agent
  → 子 Agent 需要 LLM 时，通过 sampling/createMessage 请求 Reasonix 代劳
  → Reasonix 用自身 API Key + 缓存完成调用
  → 结果返回 Collusion 继续审查、收束、投票
  → 最终方案展示在 Reasonix 终端
```

### 对缓存的影响？

**零影响。** Reasonix 的会话缓存完全保留，命中率维持在 >90%。

原因：子 Agent 只接收任务摘要（不加载 Reasonix 的全量对话上下文），每次 LLM 调用都是 Reasonix 自己执行的，走的是 Reasonix 的缓存体系。这与其他 MCP 工具不同——它们通常会引入额外上下文导致缓存失效。

### Benchmark 数据

在 5 个领域、25 个维度的真实项目盲评中：
- 维度级对比：Collusion **16:1** 胜出（8 平）
- 方案完整性、创新性、业务对齐三个维度：全胜
- 单任务成本：¥0.04（1 Agent）~ ¥0.16（3 Agent 全 Flash）~ ¥0.30（混合）
- 3 Agent 并行产出方案约 32,000 字，无同质化

具体案例：为一个开源博客平台设计技术方案，Collusion（Go+SQLite 单二进制）vs 对照组（Next.js + 5 容器），Collusion 在部署门槛、外部依赖、架构务实性上全面优于对照组。

### 当前状态

- 代码开源: [github.com/nideweilaiya/Collusion](https://github.com/nideweilaiya/Collusion)
- 18 个 MCP 工具（方案设计、代码审查、任务拆解、问题诊断、技术选型、项目侦察、黑板模式）
- 51 单元测试 + 16 回归/冒烟测试，全部通过
- pip install 一键安装，Reasonix 用户零配置（自动读取 Reasonix API Key）

### 征求意见

1. 这个方向是否适合作为 Reasonix 的外部 MCP 工具推荐给社区？
2. 集成方式（MCP Sampling 委托）是否符合 Reasonix 的设计理念？
3. 如果方向 OK，我们会录制演示视频 + 整理 PR，以 Skill 形式提交到社区。

---

## 发布前检查

- [ ] 阅读 Reasonix CONTRIBUTING.md 确认 Issue 模板要求
- [ ] 附上真实 Benchmark 数据截图
- [ ] 在 Reasonix 中实测 `brainstorm_orchestrate` 端到端可用后截图/录屏
- [ ] 准备一段 1-2 分钟的演示视频链接
