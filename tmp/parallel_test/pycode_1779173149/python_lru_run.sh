#!/bin/bash
TASK=$(cat "D:/BrainstormOrchestrator/tmp/parallel_test/pycode_1779173149\python_lru_task.txt")
npx reasonix run "$TASK" -m deepseek-v4-flash --system "你是Python高级工程师。擅长并发编程、数据结构实现。输出可执行的完整代码。禁止使用任何MCP工具。pycode_1779173149" --no-config
