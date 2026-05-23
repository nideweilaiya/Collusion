# MC AI Companion Mod — 问题归档

> 最后更新: 2026-05-20
> 编译状态: ✅ 通过 (gradle build -x test)
> 部署位置: D:/.minecraft/1.20.4/versions/1204/mods/AICompanion.jar

---

## P0 — 核心功能阻断 (进游戏必现)

| ID | 问题 | 根因 | 修复状态 |
|----|------|------|---------|
| P0-1 | 采集模式: 技能无限循环「发现→完成」 | SkillEngine start→BreakBlock找不到→complete→GatherGoal立即重启 | ⚠️ 部分修复: 禁用SkillVerifier + 加200ms冷却。新BTreeGatherGoal有40tick冷却, 需实测 |
| P0-2 | BreakBlockAction 找不到目标方块 | AtomicAction 自己 findNearest() 搜索, 和 GatherGoal 找到的不是同一个 | 🔄 新架构已创建 TaskTarget 对象传递坐标, BTreeGatherGoal 已部署待验证 |
| P0-3 | 导航到目标后技能启动但挖不了 | 日志: 46次 BreakBlock "No matching blocks found" | 同上, TaskTarget 解决 |
| P0-4 | G键面板打开但按钮重叠 | 旧 QuickMenu 布局硬编码 140px 按钮 | ✅ 已修: 改120px+自适应边距 |
| P0-5 | 聊天框刷屏 | 3个来源: sendSuccess(true→广播)、showDialogue、LOGGER | ✅ 已修: sendSuccess false + 禁用showDialogue |

## P1 — 功能缺失 (有代码框架但未实现)

| ID | 问题 | 现有代码 | 修复状态 |
|----|------|---------|---------|
| P1-1 | 属性面板不存在 | CompanionDetailScreen(166行)只有3个按钮 | ❌ 未实现 |
| P1-2 | 拾取模式无命令 | CompanionBehaviorCommands 无 pickup | ✅ 已修: 已注册 pickup/auto 命令 |
| P1-3 | 自主模式无命令 | CompanionBehaviorCommands 无 auto | ✅ 已修: 已注册 |
| P1-4 | SkillScreen 未对接数据 | SkillScreen.java 存在但无数据 | ❌ 未实现 |
| P1-5 | AutoCurriculum 未接入主循环 | AutoCurriculum.java 存在 | ❌ 未接入 |
| P1-6 | FeedbackCollector 未实际使用 | FeedbackCollector.java 存在 | ❌ 未启用 |
| P1-7 | SkillLibrary 无法加载 | 日志: "Failed to load skills from <uuid>" | ❌ 文件持久化问题 |

## P2 — 性能/体验问题

| ID | 问题 | 严重度 | 说明 |
|----|------|--------|------|
| P2-1 | GuardGoal 每tick运行(322次) | 中 | 每秒20次空检查, 即使没敌人 |
| P2-2 | registerGoals() 被调用103次 | 低 | 应该只注册一次 |
| P2-3 | SkillVerifier 调用Ollama(46次) | 高 | 已禁用, 但仍留有日志 |
| P2-4 | VectorSkillLib 每次技能启动都搜索 | 低 | 68次搜索, 可以缓存 |

## P3 — 代码质量 (已修)

| ID | 问题 | 位置 | 修复 |
|----|------|------|------|
| P3-1 | 空catch块(13处) | MemoryStore/TCPServer | ✅ 加warn日志 |
| P3-2 | 硬编码温度值(8处) | 多处 | ✅ 改为OllamaClient常量 |
| P3-3 | 编译错误 AI/OllamaClient import | 5个文件 | ✅ 修复斜杠→点 |
| P3-4 | DialogueStack return null | DialogueStack.java | ✅ 恢复(测试需返回null) |
| P3-5 | SkillEngine 编译错误 | SkillEngine.java | ✅ 修复注释残留 |

## P4 — 新架构 (v2.0, 已部署)

| ID | 组件 | 文件 | 状态 |
|----|------|------|------|
| P4-1 | TaskTarget 对象 | task/TaskTarget.java | ✅ 已编译部署 |
| P4-2 | Navigator 抽象层 | task/Navigator.java | ✅ 已编译部署 |
| P4-3 | 行为树引擎 | btree/BehaviorNode.java | ✅ 已编译部署 |
| P4-4 | BTreeGatherGoal | entity/goal/BTreeGatherGoal.java | ✅ 已激活替换旧GatherGoal |

## P5 — 待设计决策

| ID | 议题 | 需要你决定 |
|----|------|-----------|
| P5-1 | Baritone 是否作为依赖引入？ | 需修改 build.gradle + 添加 mod 依赖 |
| P5-2 | 属性面板显示哪些属性？ | 等级/血量/攻击/防御/技能数？ |
| P5-3 | 自主模式的行为逻辑？ | 自动采集？自动守护？混合？ |
| P5-4 | 多同伴上限？ | 可以同时召唤几个同伴？ |

## 优先修复建议

```
明天继续:
  1. 进游戏测试 BTreeGatherGoal + TaskTarget 是否解决采集问题 ✓
  2. 查看最新日志 [BTreeGather] 前缀的输出
  3. 如果采集仍不行: BreakBlockAction 改用 TaskTarget 的坐标
  4. 如果采集好了: 修复 GuardGoal 频率 + AutoCurriculum 接入
  
下阶段:
  5. 属性面板 GUI (CompanionDetailScreen 显示数据)
  6. SkillScreen 对接 SkillLibrary
  7. Baritone 集成评估
```
