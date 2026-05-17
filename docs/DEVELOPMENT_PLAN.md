# Collusion (共谋) 完整开发计划书 v1.1

当前版本：v3.2（代码）/ v0.4.0（对外）
核心定位：一个在方案设计阶段，通过多对象协作防止过度设计和意图丢失的 MCP 引擎。

## 一、已完成的核心能力

| 模块 | 说明 | 版本 |
|------|------|------|
| 多对象代言并行提案 | 安全、性能、UX 三视角独立生成完整方案 | v3.0 |
| 环节共识与缺失补全 | 自动识别并补全用户需求中遗漏的关键环节 | v3.0 |
| 交叉审查 | 每个 Agent 轮流审查其他方案 | v3.0 |
| 可行性强制收束 | 工程对象代言人对过度设计做减法 | v3.0 |
| 多维度投票评分 | 5 维打分，输出 Top 3 | v3.0 |
| MCP Server | stdio + SSE 双传输 | v3.1 |
| 异步编排 | 避免 MCP 客户端 60s 超时 | v3.1 |
| 零配置 Key | 自动读取 Reasonix 已有 API Key | v3.1 |
| HTML/MD 双文件输出 | Jinja2 模板，SVG 雷达图，反馈 UI | v3.2 |
| 用户反馈回路 | collusion_refine，3 Agent 审查 + Owner 重整合 | v3.2 |
| 评分空值防御 | 方案为空时拒绝评分 | v3.2 |
| Benchmark 体系 | 5 领域盲评，16:1 胜出 | v3.0 |
| pyproject.toml + 命令入口 | `collusion-mcp` CLI | v0.4.0 |
| Skill 文件 | 5 种协作模式 (scheme/review/plan/diagnose/choose) | v0.4.0 |
| Mermaid 架构图 | 编排流程 + 方案架构分层图 | v0.4.0 |
| 代码入口锚点 | 方案中的文件路径自动提取 | v0.4.0 |
| MVP 自动检测 | 无依赖前 3 步标记为 MVP | v0.4.0 |
| Elicitation 引导交互 | 6 维度缺失检测 + brainstorm_elicit | v0.4.0 |
| 废案资产库与语义检索 | 自动索引 + brainstorm_search_assets | v0.4.0 |
| 会话分支与合并 | collusion_branch / collusion_merge | v0.4.0 |
| MCP Sampling 委托调用 | 保留宿主 99% 缓存命中率 | v0.4.0 |
| 平台配置模板 | Claude Code / Cursor / Reasonix / Trae Solo | v0.4.0 |
| 一键安装脚本 | setup.sh + setup.bat 自动检测宿主 | v0.4.0 |

## 二、开发路线图

### Phase 1：工程稳定性 ✅ 已完成

- [x] API Key 环境变量 + Reasonix 自动读取
- [x] 评分空值防御
- [x] 异步编排避免超时
- [x] 缓存友好化（全局 PREFIX）
- [x] 状态查询增强
- [x] MCP Sampling 委托调用

### Phase 2：Skill 化与分发 ✅ 已完成

- [x] MCP Server stdio + SSE
- [x] Skill 文件（5 种协作模式）
- [x] pyproject.toml + collusion-mcp 命令入口
- [x] 平台配置模板（4 平台）
- [x] 一键安装脚本
- [ ] 发布到 PyPI (`pip install collusion-mcp`)

### Phase 3：双文件输出系统 ✅ 已完成

- [x] Jinja2 模板引擎集成
- [x] HTML 报告模板（SVG 雷达图、对比表、风险卡片）
- [x] Markdown 技术文档（任务清单、修改历史）
- [x] 在线反馈回路完整闭环
- [x] Mermaid 架构分层图
- [x] 代码入口锚点
- [x] MVP 自动检测与提醒

### Phase 4：用户反馈与交互 ✅ 已完成

- [x] HTML 报告中嵌入修改输入区（textarea）
- [x] collusion_refine MCP 工具
- [x] MCP Elicitation 引导交互（brainstorm_elicit）

### Phase 5：方案资产化 ✅ 已完成

- [x] 废案资产库与语义检索（brainstorm_search_assets）
- [x] 会话分支与合并（collusion_branch / collusion_merge）

### Phase 6：生态融入与长期运营 🔴 进行中

- [x] Reasonix MCP 集成
- [x] GitHub 开源仓库
- [x] README/CONTRIBUTING/ROADMAP/LICENSE
- [ ] 发布到 PyPI
- [ ] MCP 市场提交（mcpservers.org, mcp.so）
- [ ] 联合工作流 Demo
- [ ] 社区文章与推广

### Phase 7：项目侦察 🟢 未来

- [ ] 项目索引层（侦察前输出文件清单）
- [ ] 并行侦察模式（3 Agent 按标签分配文件）
- [ ] 标签机制（共享侦察报告）
- [ ] 语义向量检索增强

### Phase 8：多模式协作 🟢 未来

- [ ] 模式 YAML 配置化
- [ ] collusion_enhance（增量增强已有方案）
- [ ] 动态 Agent 调度
- [ ] Agent 角色扩展（成本优化师、前端专家等）
- [ ] 社区角色市场

### Phase 9：原生多 Agent 协作 🟢 远期

- [ ] 3 Agent 护航模式
- [ ] 共享上下文引擎
- [ ] Agent 间通信协议
- [ ] 超时休眠机制

## 三、关键时间线

| 版本 | 核心交付 | 状态 |
|------|---------|------|
| v3.0-v3.1 | 核心编排引擎、benchmark 体系 | ✅ |
| v3.2 | HTML/MD 双输出、反馈回路 | ✅ |
| v0.4.0 | 资产库、分支合并、Sampling、Mermaid、Elicitation | ✅ |
| v0.5.0 | PyPI 发布、MCP 市场、动态 Agent 调度 | 📋 |
| v0.6.0 | 项目侦察、enhance 模式 | 📋 |
| v1.0.0 | 原生多 Agent 协作、社区角色市场 | 🎯 |

## 四、对外承诺

- **零依赖 HTML 报告**：离线可用
- **一句话安装**：`pip install collusion-mcp`
- **成本透明**：每次调用返回 Token 消耗和费用
- **过程可追溯**：保留修改历史、缺失补全记录、可行性收束决策
- **缓存无损**：在任何平台上都不破坏宿主原有的缓存机制
- **与执行工具无缝衔接**：输出格式与主流工具兼容
