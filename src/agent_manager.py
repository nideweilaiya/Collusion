"""增强版 Agent 管理器 — 进程池管理、监控、自动重启和优雅关闭

解决 Reasonix 等单 Agent 宿主中后台 Agent 管理的问题：
- 进程池统一管理
- 进程监控和自动重启
- 优雅关闭和资源清理
- 线程模式备选方案（不支持子进程环境）
"""
import json
import os
import sys
import time
import uuid
import threading
import subprocess
import atexit
import signal
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum
from dataclasses import dataclass, field


BLACKBOARD_ROOT = Path.home() / ".collusion" / "blackboard"


class AgentStatus(Enum):
    """Agent 状态枚举"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    RESTARTING = "restarting"


class ExecutionMode(Enum):
    """执行模式"""
    PROCESS = "process"  # 子进程模式
    THREAD = "thread"    # 线程模式
    SYNC = "sync"        # 同步调用模式


@dataclass
class AgentProcess:
    """Agent 进程信息"""
    role: str
    task_id: str
    mode: str
    process: Optional[subprocess.Popen] = None
    thread: Optional[threading.Thread] = None
    status: AgentStatus = AgentStatus.IDLE
    start_time: Optional[float] = None
    last_heartbeat: Optional[float] = None
    restart_count: int = 0
    error_message: Optional[str] = None
    exit_code: Optional[int] = None


@dataclass
class AgentManagerConfig:
    """Agent 管理器配置"""
    # 进程管理
    max_restarts: int = 3
    restart_delay: float = 2.0
    heartbeat_timeout: float = 300.0  # 5 分钟心跳超时
    process_monitor_interval: float = 5.0
    
    # 执行模式
    default_execution_mode: ExecutionMode = ExecutionMode.PROCESS
    
    # 日志
    log_dir: Optional[Path] = None
    
    # 资源限制
    max_concurrent_agents: int = 10


class AgentManager:
    """增强版 Agent 管理器"""
    
    def __init__(self, config: Optional[AgentManagerConfig] = None):
        self.config = config or AgentManagerConfig()
        self._agents: Dict[str, AgentProcess] = {}  # key: f"{task_id}:{role}"
        self._lock = threading.Lock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._shutdown = False
        
        # 确保日志目录存在
        if self.config.log_dir:
            self.config.log_dir.mkdir(parents=True, exist_ok=True)
        
        # 注册退出钩子
        atexit.register(self.shutdown)
        signal.signal(signal.SIGTERM, self._signal_handler)
        signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        print(f"[AgentManager] 收到信号 {signum}，正在关闭...")
        self.shutdown()
    
    def start_monitor(self):
        """启动进程监控线程"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="AgentMonitor"
        )
        self._monitor_thread.start()
        print("[AgentManager] 进程监控已启动")
    
    def stop_monitor(self):
        """停止进程监控"""
        self._monitor_stop.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=10.0)
    
    def _monitor_loop(self):
        """监控循环"""
        while not self._monitor_stop.is_set():
            try:
                self._check_agents()
            except Exception as e:
                print(f"[AgentManager] 监控循环异常: {e}")
            
            self._monitor_stop.wait(self.config.process_monitor_interval)
    
    def _check_agents(self):
        """检查所有 Agent 状态"""
        now = time.time()
        
        with self._lock:
            for key, agent in list(self._agents.items()):
                if agent.status in (AgentStatus.STOPPED, AgentStatus.IDLE):
                    continue
                
                # 检查进程是否还在运行
                if agent.status == AgentStatus.RUNNING:
                    is_alive = self._is_agent_alive(agent)
                    
                    if not is_alive:
                        # 进程已死，尝试重启
                        self._handle_agent_death(agent, key)
                    else:
                        # 更新心跳
                        agent.last_heartbeat = now
                
                # 检查心跳超时
                elif agent.status == AgentStatus.RUNNING and agent.last_heartbeat:
                    if now - agent.last_heartbeat > self.config.heartbeat_timeout:
                        print(f"[AgentManager] Agent {agent.role} 心跳超时，正在重启...")
                        self._restart_agent(agent, key)
    
    def _is_agent_alive(self, agent: AgentProcess) -> bool:
        """检查 Agent 是否存活"""
        if self.config.default_execution_mode == ExecutionMode.PROCESS:
            if agent.process:
                return agent.process.poll() is None
            return False
        elif self.config.default_execution_mode == ExecutionMode.THREAD:
            if agent.thread:
                return agent.thread.is_alive()
            return False
        return False
    
    def _handle_agent_death(self, agent: AgentProcess, key: str):
        """处理 Agent 死亡"""
        # 获取退出代码
        if agent.process:
            agent.exit_code = agent.process.poll()
        
        agent.status = AgentStatus.ERROR
        agent.error_message = f"进程异常退出 (code: {agent.exit_code})"
        
        print(f"[AgentManager] Agent {agent.role} 死亡: {agent.error_message}")
        
        # 尝试重启
        if agent.restart_count < self.config.max_restarts:
            self._restart_agent(agent, key)
    
    def _restart_agent(self, agent: AgentProcess, key: str):
        """重启 Agent"""
        agent.status = AgentStatus.RESTARTING
        agent.restart_count += 1
        
        print(f"[AgentManager] 正在重启 Agent {agent.role} (第 {agent.restart_count} 次)...")
        
        # 先清理旧进程
        self._cleanup_agent(agent)
        
        # 等待一段时间再重启
        time.sleep(self.config.restart_delay)
        
        # 重新启动
        try:
            self._spawn_agent_internal(agent)
            print(f"[AgentManager] Agent {agent.role} 重启成功")
        except Exception as e:
            agent.status = AgentStatus.ERROR
            agent.error_message = f"重启失败: {e}"
            print(f"[AgentManager] Agent {agent.role} 重启失败: {e}")
    
    def spawn_agents(
        self,
        task_id: str,
        roles: List[str],
        mode: str,
        execution_mode: Optional[ExecutionMode] = None
    ) -> Dict[str, AgentProcess]:
        """启动多个 Agent
        
        Args:
            task_id: 任务 ID
            roles: 角色列表
            mode: 执行模式 (proposal/review/brake/integrate/vote)
            execution_mode: 执行模式（进程/线程/同步）
        
        Returns:
            AgentProcess 字典，key 为 role
        """
        exec_mode = execution_mode or self.config.default_execution_mode
        result = {}
        
        with self._lock:
            for role in roles:
                key = f"{task_id}:{role}"
                agent = AgentProcess(
                    role=role,
                    task_id=task_id,
                    mode=mode
                )
                self._agents[key] = agent
                
                try:
                    self._spawn_agent_internal(agent, exec_mode)
                    result[role] = agent
                except Exception as e:
                    agent.status = AgentStatus.ERROR
                    agent.error_message = str(e)
                    print(f"[AgentManager] 启动 Agent {role} 失败: {e}")
        
        # 启动监控
        self.start_monitor()
        
        return result
    
    def _spawn_agent_internal(
        self,
        agent: AgentProcess,
        execution_mode: Optional[ExecutionMode] = None
    ):
        """内部方法：启动单个 Agent"""
        exec_mode = execution_mode or self.config.default_execution_mode
        agent.status = AgentStatus.STARTING
        agent.start_time = time.time()
        agent.last_heartbeat = time.time()
        
        if exec_mode == ExecutionMode.PROCESS:
            self._spawn_agent_process(agent)
        elif exec_mode == ExecutionMode.THREAD:
            self._spawn_agent_thread(agent)
        else:
            self._spawn_agent_sync(agent)
    
    def _spawn_agent_process(self, agent: AgentProcess):
        """子进程模式启动 Agent"""
        agent_script = Path(__file__).parent / "child_agent.py"
        
        cmd = [
            sys.executable,
            str(agent_script),
            "--task-id",
            agent.task_id,
            "--role",
            agent.role,
            "--mode",
            agent.mode,
            "--blackboard",
            str(BLACKBOARD_ROOT),
        ]
        
        # 设置启动信息
        startupinfo = None
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
        
        # 重定向输出
        stdout = stderr = None
        if self.config.log_dir:
            log_file = self.config.log_dir / f"{agent.task_id}_{agent.role}_{agent.mode}.log"
            stdout = open(log_file, "w", encoding="utf-8")
            stderr = subprocess.STDOUT
        
        try:
            agent.process = subprocess.Popen(
                cmd,
                stdout=stdout,
                stderr=stderr,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            agent.status = AgentStatus.RUNNING
            print(f"[AgentManager] Agent {agent.role} 进程已启动 (PID: {agent.process.pid})")
        except Exception as e:
            if stdout:
                stdout.close()
            raise
    
    def _spawn_agent_thread(self, agent: AgentProcess):
        """线程模式启动 Agent"""
        # 导入子 Agent 模块
        from src import child_agent
        
        def _agent_worker():
            try:
                agent.status = AgentStatus.RUNNING
                # 模拟子 Agent 执行
                agent_dir = BLACKBOARD_ROOT / agent.task_id / "agents" / agent.role
                task_path = BLACKBOARD_ROOT / agent.task_id / "task.json"
                
                if not task_path.exists():
                    child_agent.update_status(agent_dir, phase="error", error="task.json not found")
                    return
                
                task_data = json.loads(task_path.read_text(encoding="utf-8"))
                child_agent.update_status(agent_dir, phase=f"{agent.mode}_start")
                
                # 加载适配器
                from src.llm.deepseek import DeepSeekAdapter
                adapter = DeepSeekAdapter(model="deepseek-chat")
                
                # 执行对应模式
                handler = child_agent.MODES.get(agent.mode)
                if handler:
                    handler(adapter, task_data, agent_dir, agent.role)
                
                agent.status = AgentStatus.STOPPED
            except Exception as e:
                agent.status = AgentStatus.ERROR
                agent.error_message = str(e)
                print(f"[AgentManager] Agent {agent.role} 线程异常: {e}")
        
        agent.thread = threading.Thread(
            target=_agent_worker,
            daemon=True,
            name=f"Agent-{agent.role}-{agent.task_id}"
        )
        agent.thread.start()
        print(f"[AgentManager] Agent {agent.role} 线程已启动")
    
    def _spawn_agent_sync(self, agent: AgentProcess):
        """同步模式（直接调用，不适合并行）"""
        # 这种模式主要用于测试和调试
        agent.status = AgentStatus.RUNNING
        print(f"[AgentManager] Agent {agent.role} 同步模式启动")
    
    def wait_for_agents(
        self,
        task_id: str,
        timeout: float = 300.0,
        poll_interval: float = 2.0
    ) -> Tuple[bool, Dict[str, AgentProcess]]:
        """等待 Agent 完成
        
        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔
        
        Returns:
            (是否全部成功, Agent 字典)
        """
        start_time = time.time()
        agent_keys = [k for k in self._agents.keys() if k.startswith(f"{task_id}:")]
        
        while time.time() - start_time < timeout:
            all_done = True
            any_error = False
            
            with self._lock:
                for key in agent_keys:
                    agent = self._agents.get(key)
                    if not agent:
                        continue
                    
                    if agent.status == AgentStatus.ERROR:
                        any_error = True
                    elif agent.status != AgentStatus.STOPPED:
                        # 检查文件系统状态作为补充
                        if self._check_agent_file_status(task_id, agent.role, agent.mode):
                            agent.status = AgentStatus.STOPPED
                        else:
                            all_done = False
            
            if all_done:
                break
            
            time.sleep(poll_interval)
        
        # 收集结果
        result = {}
        with self._lock:
            for key in agent_keys:
                if key in self._agents:
                    result[key.split(":")[1]] = self._agents[key]
        
        # 检查是否超时
        timed_out = time.time() - start_time >= timeout
        if timed_out:
            print(f"[AgentManager] 等待 Agent 完成超时 ({timeout}秒)")
        
        success = all(
            a.status in (AgentStatus.STOPPED, AgentStatus.IDLE)
            for a in result.values()
        )
        
        return success, result
    
    def _check_agent_file_status(self, task_id: str, role: str, mode: str) -> bool:
        """通过文件系统检查 Agent 是否完成"""
        agent_dir = BLACKBOARD_ROOT / task_id / "agents" / role
        status_file = agent_dir / "status.json"
        
        if not status_file.exists():
            return False
        
        try:
            status = json.loads(status_file.read_text(encoding="utf-8"))
            phase = status.get("phase", "")
            return phase.endswith("_done") or phase.endswith("_skipped") or phase == "error"
        except Exception:
            return False
    
    def stop_agent(self, task_id: str, role: str):
        """停止单个 Agent"""
        key = f"{task_id}:{role}"
        
        with self._lock:
            agent = self._agents.get(key)
            if not agent:
                return
            
            print(f"[AgentManager] 正在停止 Agent {role}...")
            agent.status = AgentStatus.STOPPING
            self._cleanup_agent(agent)
            agent.status = AgentStatus.STOPPED
    
    def stop_task_agents(self, task_id: str):
        """停止任务的所有 Agent"""
        agent_keys = [k for k in self._agents.keys() if k.startswith(f"{task_id}:")]
        
        for key in agent_keys:
            role = key.split(":")[1]
            self.stop_agent(task_id, role)
    
    def _cleanup_agent(self, agent: AgentProcess):
        """清理 Agent 资源"""
        # 清理进程
        if agent.process:
            try:
                if agent.process.poll() is None:
                    # 先尝试优雅终止
                    agent.process.terminate()
                    try:
                        agent.process.wait(timeout=5.0)
                    except subprocess.TimeoutExpired:
                        # 强制杀死
                        agent.process.kill()
                        agent.process.wait()
            except Exception as e:
                print(f"[AgentManager] 清理进程异常: {e}")
            finally:
                agent.process = None
        
        # 清理线程（线程无法强制停止，只能等待）
        if agent.thread and agent.thread.is_alive():
            # 线程无法强制停止，这里只能打个日志
            print(f"[AgentManager] Agent {agent.role} 线程仍在运行，等待自然退出")
        
        agent.process = None
        agent.thread = None
    
    def get_agent_status(self, task_id: str, role: str) -> Optional[AgentProcess]:
        """获取 Agent 状态"""
        key = f"{task_id}:{role}"
        with self._lock:
            return self._agents.get(key)
    
    def get_task_status(self, task_id: str) -> Dict[str, dict]:
        """获取任务所有 Agent 的状态摘要"""
        agent_keys = [k for k in self._agents.keys() if k.startswith(f"{task_id}:")]
        result = {}
        
        with self._lock:
            for key in agent_keys:
                agent = self._agents[key]
                result[agent.role] = {
                    "status": agent.status.value,
                    "start_time": agent.start_time,
                    "last_heartbeat": agent.last_heartbeat,
                    "restart_count": agent.restart_count,
                    "error": agent.error_message,
                    "exit_code": agent.exit_code,
                }
        
        return result
    
    def shutdown(self):
        """关闭管理器，清理所有资源"""
        if self._shutdown:
            return
        
        print("[AgentManager] 正在关闭...")
        self._shutdown = True
        
        # 停止监控
        self.stop_monitor()
        
        # 停止所有 Agent
        with self._lock:
            for agent in self._agents.values():
                if agent.status in (AgentStatus.RUNNING, AgentStatus.STARTING):
                    self._cleanup_agent(agent)
                    agent.status = AgentStatus.STOPPED
        
        print("[AgentManager] 已关闭")


# 全局单例
_instance: Optional[AgentManager] = None
_instance_lock = threading.Lock()


def get_agent_manager(config: Optional[AgentManagerConfig] = None) -> AgentManager:
    """获取 AgentManager 单例"""
    global _instance
    
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = AgentManager(config)
    
    return _instance