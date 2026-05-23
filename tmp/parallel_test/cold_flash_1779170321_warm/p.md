# TeamWiki 三层缓存 + Meilisearch + CRDT 架构方案

## 1. 架构总览

```
                   ┌─────────────────────────────────────────────┐
                   │                  CDN                         │
                   │  (静态资源 / 页面快照 / 公开API响应)          │
                   └──────────┬────────────────────────────────┘
                              │ 回源 (Cache-Control / Surrogate-Key)
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          负载均衡 (Nginx / ALB)                     │
└──────────┬──────────────────────────────────┬──────────────────────┘
           │                                  │
           ▼                                  ▼
┌─────────────────────┐          ┌───────────────────────────┐
│  应用服务器(池)      │          │  WebSocket 服务器(池)      │
│  - 本地缓存(Caffeine)│          │  (Yjs sync, awareness)    │
│  - Redis Client      │          │  - Redis Pub/Sub          │
│  - Meilisearch Client│          └──────────┬────────────────┘
└──────────┬─────────────────────────────┘   │
           │                                  │
           ▼                                  ▼
┌─────────────────────┐          ┌───────────────────────────┐
│  Redis (缓存)        │◄─────────│  Redis (CRDT broadcast)   │
│  cluster / sentinel  │  pub/sub │  可能共用实例但不同db     │
│  - 页面对象缓存       │          │  - 房间频道              │
│  - 用户会话           │          └───────────────────────────┘
│  - 权限缓存           │
│  - 锁 / 计数器        │
└──────────────────────┘

┌──────────────────────┐
│  Meilisearch          │
│  - 索引: pages        │
│  - 搜索查询API        │
└──────────────────────┘

┌──────────────────────┐
│  持久存储 (PostgreSQL) │
│  作为最终一致性底座    │
└──────────────────────┘
```

---

## 2. 三层缓存设计

### 2.1 CDN 缓存层

**目标**：加速静态资源（JS/CSS/图片）以及可公开访问的页面快照（如已发布的、无需频繁变动的 wiki 页面）。  
**实现**：使用 Cloudflare、Akamai 或自建 Varnish。  
**缓存策略**：

| 资源类型 | Cache-Control 示例 | 备注 |
|--------|-------------------|------|
| 静态资源 (`.js`, `.css`, `.png`, `.svg`) | `public, max-age=31536000, immutable` | 文件指纹哈希 |
| API 响应（页面快照，公开 API） | `public, max-age=300, s-maxage=600, stale-while-revalidate=60` | 快捷类页面缓 5 分钟，CDN 边缘缓存 10 分钟 |
| 用户私有页面 | `private, no-cache` | 不经过 CDN 或标记为私有 |

**配置示例（Cloudflare）**：

- 缓存级别：`Standard`  
- 边缘缓存 TTL：根据 `Cache-Control`  
- 浏览器缓存 TTL：配置为 `Respect Existing Headers`  
- 缓存清除：通过 `purge_by_tags` 或 `purge_everything` 在页面发布/更新时触发
- 建议启用 **Brotli** 压缩、**HTTP/2** 和 **Argo Smart Routing**

---

### 2.2 Redis 缓存层

**目标**：存储频繁访问的动态数据，减少数据库查询。  
**数据划分**：

- **页面对象**：序列化的页面（标题、正文原文、元数据）  
- **用户会话**：JWT 黑名单、session 数据  
- **权限缓存**：用户-空间权限映射  
- **锁/计数器**：并发写保护，实时在线人数  
- **CRDT 广播通道**（见第 5 节）

**部署模式**：Redis Cluster (6 节点以上) 或 Sentinel (主+2从) 基于可用性要求。  
**配置参数（redis.conf / 环境变量）**：

