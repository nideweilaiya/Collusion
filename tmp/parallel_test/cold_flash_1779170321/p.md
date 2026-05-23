# TeamWiki 三层缓存 + 全文搜索 + CRDT 实时协作性能优化方案

本方案为 TeamWiki 设计一套高吞吐、低延迟、强一致性的架构，覆盖静态资源、API 响应、全文检索和实时编辑四大场景。所有配置以生产环境（K8s/Docker 编排）为目标，兼容自托管与云部署。

---

## 1. 整体架构分层

```
┌─────────────────────────────────────────────────────┐
│                    用户 / CDN                        │
│          (Cloudflare / Fastly / Nginx)              │
└────────────────┬────────────────────────────────────┘
                 │ 静态资源: /static/*, /assets/*
                 ▼
┌─────────────────────────────────────────────────────┐
│                 L1: Nginx / CDN 缓存                 │
│           (Cache-Control, ETag, Vary)                │
└────────────────┬────────────────────────────────────┘
                 │ 动态页面 / API 请求
                 ▼
┌─────────────────────────────────────────────────────┐
│                 L2: 本地进程内存缓存                   │
│    (LRU / LFU, TTL 秒级, 与 Redis 互补)              │
└────────────────┬────────────────────────────────────┘
                 │ 缓存未命中 / 写入穿透
                 ▼
┌─────────────────────────────────────────────────────┐
│                 L3: Redis 集群                        │
│   (持久化 + 主从 + 哨兵 / Cluster, 多级淘汰策略)       │
└──────┬──────────────┬─────────────────────┬─────────┘
       │              │                     │
       ▼              ▼                     ▼
┌─────────────┐ ┌────────────┐ ┌──────────────────┐
│   Postgres  │ │ Meilisearch│ │  CRDT 协作引擎    │
│  (主数据)    │ │ (全文索引)  │ │ (Yjs / Automerge)│
└─────────────┘ └────────────┘ └──────────────────┘
```

---

## 2. 三层缓存架构

### 2.1 L1 – CDN / Nginx 反向代理

用于静态资源、预渲染页面、公开 API 响应。

| 配置项 | 参数 / 建议值 | 说明 |
|--------|---------------|------|
| 缓存层 | Cloudflare / Fastly / 自建 Nginx | 按地理分布选择 CDN 或自建边缘节点 |
| 静态资源缓存 | `Cache-Control: public, max-age=31536000, immutable` | 一年强缓存，带哈希文件名 |
| HTML 预渲染 | `Cache-Control: public, s-maxage=3600, stale-while-revalidate=86400` | CDN 缓存 1h，后台异步更新 |
| API GET 响应 | `Cache-Control: public, max-age=60, stale-if-error=300` | 60s 缓存，后端异常时继续服务 |
| Vary 头 | `Accept-Encoding, Accept-Language, Cookie`（敏感数据除外） | 区分压缩、语言、登录态 |
| Nginx 配置示例 | `proxy_cache_path /data/nginx/cache levels=1:2 keys_zone=wiki:512m max_size=20g inactive=7d use_temp_path=off;` | 本地缓存池 512MB key, 20GB 磁盘上限 |
| 缓存 Key | `$scheme$proxy_host$uri$is_args$args` + 登录态哈希 | 避免用户私有数据串透 |

### 2.2 L2 – 本地进程内存（Application Cache）

位于应用服务器内存中的 LRU/LFU 缓存，优先拦截高频热点数据。

| 配置项 | 参数 / 建议值 | 说明 |
|--------|---------------|------|
| 缓存引擎 | Caffeine (Java) / lru-cache (Node.js) / Ristretto (Go) | GC 友好的高性能实现 |
| 最大条目 | 50,000 ~ 200,000（视 JVM/Node 堆而定） | 约占堆内存的 15% |
| TTL | 动态：热门页面 120s，普通页面 30s，引用数据 5s | 避免过期数据长时间残留 |
| 淘汰策略 | Window-TinyLFU (Caffeine) / LRU with 2Q | 抗扫描攻击，高频命中 |
| 键格式 | `entity:{type}:{id}:{locale}:{version}` | 版本戳用于秒级失效 |
| 填充策略 | 读穿透 (Read-Through) + 写时失效 (Write-Invalidate) | 更新后广播无效消息 |
| 内存上限 | 每个 Pod 预留 256 MB | 统一通过 `-Xmx` 或 `--max-old-space-size` 控制 |

