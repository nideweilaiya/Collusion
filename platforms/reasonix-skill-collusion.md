# Collusion Skill for Reasonix

**仅当用户明确提到「Collusion」或「共谋」时才激活此技能。**
普通的设计/审查请求不触发，保持 Reasonix 默认行为。

## 触发关键词（任一即触发）

- `Collusion`
- `共谋`
- `用 Collusion`
- `用共谋`

**不带这些关键词的请求 → 不触发，Reasonix 正常处理。**

## 触发后流程

### 1. 识别用户意图

从用户消息中提取：
- **任务描述**：用户想做什么？
- **模式**：scheme(方案设计) / review(审查) / plan(拆解) / diagnose(诊断) / choose(选型) / scout(侦察) / blackboard(黑板)

根据关键词判断模式：
- "设计/方案/scheme" → `brainstorm_orchestrate`
- "审查/review/检查代码" → `collusion_review`
- "拆解/plan/规划/任务" → `collusion_plan`
- "诊断/diagnose/排查/报错" → `collusion_diagnose`
- "选型/choose/选哪个/对比" → `collusion_choose`
- "侦察/scout/看看项目" → `collusion_scout`
- "黑板/blackboard/护航" → `collusion_blackboard_start`
- "增强/enhance/优化方案" → `collusion_enhance`

### 2. 自动检测 Agent 配置

使用 `brainstorm_orchestrate` 的 `preset` 参数：
- 不填 = `auto`（自动检测任务复杂度，智能分配 1-5 个 Agent）
- 用户说"快一点" = `quick`（1 Agent）
- 用户说"完整模式" = `full`（5 Agent）

### 3. 执行

```
brainstorm_orchestrate(task="用户任务", agents=3, format="html", preset="auto")
→ 返回 task_id
→ "3 个 Agent 并行提案中，预计 2-4 分钟"
→ 轮询 brainstorm_status
→ 完成 → brainstorm_result → 展示 Top 3
```

## 示例

```
用户: 用 Collusion 设计一个文件分享服务，支持过期时间和密码

→ 触发 ✓ (含"Collusion")
→ 模式: scheme
→ 调用 brainstorm_orchestrate(task="设计一个文件分享服务...", preset="auto")
→ 自动检测到"安全"关键词 → 安全Agent权重提升
→ 3 Agent 并行 → 交叉审查 → 投票 → 输出 Top 3
```

```
用户: 帮我设计一个API

→ 不触发 ✗ (不含"Collusion"或"共谋")
→ Reasonix 正常处理
```

## 可用的 Collusion MCP 工具

brainstorm_orchestrate, brainstorm_status, brainstorm_result,
collusion_enhance, collusion_review, collusion_plan,
collusion_diagnose, collusion_choose, collusion_scout,
collusion_refine, brainstorm_search_assets, brainstorm_elicit,
collusion_branch, collusion_merge,
collusion_blackboard_start, collusion_blackboard_status,
collusion_blackboard_answer, collusion_blackboard_merge
