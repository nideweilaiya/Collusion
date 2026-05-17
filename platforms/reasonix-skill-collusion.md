# Collusion Skill

当用户输入 `/collusion` 或 `/collusion scheme <任务>` 时触发。

## 执行流程

MCP Server 已配置为 stdio 模式，Reasonix 启动时自动拉起，无需手动操作。

### Step 1: 启动编排

调用 `brainstorm_orchestrate(task="<用户任务>", agents=3, format="html")`

立即告知用户：**"3 个 Agent 并行提案中，预计 2-4 分钟。"**

### Step 2: 等待并轮询

等待至少 60 秒后首次查询，之后每 30 秒查询一次：

调用 `brainstorm_status(task_id="...")`

向用户展示进度：
```
⏳ Phase 3/7: 并行提案中... (方案A ✅ 方案B ⏳ 方案C ⏳)
```

如果有 `pending_questions`（引导问题），向用户展示并等待回答。

### Step 3: 获取结果

当 phase 为 "done" 时，调用 `brainstorm_result(task_id="...")`

### Step 4: 展示结果

展示 Top 3 排名 + 方案概要：

```
🏆 推荐方案: 方案X — X.X分 | 一句话评语
📊 排名: 🥇X | 🥈Y | 🥉Z

📄 HTML 报告: http://localhost:8020/outputs/{task_id}/report.html

如需修改，直接告诉我具体要改的步骤即可。
```

## 子命令

| 命令 | 工具 | 说明 |
|------|------|------|
| `/collusion scheme <任务>` | brainstorm_orchestrate | 方案设计 |
| `/collusion status <id>` | brainstorm_status | 查询进度 |
| `/collusion result <id>` | brainstorm_result | 获取结果 |
| `/collusion enhance <方案>` | collusion_enhance | 增强已有方案 |
| `/collusion review <代码>` | collusion_review | 代码审查 |
| `/collusion plan <任务>` | collusion_plan | 任务拆解 |
| `/collusion diagnose <问题>` | collusion_diagnose | 问题诊断 |
| `/collusion choose A vs B` | collusion_choose | 技术选型 |
| `/collusion scout` | collusion_scout | 项目侦察 |
| `/collusion blackboard <任务>` | collusion_blackboard_start | 黑板+顾问模式 |

## 黑板模式

当用户输入 `/collusion blackboard <任务>` 时：

1. 调用 `collusion_blackboard_start(task="<任务>", model="hybrid")`
2. 告知用户：**"3 个子 Agent 在后台静默运行（架构师 R1 + 安全/UX Flash），预计 5-8 分钟"**
3. 用 `collusion_blackboard_status` 轮询进度
4. 如有 pending_queries，向用户展示并调用 `collusion_blackboard_answer` 回复
5. 全部完成后调用 `collusion_blackboard_merge` 获取最终方案

## 注意事项

- agents=3 是默认值，不要主动降为 1
- 如果用户说"快一点"或"--quick"，使用 agents=1 或 collusion_plan 工具
- MCP Server 空闲 10 分钟后自动关闭（节省资源）
- 所有工具调用错误时，检查 MCP Server 是否在运行