```bash
# 内存管理
maxmemory 4gb
maxmemory-policy allkeys-lru
maxmemory-samples 10

# 持久化（只做缓存可关闭 RDB/AOF，但为了恢复建议 AOF appendfsync everysec）
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# 网络与连接
tcp-backlog 511
timeout 0
tcp-keepalive 300

# 客户端限制
maxclients 10000

# 用于 CRDT 广播：开启 keyspace notifications 或单独 pub/sub 通道
notify-keyspace-events ""   # 不需 keyspace event，CRDT 用独立 pub/sub
```

**集群模式下**：每节点 `maxmemory 2gb`，`cluster-node-timeout 5000`，`cluster-require-full-coverage no`

**连接池示例（应用端，Java/Golang 等）**：

| 参数 | 值 |
|------|-----|
| 最大连接数 | 64 （每应用实例） |
| 最小空闲连接 | 8 |
| 连接超时 | 3s |
| 命令超时 | 500ms |
| Redis 客户端类型 | Lettuce（异步）或 go-redis |

---

### 2.3 本地缓存（应用内存）

**目标**：缓存热点只读数据（如已渲染的页面摘要、配置信息），进一步降低网络开销。  
**实现**：  
- Java: Caffeine Cache  
- Go: freecache / bigcache  
- Node.js: node-cache / lru-cache  

**配置示例（Caffeine）**：

```java
Cache<String, PageSummary> localCache = Caffeine.newBuilder()
    .initialCapacity(10000)
    .maximumSize(50000)
    .expireAfterWrite(5, TimeUnit.MINUTES)
    .expireAfterAccess(1, TimeUnit.HOURS)
    .recordStats()
    .build();
```

**规则**：

- 只缓存**读频繁、写极少**的数据（如页面渲染后的 HTML 片段、用户摘要）  
- 写操作（编辑、创建、删除）立即 `invalidate` 对应本地 key  
- 配合 Redis 使用：先查本地，miss 再查 Redis，再查 DB  
- 本地缓存容量不宜过大，防止 JVM heap 压力（建议 ≤ 最大堆的 5%）

---

### 2.4 缓存策略与失效

| 操作 | CDN | Redis | Local |
|------|-----|-------|-------|
| 页面读取（用户） | 可能命中 | 缓存页面对象 | 缓存渲染结果 |
| 页面创建/保存 | 清除该页面 URL 和相关聚合页 | 更新/设置新值 | 失效对应 key |
| 页面删除 | 清除 | 删除 & 通知 | 失效 |
| 权限变更 | – | 更新用户权限缓存 | 失效用户权限本地副本 |
| 全局设置更新 | – | 更新 | 失效部分本地配置 |

**失效机制**：

- **写操作结束后**：通过消息队列（Redis Pub/Sub / RabbitMQ）广播 `cache.invalidate { type, id }`  
- 所有应用服务器监听该频道，主动清除本地缓存  
- CDN：通过 REST API（如 Cloudflare Purge）批量清除关键路径

---

## 3. Meilisearch 全文搜索

### 3.1 部署与基础配置

**推荐版本**：v1.10+  
**部署**：单实例（小型团队）或三节点集群（HA，使用 Meilisearch Cloud 或官方 Docker Compose）  

**环境变量 / 配置文件**：

```toml
# meilisearch.toml
env = "production"
http_addr = "0.0.0.0:7700"
master_key = "your-generated-key"

# 索引存储路径
db_path = "/data/meili_data"

# 内存：占用总物理内存 50%（根据服务器调整）
max_indexing_memory = "4 GiB"
max_indexing_threads = 2

# 搜索限制：分页大小
pagination_max_total_hits = 1000
```

### 3.2 索引定义

```
Index: pages
Primary key: id (string, UUID)
```

**可搜索属性** (`searchableAttributes`) | **过滤属性** (`filterableAttributes`) | **排序属性** (`sortableAttributes`)  
---|---|---  
`title` `content` `tags` | `spaceId` `authorId` `isDeleted` `visibility` | `updatedAt` `createdAt`

**索引设置（API 或仪表盘）**：

