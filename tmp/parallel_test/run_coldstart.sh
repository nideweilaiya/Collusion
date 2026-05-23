#!/bin/bash
# Parallel Skill 冷启动测试 v2
# - MCP已清空，防止Agent调用工具
# - 每个Agent独立system prompt + 唯一天task
# - 3 Agent并行 → 墙钟计时
set -e

TEST_ID="cold_$(date +%s)"
OUT_DIR="/d/BrainstormOrchestrator/tmp/parallel_test/$TEST_ID"
mkdir -p "$OUT_DIR"

# 唯一天任务
TASK="你是后端技术专家。请直接输出以下技术方案，不要使用任何工具或调用MCP。
设计一个团队协作Wiki知识库系统（TeamWiki）的完整后端技术方案：

需求：
1. Markdown实时协作编辑（类似Notion/Confluence）
2. 全文搜索 + 语义搜索
3. 文档版本历史/回滚
4. 三级权限管理（组织→空间→页面）
5. 目标：100人团队，10000+页面
6. REST API + WebSocket实时通知

请输出完整方案。
冷启动标记: $TEST_ID"

ARCH_SYSTEM="你是后端架构师。专注：API设计、数据库Schema、技术栈选型、系统可扩展性。
禁止使用任何MCP工具。禁止调用任何外部工具。直接输出纯文本Markdown方案。

输出要求：
## 1. 整体架构（ASCII art架构图）
## 2. 技术栈推荐（含理由）
## 3. 核心API设计（RESTful端点列表+请求/响应格式）
## 4. 数据库Schema（核心表结构+索引设计）
## 5. 扩展性与部署方案
用中文，具体可落地。冷启动: $TEST_ID"

SEC_SYSTEM="你是安全专家。专注：认证授权、注入防护、数据加密、审计日志。
禁止使用任何MCP工具。禁止调用任何外部工具。直接输出纯文本Markdown方案。

输出要求：
## 1. 认证方案（JWT vs Session vs OAuth2，推荐及理由）
## 2. 三级权限模型设计（数据模型+校验流程）
## 3. Web安全防护（XSS/CSRF/SQL注入/API限流）
## 4. 数据加密策略（传输TLS+存储加密）
## 5. 审计日志设计（事件定义+存储方案）
用中文，具体可落地。冷启动: $TEST_ID"

PERF_SYSTEM="你是性能分析师。专注：缓存策略、数据库优化、并发处理、搜索性能。
禁止使用任何MCP工具。禁止调用任何外部工具。直接输出纯文本Markdown方案。

输出要求：
## 1. 缓存分层策略（CDN→应用→数据库，含具体方案）
## 2. 数据库优化（索引设计、分表策略、读写分离、连接池）
## 3. 全文搜索方案对比（Elasticsearch vs PostgreSQL FTS vs Meilisearch → 推荐）
## 4. 实时协作并发控制（CRDT vs OT → 推荐方案+实现要点）
## 5. 容量规划（100人+10000页的资源估算）
用中文，具体可落地。冷启动: $TEST_ID"

echo "============================================"
echo "  Parallel Skill 冷启动测试 v2"
echo "============================================"
echo "测试ID: $TEST_ID"
echo "输出目录: $OUT_DIR"
echo "模型: deepseek-chat"
echo "MCP: 已清空（无MCP工具干扰）"
echo "模式: 3 Agent 并行 + 唯一天任务 → 全冷启动"
echo "开始时间: $(date '+%H:%M:%S')"
echo "============================================"
echo ""

START_TS=$(date +%s)

echo "启动 3 个 Agent (并行)..."
echo ""

# 并行启动，加 --no-config 避免读配置
npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$ARCH_SYSTEM" \
  --transcript "$OUT_DIR/architect.jsonl" \
  --no-config \
  > "$OUT_DIR/architect_output.md" 2>"$OUT_DIR/architect_stderr.log" &
PID_A=$!

npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$SEC_SYSTEM" \
  --transcript "$OUT_DIR/security.jsonl" \
  --no-config \
  > "$OUT_DIR/security_output.md" 2>"$OUT_DIR/security_stderr.log" &
PID_S=$!

npx reasonix run "$TASK" \
  -m deepseek-chat \
  --system "$PERF_SYSTEM" \
  --transcript "$OUT_DIR/performance.jsonl" \
  --no-config \
  > "$OUT_DIR/performance_output.md" 2>"$OUT_DIR/performance_stderr.log" &
PID_P=$!

echo "架构师 PID=$PID_A"
echo "安全专家 PID=$PID_S"
echo "性能分析师 PID=$PID_P"
echo ""
echo "等待中..."

wait $PID_A $PID_S $PID_P

END_TS=$(date +%s)
WALL=$((END_TS - START_TS))

echo ""
echo "============================================"
echo "  全部完成"
echo "============================================"
echo "墙钟时间: ${WALL}s ($((WALL / 60))m $((WALL % 60))s)"
echo "结束时间: $(date '+%H:%M:%S')"
echo ""

TOTAL_KB=0
TOTAL_LINES=0
for ROLE in architect security performance; do
  OUTPUT="$OUT_DIR/${ROLE}_output.md"
  TXT="$OUT_DIR/${ROLE}.jsonl"
  KB=0; LINES=0
  [ -f "$OUTPUT" ] && KB=$(du -k "$OUTPUT" | cut -f1) && LINES=$(wc -l < "$OUTPUT")
  TOTAL_KB=$((TOTAL_KB + KB))
  TOTAL_LINES=$((TOTAL_LINES + LINES))
  echo "=== $ROLE : ${KB}KB, ${LINES}行 ==="
  if [ -f "$TXT" ]; then
    npx reasonix stats "$TXT" 2>&1 || true
  fi
  echo ""
done
echo "总计: ${TOTAL_KB}KB, ${TOTAL_LINES}行"

{
  echo "test_id=$TEST_ID"
  echo "model=deepseek-chat"
  echo "wall_time=${WALL}s"
  echo "total_kb=${TOTAL_KB}"
  echo "total_lines=${TOTAL_LINES}"
} > "$OUT_DIR/summary.txt"

echo ""
echo "结果目录: $OUT_DIR"
