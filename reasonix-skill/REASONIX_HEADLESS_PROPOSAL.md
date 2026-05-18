# Reasonix Headless 模式功能需求

## 一句话

新增 `--headless` 模式，让 `reasonix code` 可以被其他程序 (如 Collusion MCP Server) 作为子进程调用，传入任务并取回结果。

## 最小可行 API

```bash
# 单轮对话模式：传入任务 → 生成结果 → 写入文件 → 退出
reasonix code --headless --prompt "设计文件分享服务的存储方案" --output ./result.md

# 带角色注入（复用现有 --system-append/-F 参数）
reasonix code --headless \
  --system-append "你是一名安全专家。关注OWASP Top 10、认证授权、数据保护。" \
  --prompt "审查以下方案的安全漏洞: $(cat proposal.md)" \
  --output ./security_review.md

# 从 stdin 读取任务（兼容管道）
echo "审查以下代码" | reasonix code --headless --system-append-file ./role_security.md --output review.md
```

## 三个新参数

| 参数 | 类型 | 说明 |
|------|------|------|
| `--headless` | flag | 跳过 TUI 初始化，单轮对话后退出。exit code 0=成功, 1=出错 |
| `--prompt <text>` | string | 初始用户消息。如有 stdin 输入则拼接在末尾 |
| `--output <path>` | string | 最终回复写入文件（不含工具调用过程，只写模型文本回复） |

已有可复用参数：`--system-append`、`--system-append-file`、`--transcript`、`--model`、`--new`

## 内部改动范围

不需要改内核、缓存策略或 LLM 适配器。只需改入口层：

### 1. CLI 参数注册 (~10 行)
```
code-BZRNL62L.js (commander 定义处)
  新增 .option('--headless', 'non-interactive single-turn mode')
  新增 .option('--prompt <text>', 'initial user message')
  新增 .option('--output <path>', 'write final reply to file')
```

### 2. 启动分支 (~15 行)
```
code-BZRNL62L.js (主流程入口处)
  if (options.headless) {
    return runHeadless(options);  // 走 headless 路径
  }
  // 现有 TUI 路径不变
```

### 3. runHeadless() 函数 (~40 行)
```
async function runHeadless(options) {
  // 1. 初始化 LLM（复用现有逻辑）
  const adapter = createAdapter(options);

  // 2. 构建初始消息
  const prompt = options.prompt || readStdin();

  // 3. 单轮对话（复用现有 chat 模块，不需要 tool calling 循环）
  const reply = await adapter.chat([
    { role: "system", content: buildSystemPrompt(options) },
    { role: "user", content: prompt }
  ]);

  // 4. 写入输出
  if (options.output) {
    fs.writeFileSync(options.output, reply);
  } else {
    process.stdout.write(reply);
  }

  // 5. 退出
  process.exit(0);
}
```

### 总改动量估算

| 模块 | 新增 | 修改 | 说明 |
|------|------|------|------|
| CLI 参数定义 | ~12 行 | ~0 行 | 3 个新 option |
| headless 入口 | ~40 行 | ~3 行 | 新函数 + 分支判断 |
| 测试 | ~20 行 | ~0 行 | smoke test |
| **合计** | **~72 行** | **~3 行** | |

## 不改动的部分

- ❌ 不改缓存前缀结构
- ❌ 不改 LLM 适配器
- ❌ 不改会话管理
- ❌ 不改 MCP 协议
- ❌ 不改 TUI 渲染

## 为什么这对 Reasonix 有价值

1. **解锁"可组合 AI"生态** — 外部工具可以通过子进程调用 Reasonix 作为 LLM 后端，而不需要各自管理 API Key
2. **多 Agent 协作的前提** — Collusion 等多 Agent 框架借助 headless Reasonix 实现真正的多开并行，每个 Agent 独立维护缓存
3. **CI/CD 集成** — `reasonix code --headless --prompt "审查这次PR的安全问题" --output review.md` 可以放进 GitHub Actions
4. **零风险** — 不碰内核，不改缓存，不影响现有用户体验。一个 flag 加一个分支

## 调用时序对比

### 当前（无 headless）
```
MCP Server 需要 LLM 调用
  → MCP Sampling 委托 Reasonix 执行
  → Reasonix 主进程打断当前任务去执行
  → 返回结果
  → 问题：单进程，阻塞用户当前会话
```

### 加 headless 后
```
MCP Server 需要 LLM 调用
  → spawn: reasonix code --headless --prompt "..." --output /tmp/r1.md
  → 独立进程，独立缓存，不干扰主窗口
  → MCP Server 读 /tmp/r1.md 获取结果
  → 优势：完全并行，各自独立缓存
```
