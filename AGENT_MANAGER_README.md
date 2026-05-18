# 增强版 Agent 管理器使用说明

## 概述

增强版 Agent 管理器解决了在 DeepSeek Reasonix 等单 Agent 宿主环境中后台 Agent 管理困难的问题。

## 主要改进

### 1. 统一的 Agent 生命周期管理

- **进程监控**：自动检测并重启异常退出的 Agent
- **优雅关闭**：使用 `atexit` 和信号处理确保资源清理
- **心跳超时**：检测并重启无响应的 Agent

### 2. 多种执行模式

系统支持三种执行模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `process` (默认) | 独立子进程运行 | 资源隔离要求高的环境 |
| `thread` | 同一进程的线程运行 | 受限环境（如 Reasonix） |
| `sync` | 同步调用 | 调试和测试 |

### 3. 进程管理特性

- 自动重启（最多 3 次）
- 进程监控线程
- 日志记录（可选）
- PID 跟踪和管理

## 使用方法

### 环境变量配置

通过环境变量选择执行模式：

```bash
# 线程模式（推荐在 Reasonix 中使用）
export COLLUSION_EXECUTION_MODE=thread

# 进程模式（默认）
export COLLUSION_EXECUTION_MODE=process
```

### 编程方式使用

```python
from src.blackboard import BlackboardOrchestrator
from src.agent_manager import ExecutionMode

# 线程模式（适合受限环境）
orchestrator = BlackboardOrchestrator(
    execution_mode=ExecutionMode.THREAD
)

# 进程模式（默认）
orchestrator = BlackboardOrchestrator(
    execution_mode=ExecutionMode.PROCESS
)

# 常规使用
task_id = orchestrator.create_task("设计一个高并发系统")
result = orchestrator.orchestrate_full(task_id)
```

### 直接使用 AgentManager

```python
from src.agent_manager import (
    get_agent_manager,
    AgentManagerConfig,
    ExecutionMode,
)

# 配置
config = AgentManagerConfig(
    max_restarts=3,
    heartbeat_timeout=300,  # 5 分钟
    log_dir=Path("/path/to/logs"),
    default_execution_mode=ExecutionMode.THREAD,
)

# 获取单例
manager = get_agent_manager(config)

# 启动 Agent
agents = manager.spawn_agents(
    task_id="my_task",
    roles=["ux", "architecture", "security"],
    mode="proposal",
)

# 等待完成
success, results = manager.wait_for_agents("my_task")

# 查看状态
status = manager.get_task_status("my_task")

# 停止
manager.stop_task_agents("my_task")
```

## MCP 服务器启动

### 线程模式（推荐用于 Reasonix）

```bash
export COLLUSION_EXECUTION_MODE=thread
python src/mcp_server.py --sse --port 8020
```

### 进程模式（默认）

```bash
export COLLUSION_EXECUTION_MODE=process
python src/mcp_server.py --stdio
```

## 文件结构

```
src/
├── agent_manager.py       # 新增：Agent 管理器
├── blackboard.py          # 已更新：使用新的 AgentManager
├── mcp_server.py          # 已更新：支持环境变量配置
└── child_agent.py         # 保持不变
```

## 测试

运行测试脚本：

```bash
python test_agent_manager.py
```

## 注意事项

1. **线程模式的限制**：
   - 不提供进程级别的隔离
   - 错误可能影响主进程
   - 但启动更快，资源消耗更低

2. **日志位置**：
   - 默认：`~/.collusion/blackboard/logs/`
   - 可通过 `AgentManagerConfig.log_dir` 配置

3. **Agent 状态说明**：
   - `idle`：空闲
   - `starting`：启动中
   - `running`：运行中
   - `stopping`：停止中
   - `stopped`：已停止
   - `error`：错误
   - `restarting`：重启中