```json
{
  "uid": "pages",
  "primaryKey": "id",
  "searchableAttributes": ["title", "content", "tags"],
  "filterableAttributes": ["spaceId", "authorId", "isDeleted", "visibility"],
  "sortableAttributes": ["updatedAt", "createdAt"],
  "rankingRules": [
    "words",
    "typo",
    "proximity",
    "attribute",
    "sort",
    "exactness"
  ],
  "typoTolerance": {
    "enabled": true,
    "minWordSizeForTypos": { "oneTypo": 5, "twoTypos": 9 },
    "disableOnAttributes": []
  },
  "pagination": {
    "maxTotalHits": 1000
  }
}
```

### 3.3 数据同步流

```
应用编辑 → 保存 DB → 更新 Redis → 发送索引消息 (RabbitMQ / Redis Stream)
   → 索引 worker 消费 → Meilisearch add/replace documents
```

**批量处理**：每秒可处理 100 条更新（默认），通过 `batchSize=100, waitTimeMs=500` 配置。

### 3.4 搜索 API 示例

```http
POST /indexes/pages/search
Content-Type: application/json
Authorization: Bearer {api_key}

{
  "q": "架构设计",
  "filter": "spaceId = 42 AND visibility = 'public'",
  "sort": ["updatedAt:desc"],
  "limit": 20,
  "attributesToHighlight": ["title", "content"]
}
```

---

## 4. CRDT 实时协作

### 4.1 选型：Yjs + y-websocket + y-redis

- **Yjs**：成熟的 CRDT 库，支持多种数据类型（Text, Map, Array）  
- **y-websocket**：官方 WebSocket 绑定，负责同步与 awareness（光标、选中等）  
- **y-redis**：用于在多 WebSocket 服务器间广播编辑，通过 Redis Pub/Sub 实现

### 4.2 协作架构

```
浏览器 A (Y.Doc) ─── WebSocket ──→ WebSocket Server 1 → redis pub (room:pageId)
浏览器 B (Y.Doc) ─── WebSocket ──→ WebSocket Server 2 ← redis sub (room:pageId)
                                    └── y-redis 自动处理广播
```

### 4.3 WebSocket 服务器配置

**示例（Node.js + y-websocket + y-redis）**：

```javascript
const WebSocket = require('ws');
const Y = require('yjs');
const { setupPersistence, setPersistence } = require('y-redis');
const redisClient = require('redis').createClient({ url: 'redis://redis-host:6379' });

const wss = new WebSocket.Server({ port: 1234 });

// 使用 Redis 广播 (y-redis)
const redisSub = redisClient.duplicate();
const redisPub = redisClient.duplicate();

setupPersistence({
  bindState: async (docName, ydoc) => {
    // 从 Redis 或 DB 恢复文档快照（可选）
    const persisted = await redisClient.get(`yjs:${docName}`);
    if (persisted) Y.applyUpdate(ydoc, new Uint8Array(Buffer.from(persisted, 'base64')));
  },
  writeState: async (docName, ydoc) => {
    // 定期持久化（防丢失），实际编辑仍以最终 DB 为准
    const update = Y.encodeStateAsUpdate(ydoc);
    await redisClient.set(`yjs:${docName}`, Buffer.from(update).toString('base64'), { EX: 86400 });
  }
});

wss.on('connection', (ws, req) => {
  // 从 URL 解析 room (pageId)
  const pageId = new URL(req.url, `http://${req.headers.host}`).searchParams.get('pageId');
  // 使用 y-websocket 的 utils 创建房间  
  // 内部使用 y-redis 广播
});
```

### 4.4 配置参数

| 组件 | 参数 | 建议值 |
|------|------|--------|
| Redis Pub/Sub | 频道模式 | `page:{pageId}` |
| WebSocket | 最大消息大小 | 5 MB（含 update） |
| WebSocket | ping/pong 间隔 | 30s |
| WebSocket Server | 集群数量 | 2‑4（根据并发编辑数） |
| Yjs | 合并间隔（gc） | 每个用户编辑后自动合并 |
| 文档快照 | 保存间隔 | 每 30 秒或每次完整的暂存保存时 |

**注意事项**：

- CRDT 只负责编辑同步，**最终存储仍由应用在用户“保存”时写入数据库**  
- 保存时：停止同步，将 Y.Doc 转为纯文本/Markdown 写入 DB，并触发缓存失效和索引更新  
- 未保存的编辑区使用 `y-indexeddb` 持久化在浏览器 IndexedDB，防止意外丢失

---

## 5. 集成示例：编辑流程

```
1. 用户打开页面
   - 浏览器加载静态资源（CDN）
   - 请求页面数据 → 本地缓存/Redis/DB → 返回
   - 初始化 Y.Doc，连接 WebSocket
   - 与服务器同步 Yjs(doc)