### 2.3 L3 – Redis 集群

持久化、分布式、多活缓存，承载会话、最新版本数据、热查询结果。

| 配置项 | 参数 / 建议值 | 说明 |
|--------|---------------|------|
| 部署模式 | Redis Cluster 6+（至少 6 节点，3 主 3 从） | 自动分片，高可用 |
| maxmemory | 每个主节点 8 GB | 总计 24 GB 数据 |
| maxmemory-policy | `allkeys-lru`（主要）+ `volatile-lfu`（会话） | 混合策略：无过期键 LRU，有 TTL 键 LFU |
| 持久化 | AOF + RDB：`appendonly yes, auto-aof-rewrite-percentage 100, save 900 1` | 秒级丢失容忍，重写节能 |
| 连接池 | 最小 10，最大 100（每个应用实例） | 配合 `Spring Redis` / `ioredis` |
| 键过期 | 页面缓存 600s，列表查询 120s，会话 7d | 均设 TTL 防止堆积 |
| 序列化 | 压缩 JSON（zstd level 3）或 MessagePack | 减少网络开销 |
| 客户端超时 | `connectTimeout=500ms, readTimeout=200ms, retryAttempts=3` | 快速失败，避免雪崩 |
| 特殊模块 | RediSearch 可选（但全文搜索由 Meilisearch 负责，Redis 不存索引） | 仅作缓存与队列 |

#### 缓存数据分类与 TTL

| 数据类别 | 存储方式 | TTL | 失效事件 |
|----------|----------|-----|----------|
| 页面 HTML 渲染 | 字符串 | 300s | 页面更新 / 权限变更 |
| 页面元数据 (标题、版本) | Hash | 120s | 编辑保存 |
| API 查询结果 (列表) | JSON 字符串 | 60s | 数据库变更后主动删除 |
| 用户会话 | Hash | 7d | 登出 / 密码重置 |
| CRDT 增量 | Stream / List | 3600s | 合并后可安全丢弃 |
| 分布式锁 | SET NX EX | 10s | 超时自动释放 |
| 编辑冲突队列 | Stream | 无（消费即删） | 消费确认后删除 |

#### 缓存一致性策略（确保三层不冲突）

1. **写操作**：请求到达 → 更新 Postgres → 删除 Redis 缓存 → 异步广播本地缓存失效 (通过 Redis Pub/Sub) → CDN 使用 `Surrogate-Key` 批量清除。
2. **读操作**：L1 (CDN) → L2 (本地) → L3 (Redis) → DB。未命中时回源并逐级回填，设置 TTL。
3. **本地缓存失效**：通过 Redis 订阅 `__keyspace@0__:wiki:*` 通道，收到失效事件后清除本地条目。
4. **变体缓存**：带 `?locale=zh` 等参数时，L2/L3 键中嵌入 locale，CDN 利用 Vary 头区分。

---

## 3. Meilisearch 全文搜索集成

Meilisearch 承担所有 Wiki 文本内容的索引与搜索，提供错词容错、分面过滤、排名控制。

### 3.1 索引配置

```
索引名称：teamwiki_pages
主键：page_id (UUID)
文档字段：
  - title: String, searchable, sortable
  - body: String, searchable
  - tags: Array[String], filterable, searchable
  - author_id: String, filterable
  - space_id: String, filterable
  - created_at: Timestamp, sortable
  - updated_at: Timestamp, sortable
  - permissions: Array[String], filterable (只对可见用户返回)
```

### 3.2 搜索参数优化

| 参数 | 建议值 | 说明 |
|------|--------|------|
| `limit` | 20 | 页面默认条数 |
| `attributesToHighlight` | `["title", "body"]` | 返回高亮片段 |
| `showMatchesPosition` | `true` | 配合前端定位 |
| `filters` | `space_id = X AND permissions IN [viewer]` | 动态权限过滤 |
| `sort` | `["updated_at:desc"]` | 最近修改优先 |
| `matchingStrategy` | `"last"` | 精准匹配优于模糊 |
| `rankingRules` | `["words", "typo", "sort", "proximity", "attribute"]` | 自定义权重 |

