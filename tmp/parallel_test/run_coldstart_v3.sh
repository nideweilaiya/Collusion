#!/bin/bash
# Parallel Skill 冷启动测试 v3
# 改进：每个Agent分配具体子任务（模拟编排器分配），完全唯一天prompt
set -e

TEST_ID="cold3_$(date +%s)_$(shuf -i 1000-9999 -n 1)"
OUT_DIR="/d/BrainstormOrchestrator/tmp/parallel_test/$TEST_ID"
mkdir -p "$OUT_DIR"

echo "============================================"
echo "  Parallel Skill 冷启动测试 v3"
echo "============================================"
echo "测试ID: $TEST_ID"
echo "模型: deepseek-chat"
echo "策略: 每Agent独立具体子任务 + 唯一prompt → 真冷启动"
echo "开始: $(date '+%H:%M:%S')"
echo ""

# ===== Agent 1: 架构师 — 数据库Schema =====
ARCH_TASK="为TeamWiki知识库系统设计完整的PostgreSQL数据库Schema。

TeamWiki背景：
- 团队协作Wiki，组织→空间→页面三级结构
- 支持Markdown文档，每个页面有版本历史
- 支持页面评论、@提及、标签分类
- 权限分三级：组织级(admin/member/viewer)、空间级(manage/edit/view)、页面级(edit/view)
- 目标100人团队，10000+页面

请用纯文本Markdown直接输出：
1. 所有核心表（5-8张表）的CREATE TABLE语句
2. 每张表的索引设计
3. 表之间的外键关系
4. 版本历史的存储策略（推荐做法）
不要使用任何工具或MCP。不要问问题。直接输出。$TEST_ID"

ARCH_SYSTEM="你是有10年经验的PostgreSQL DBA和数据库架构师。你精通数据库范式设计、索引优化、partition策略。
重要规则：
- 不调用任何MCP工具
- 不调用任何外部工具
- 直接用纯文本Markdown输出
- 不反问、不澄清，直接给方案
- 输出要具体到可以建表的SQL
冷启动唯一码: $TEST_ID"

# ===== Agent 2: 安全专家 — 认证+权限 =====
SEC_TASK="为TeamWiki知识库系统设计完整的认证和权限安全方案。

TeamWiki背景：
- SaaS多租户Wiki，企业团队使用
- 用户可通过邮箱注册、SSO(OIDC)登录
- 权限：组织级(owner/admin/member/viewer) → 空间级(manage/edit/view) → 页面级(edit/view)
- 需要审计日志追踪所有敏感操作
- API对外开放，需要防滥用

请用纯文本Markdown直接输出：
1. JWT认证方案：Token结构、刷新策略、吊销机制
2. 三级权限模型：数据表设计、中间件校验流程
3. XSS/CSRF/SQL注入/API限流 防护措施
4. 审计日志：事件分类、存储方案、保留策略
不要使用任何工具或MCP。不要问问题。直接输出。$TEST_ID"

SEC_SYSTEM="你是Web安全专家，专精OAuth2、JWT、OWASP Top 10防护、零信任架构。
重要规则：
- 不调用任何MCP工具
- 不调用任何外部工具
- 直接用纯文本Markdown输出
- 不反问、不澄清，直接给方案
- 输出要包含具体实现代码示例
冷启动唯一码: $TEST_ID"

# ===== Agent 3: 性能 — 缓存+搜索 =====
PERF_TASK="为TeamWiki知识库系统设计缓存和搜索性能方案。

TeamWiki背景：
- 100人团队同时编辑，10000+页面
- 页面Markdown内容平均50KB，部分页面被频繁读取
- 需要全文搜索（标题+内容+标签），毫秒级响应
- 实时协作编辑需要处理并发冲突
- 服务器: 4核16GB x 3节点

请用纯文本Markdown直接输出：
1. 三层缓存架构：具体选型(Redis/本地缓存/CDN)、失效策略、Key设计
2. 搜索方案：Meilisearch vs ES vs PG FTS对比 → 推荐Meilisearch方案+索引设计
3. 实时协作：CRDT Yjs方案 or OT → 推荐+实现要点
4. 容量规划：100人+10000页的内存/磁盘/带宽估算
不要使用任何工具或MCP。不要问问题。直接输出。$TEST_ID"

PERF_SYSTEM="你是后端性能优化专家，专精缓存架构、搜索引擎选型、大规模并发优化、容量规划。
重要规则：
- 不调用任何MCP工具
- 不调用任何外部工具
- 直接用纯文本Markdown输出
- 不反问、不澄清，直接给方案
- 输出要包含具体数字和配置参数
冷启动唯一码: $TEST_ID"

START_TS=$(date +%s)

echo "架构师 → 数据库Schema设计"
echo "安全专家 → 认证+权限+审计"
echo "性能专家 → 缓存+搜索+协作"
echo ""

npx reasonix run "$ARCH_TASK" \
  -m deepseek-chat \
  --system "$ARCH_SYSTEM" \
  --transcript "$OUT_DIR/architect.jsonl" \
  --no-config \
  > "$OUT_DIR/architect_output.md" 2>"$OUT_DIR/architect_stderr.log" &
PID_A=$!

npx reasonix run "$SEC_TASK" \
  -m deepseek-chat \
  --system "$SEC_SYSTEM" \
  --transcript "$OUT_DIR/security.jsonl" \
  --no-config \
  > "$OUT_DIR/security_output.md" 2>"$OUT_DIR/security_stderr.log" &
PID_S=$!

npx reasonix run "$PERF_TASK" \
  -m deepseek-chat \
  --system "$PERF_SYSTEM" \
  --transcript "$OUT_DIR/performance.jsonl" \
  --no-config \
  > "$OUT_DIR/performance_output.md" 2>"$OUT_DIR/performance_stderr.log" &
PID_P=$!

echo "PID: A=$PID_A S=$PID_S P=$PID_P"
echo "等待并行执行..."

wait $PID_A $PID_S $PID_P

END_TS=$(date +%s)
WALL=$((END_TS - START_TS))

echo ""
echo "============================================"
echo "  完成 — 墙钟: ${WALL}s ($((WALL / 60))m $((WALL % 60))s)"
echo "============================================"

TOTAL_KB=0
TOTAL_LINES=0
for ROLE in architect security performance; do
  OUTPUT="$OUT_DIR/${ROLE}_output.md"
  TXT="$OUT_DIR/${ROLE}.jsonl"
  KB=0; LINES=0
  [ -f "$OUTPUT" ] && KB=$(du -k "$OUTPUT" | cut -f1) && LINES=$(wc -l < "$OUTPUT")
  TOTAL_KB=$((TOTAL_KB + KB))
  TOTAL_LINES=$((TOTAL_LINES + LINES))
  echo ""
  echo "=== $ROLE : ${KB}KB, ${LINES}行 ==="
  head -3 "$OUTPUT" 2>/dev/null
  echo "  ..."
  if [ -f "$TXT" ]; then
    npx reasonix stats "$TXT" 2>&1 || true
  fi
done

echo ""
echo "============================================"
echo "总计: ${TOTAL_KB}KB, ${TOTAL_LINES}行, ${WALL}s墙钟"
echo "结果: $OUT_DIR"
echo "============================================"

{
  echo "test_id=$TEST_ID"
  echo "model=deepseek-chat"
  echo "wall_time=${WALL}s"
  echo "total_kb=${TOTAL_KB}"
  echo "total_lines=${TOTAL_LINES}"
} > "$OUT_DIR/summary.txt"