2. 用户编辑
   - 本地 Yjs 应用操作，同时通过 WebSocket 发送 update
   - WebSocket Server 接收，通过 Redis Pub 广播到同一房间其他节点
   - 其他在线用户实时看到变更

3. 用户点击“保存”
   - 应用将 Y.Doc 内容转为最终格式（Markdown / JSON）
   - 写入 PostgreSQL（或 MongoDB）
   - 更新 Redis 缓存（页面对象）
   - 失效本地缓存（应用服务器）
   - 异步推送 Meilisearch 索引更新
   - 若页面公开，通过 CDN API 清除缓存

4. 其他用户保存后
   - 保持一致：CRDT 已同步所有编辑，保存时无冲突
```

---

## 6. 配置参数汇总表

| 层级 | 参数 | 建议值 | 说明 |
|------|------|--------|------|
| **CDN** | 静态资源 TTL | 1 year (immutable) | 带 hash |
| | 页面快照 TTL | 5 min (browser), 10 min (edge) | stale-while-revalidate=60 |
| | 清除方式 | Surrogate-Key / purge API | 每次发布后清除关键页 |
| **Redis** | 实例规格 | 4 vCPU, 8 GB RAM | 可上云，按需选择 |
| | maxmemory | 4 GB | 使用 allkeys-lru |
| | maxclients | 10000 | |
| | 连接超时 | 3s | 应用端 |
| **本地缓存** | 最大 size | 50,000 entries | 每应用实例 |
| | TTL | 5 min (write) / 1 h (access) | 根据数据更新频率调整 |
| **Meilisearch** | 索引内存 | 4 GB | 索引基于文档量 |
| | 分页 | maxTotalHits: 1000 | 避免大结果集 |
| | 索引线程 | 2 | CPU 密集型 |
| **WebSocket** | 每进程连接数 | 5000 | 根据机器规格 |
| | 消息大小 | 5 MB | 防止单个编辑过大 |
| **应用服务器** | 实例数 | 2‑4 (HA) | 根据负载水平扩展 |
| | 每实例可用内存 | 16 GB | 包含本地缓存、Yjs 文档 |

---

## 7. 性能调优方向

- **热点页面**：将访问量最高的 10% 页面自动预热进本地缓存和 CDN  
- **合并读写**：对于频繁的自动保存，使用 500ms 去抖 + 批量提交 DB  
- **搜索降级**：若 Meilisearch 不可用，回退到数据库的 `LIKE` 查询  
- **CDN 回源**：开启 CDN 与源站之间的 HTTP/2 和连接复用  

---

## 8. 总结

三层缓存（CDN + Redis + 本地）实现了从边缘到核心的多级降低延迟；Meilisearch 提供了毫秒级全文搜索；Yjs+Redis 广播的 CRDT 方案保证多人实时编辑无冲突。以上配置参数可作为首次部署的基准，根据实际流量与数据量按比例调整即可。

— turns:1 cache:0.0% cost:$0.001512 save-vs-claude:98.0%