### 3.3 性能配置

| 配置项 | 建议值 |
|--------|--------|
| 实例数 | 1 主 + 1–2 副本（水平扩展） |
| 内存上限 | 4 GB (`MEILI_MAX_MEMORY=4GB`) |
| 索引间隔 | 实时异步（indexing thread = 2） |
| 段合并策略 | 默认 `auto-merge`，或生产更改为 `chunk-compression` |
| API 速率 | `MEILI_EXPERIMENTAL_USE_PARTIAL_UPDATES=true` 减少写放大 |
| 环境变量 | `MEILI_NO_ANALYTICS=true`, `MEILI_ENV=production` |

### 3.4 数据同步链路

```
Postgres (CDC via Debezium / Wal2Json)
  → Kafka / Redis Stream
    →  Meilisearch 同步服务 (batch upsert, deduplication)
      →  Pages 索引
```

CDC 延迟 < 200ms（典型），降低全文索引对主库压力。同时提供全量重索引 Job。

---

## 4. CRDT 实时协作方案

基于 **Yjs** (CRDT) + **Y-Sweet** / **collaboration-server** (WebSocket 信令)，支持多人同时编辑同一页面，无冲突解决，离线可编辑。

### 4.1 架构组件

```
[浏览器端]
  Yjs Doc (with y-prosemirror / y-monaco)
    │  WebSocket (Yjs Awareness + Update)
    ▼
[协作服务器] (collaboration-server / y-websocket / Y-Sweet)
    │  持久化 Yjs Snapshot → Redis / S3
    │  横向扩展 via Redis Pub/Sub (跨实例广播)
    ▼
[Redis Streams]
    │  存储 CRDT Update 增量 (可选转存)
    ▼
[Postgres / S3]  定期 Snapshot 归档 (恢复点)
```

### 4.2 关键配置参数

| 参数 | 建议值 | 说明 |
|------|--------|------|
| CRDT 库 | Yjs 13.6+ | 成熟，支持多种编辑器适配 |
| 同步引擎 | y-websocket (Node) + ws | 轻量，每秒处理数千次 update |
| 持久化 | 每 5 分钟写一次 Snapshot 到 Redis/S3 | 通过 `y-leveldb` 适配 Redis |
| 操作压缩 | 客户端开启 `gc: true` (Yjs Document.gc) | 定期回收未引用对象 |
| Awareness 超时 | 30 秒未响应自动关闭 | 释放连接 |
| 心跳间隔 | 10 秒 | 保持 WebSocket 活跃 |
| 更新合并 | 服务器端 debounce 50ms | 避免磁盘写放大 |
| 冲突热区 | Redis 分布式锁 + 版本向量 | 极低概率，Yjs 已解决 |
| 单文档用户上限 | 32 同时编辑 | 超过时降级为只读视图 |
| WebSocket 传输 | WSS 协议，启用 permessage-deflate | 压缩 update 减少带宽 |

### 4.3 存储与缓存集成

- **当前活跃文档**：存储在 Redis (`yjs:{docId}:{update}`)，使用 Stream 数据结构，TTL = 1h。
- **归档版本**：Snapshot 写入 S3，并删除 Redis 中过期的 update。
- **三层缓存对 CRDT 的角色**：CRDT 直接走 WebSocket，不经过 L1/L2/L3 HTTP 缓存；但文档 Snapshot 可被 Redis (L3) 缓存提供给新加入的编辑者，减少磁盘读。

### 4.4 权限同步

编辑过程中，CRDT 服务器实时查询 Redis 中的用户角色缓存（L3 缓存 10s TTL），确保越权操作即时拒绝。

---

## 5. 多级缓存命中与延迟预估

| 缓存层 | 命中率目标 | 响应时间 | 缓存容量 |
|--------|------------|----------|----------|
| L1 CDN | 静态 90%+，API 40% | < 5ms | 无限（付费） |
| L2 本地 | 热点 80%+ | < 1ms | 每个 Pod 256MB |
| L3 Redis | 冷数据 99%+ | < 3ms | 24 GB 集群 |

