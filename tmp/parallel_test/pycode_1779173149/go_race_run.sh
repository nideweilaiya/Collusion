#!/bin/bash
TASK=$(cat "D:/BrainstormOrchestrator/tmp/parallel_test/pycode_1779173149\go_race_task.txt")
npx reasonix run "$TASK" -m deepseek-v4-flash --system "你是Go并发编程专家。精通race detector、memory model、sync包。先分析问题再输出修复代码。禁止使用任何MCP工具。pycode_1779173149" --no-config
