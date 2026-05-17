# Collusion Skill for Reasonix

当用户提出**方案设计、技术选型、代码审查、任务拆解、问题诊断**等需求时，自动使用 Collusion MCP 工具。

## 触发条件（任一满足即触发）

- 用户说"设计一个XXX的方案"、"帮我设计XXX"
- 用户说"审查这段代码"、"review一下"
- 用户说"拆解这个任务"、"帮我规划XXX"
- 用户说"诊断这个问题"、"排查XXX"
- 用户说"A和B选哪个"、"技术选型"
- 用户说"看看这个项目"、"侦察一下"
- 用户说"用 Collusion"、"用共谋"

## 可用工具

| 工具 | 用途 | 何时用 |
|------|------|--------|
| brainstorm_orchestrate | 完整 7 阶段方案设计 | 用户需要从零设计方案 |
| collusion_enhance | 增强已有方案 | 用户提供半成品方案 |
| collusion_review | 多视角代码审查 | 用户贴代码要审查 |
| collusion_plan | 任务拆解 | 用户需要任务清单 |
| collusion_diagnose | 故障诊断 | 用户描述异常现象 |
| collusion_choose | 技术选型 | 用户比较多个方案 |
| collusion_scout | 项目侦察 | 用户要了解项目结构 |
| brainstorm_status | 查询编排进度 | - |
| brainstorm_result | 获取编排结果 | - |
| collusion_refine | 修改方案 | 用户对方案有修改意见 |
| collusion_blackboard_start | 黑板模式（3子Agent静默） | 复杂任务需要深度多视角 |
| collusion_blackboard_status | 黑板进度 | - |
| collusion_blackboard_merge | 黑板合并 | - |

## 执行要点

1. **方案设计时 agents=3**，不要降为 1。除非用户明确说"快一点"
2. **异步等待**：orchestrate 立即返回 task_id，用 status 轮询直到 phase=done
3. **展示结果**：拿到 result 后展示 Top 3 排名 + Top1 方案概要
4. **格式参数**：format="html" 生成可视化报告，format="md" 只生成文本（默认）
5. **预设参数**：preset="auto" 自动检测任务复杂度分配 Agent
