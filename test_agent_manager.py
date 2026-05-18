#!/usr/bin/env python3
"""
测试脚本：增强版 Agent 管理器
测试进程模式和线程模式的运行
"""

import os
import sys
import time
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent))

from src.blackboard import BlackboardOrchestrator
from src.agent_manager import ExecutionMode


def test_process_mode():
    """测试进程模式"""
    print("\n" + "="*60)
    print("测试进程模式 (Process Mode)")
    print("="*60)
    
    orchestrator = BlackboardOrchestrator(execution_mode=ExecutionMode.PROCESS)
    
    # 创建测试任务
    task_id = orchestrator.create_task(
        task_description="设计一个简单的待办事项应用",
        step_list=[]
    )
    print(f"创建任务: {task_id}")
    
    # 只运行第一阶段（快速测试）
    # 注意：为了避免调用真实 API，我们只测试启动和状态查询
    print("\n启动第一个阶段 (proposal)...")
    
    # 直接使用 AgentManager 测试
    from src.agent_manager import get_agent_manager
    manager = get_agent_manager()
    
    # 测试状态查询
    print(f"\n任务状态: {orchestrator.get_status(task_id)}")
    
    # 清理（不实际运行完整编排以节省 API 调用）
    print("\n测试完成，跳过完整编排以节省 API 调用")
    return task_id


def test_thread_mode():
    """测试线程模式"""
    print("\n" + "="*60)
    print("测试线程模式 (Thread Mode)")
    print("="*60)
    
    orchestrator = BlackboardOrchestrator(execution_mode=ExecutionMode.THREAD)
    
    # 创建测试任务
    task_id = orchestrator.create_task(
        task_description="设计一个简单的博客系统",
        step_list=[]
    )
    print(f"创建任务: {task_id}")
    
    # 测试状态查询
    print(f"\n任务状态: {orchestrator.get_status(task_id)}")
    
    print("\n测试完成")
    return task_id


def test_environment_variable():
    """测试环境变量配置"""
    print("\n" + "="*60)
    print("测试环境变量配置")
    print("="*60)
    
    print("\n设置环境变量示例:")
    print("  # 线程模式（适合受限环境）")
    print("  export COLLUSION_EXECUTION_MODE=thread")
    print("  python src/mcp_server.py --sse")
    print()
    print("  # 进程模式（默认，隔离性更好）")
    print("  export COLLUSION_EXECUTION_MODE=process")
    print("  python src/mcp_server.py --sse")


def main():
    """主测试函数"""
    print("Collusion 增强版 Agent 管理器测试")
    print("="*60)
    
    # 检查是否有 API key（可选）
    has_api_key = bool(
        os.environ.get("DEEPSEEK_API_KEY") or
        Path("config.json").exists()
    )
    print(f"\nAPI Key 状态: {'已配置' if has_api_key else '未配置'}")
    print("  (注意：完整测试需要 API Key)")
    
    # 运行测试
    if has_api_key:
        test_process_mode()
        test_thread_mode()
    else:
        print("\n跳过需要 API 的测试，仅演示功能")
    
    test_environment_variable()
    
    print("\n" + "="*60)
    print("所有测试完成！")
    print("="*60)


if __name__ == "__main__":
    main()
