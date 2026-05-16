# 贡献指南

感谢你有意贡献！Collusion 是一个多对象协作技术方案编排引擎，欢迎任何形式的参与。

## 如何贡献

### 报告 Bug

1. 使用 Bug Report 模板提交 Issue
2. 清晰描述：复现步骤、预期行为、实际行为
3. 附上相关日志或截图
4. 标注环境信息（OS、Python 版本、MCP 宿主）

### 提出新功能

1. 先搜索已有 Issue，确认没有重复
2. 使用 Feature Request 模板提交
3. 描述使用场景和期望效果
4. 如果有实现思路，欢迎一并描述

### 提交 PR

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 编写代码并通过测试
4. 保持 commit 信息简洁清晰
5. 提交 PR 到 `main` 分支
6. 在 PR 描述中说明改动动机和影响范围

## 开发环境

```bash
# 克隆
git clone https://github.com/your-username/Collusion.git
cd Collusion

# 安装依赖
pip install -r requirements.txt

# 配置 API Key
export DEEPSEEK_API_KEY="sk-xxxxxxxxxxxxxxxx"

# 运行测试
python -m pytest tests/ -v
```

## 代码风格

- Python：遵循 PEP 8
- 提示词：英文优先（全球 LLM 对英文 token 处理效率更高）
- 命名：内部使用 snake_case，对外 API 保持当前命名约定
- 不要在代码中添加冗余注释，让命名说明意图

## 项目架构

```
src/
├── orchestrator.py    # 核心编排引擎（状态机）
├── agents.py          # Agent 层（对象代言人）
├── scorer.py          # 多维度投票评分
├── prompts.py         # 系统提示词
├── models.py          # 数据模型（dataclass）
├── mcp_server.py      # MCP Server（stdio + SSE）
├── cache_prefix.py    # Prompt 缓存前缀
└── llm/               # LLM 适配器
    ├── base.py
    └── deepseek.py
```

编排流程：任务解构 → 环节共识 → 并行提案 → 交叉审查 → 可行性收束 → Owner 整合 → 投票评分

## 测试策略

- 单元测试：覆盖数据模型、编排逻辑（Mock LLM）
- 集成测试：使用真实 API 验证端到端流程
- 基准测试：`benchmark.py` 用于对比评估

## Issue 和 Discussion

- Bug 报告 → Issue（使用 Bug Report 模板）
- 功能建议 → Issue（使用 Feature Request 模板）
- 使用问题、想法讨论 → Discussions
- PR 相关讨论 → PR 评论区

## 行为准则

- 保持友善和专业
- 尊重不同观点和经验水平
- 接受建设性批评
- 关注对社区最有利的事