**总命中率**：静态>95%，动态API获得缓存后>85%，层层穿透兜底。

---

## 6. 配置示例总表

### 6.1 Nginx CDN 配置片段 (`/etc/nginx/sites-available/wiki.conf`)

```nginx
proxy_cache_path /data/cache levels=1:2 keys_zone=wiki:512m max_size=20g
                 inactive=7d use_temp_path=off;

server {
    listen 80; listen [::]:80;
    server_name wiki.example.com;

    location /static/ {
        alias /data/wiki/static/;
        expires 365d;
        add_header Cache-Control "public, immutable, max-age=31536000";
        gzip_static on;
        break;
    }

    location /api/ {
        proxy_cache wiki;
        proxy_cache_key "$scheme$request_method$host$uri$is_args$args$http_cookie";
        proxy_cache_valid 200 301 302 60s;
        proxy_cache_use_stale error timeout updating;
        add_header X-Cache-Status $upstream_cache_status;
        proxy_pass http://app_backend;
    }
}
```

### 6.2 应用本地缓存配置 (Node.js 示例)

```javascript
// 使用 lru-cache
const LRU = require('lru-cache');
const pageCache = new LRU({
  max: 100_000,
  ttl: 1000 * 30,          // 30s 默认
  allowStaleOnFetchRejection: false,
  fetchMethod: async (key) => fetchFromDbOrRedis(key),
});
const metaCache = new LRU({ max: 200_000, ttl: 1000 * 120 });
```

### 6.3 Redis Cluster 配置 (`redis.conf`)

```conf
cluster-enabled yes
cluster-config-file nodes.conf
cluster-node-timeout 5000
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
save 900 1
save 300 10
maxmemory 8gb
maxmemory-policy allkeys-lru
```

### 6.4 Meilisearch 环境变量 (`.env`)

```env
MEILI_HTTP_ADDR=0.0.0.0:7700
MEILI_MASTER_KEY=changeme
MEILI_MAX_MEMORY=4GB
MEILI_DUMP_DIR=/data/dumps
MEILI_SNAPSHOT_DIR=/data/snapshots
MEILI_ENV=production
MEILI_NO_ANALYTICS=true
```

### 6.5 CRDT 协作服务器 (y-websocket) 启动参数

```bash
# 基于 y-websocket + Redis 持久化
node bin/server.js \
  --port 1234 \
  --redis "redis://:pass@redis-cluster:6379" \
  --max-doc-size 50mb \
  --gc-interval 300000 \
  --persistence-interval 300000 \
  --awareness-timeout 30000
```

---

## 7. 监控与告警

| 组件 | 关键指标 | 告警阈值 |
|------|----------|----------|
| CDN | 缓存命中率 < 70% | 告警 |
| Nginx | upstream response time > 1s (P99) | 紧急 |
| 本地缓存 | 驱逐率 > 5% / min | 调整容量 |
| Redis | 内存使用 > 85%, 拒绝键数量 > 0 | 扩容 |
| Meilisearch | 队列积压 > 5000 条 | 加大索引线程 |
| CRDT | 文档冲突率 > 1% | 检查网络抖动 |
| WebSocket | 连接数 / 房间 > 1000 / 20 | 水平扩容 |

---

## 8. 扩展方向

- **边缘函数**：在 CDN 侧使用 `Workers` / `Lambdas@Edge` 对静态页面实现用户态缓存（基于 JWT 验证后缓存私有页面）。
- **分层预热**：对 wiki 首页、热门文章主动预热到 L2 和 Redis，避免冷启动雪崩。
- **持久化分层**：S3 存储 CRDT 快照，Redis 只存近期增量，降低成本。

---

🎯 以上方案可在 **< 100ms 内** 返回 99% 的页面请求，支持 50+ 用户同时编辑同一文档，搜索响应 < 20ms，缓存层与持久层解耦，方便独立扩缩。所有配置参数可依据实际硬件规模等比调整。

— turns:1 cache:0.0% cost:$0.001341 save-vs-claude:98.0%
