# 云原生后端技术方案（通用设计）

## 1. 总体架构

采用 **微服务 + 事件驱动** 架构，分层如下：

| 层 | 组件 | 职责 |
|----|------|------|
| 接入层 | API Gateway / 负载均衡 | TLS终结、限流、鉴权、路由转发 |
| 业务层 | 多个无状态微服务 | 领域逻辑、业务编排 |
| 事件层 | 消息队列 (Kafka / Pulsar) | 异步解耦、最终一致性 |
| 数据层 | 分片数据库 + 缓存 | 持久化、高速读取 |
| 治理层 | 服务网格 (Istio) + 监控 (Prometheus+Grafana) | 可观测性、流量管理、安全策略 |

## 2. 服务拆分原则

- 按 **业务领域** 拆分（DDD bounded context），每个服务独立部署、独立数据库
- 服务间通信：同步（gRPC，用于低延迟查询） + 异步（事件，用于跨服务状态变更）
- 每个服务暴露 **健康检查** 和 **Prometheus metrics** 端点

## 3. API 设计

- **统一协议**：RESTful（对外） + gRPC（对内）
- **版本管理**：URL 路径版本号 `/v1/`，保留旧版本至少两个迭代
- **错误规范**：统一错误体 `{ code, message, details }`，使用 HTTP 状态码
- **分页**：游标分页（cursor-based）优于 offset 分页，避免偏移量偏移问题

## 4. 数据库与缓存

| 场景 | 方案 | 说明 |
|------|------|------|
| 关系型数据 | PostgreSQL（读写分离 + 分片） | 支持 JSONB、事务、物化视图 |
| 高并发读取 | Redis Cluster（缓存 + 限流计数器） | 缓存失效策略：旁路缓存 + 主动刷新 |
| 持久化事件 | Kafka / Pulsar | 日志压缩、死信队列、重试机制 |
| 全文搜索 | Elasticsearch | 索引时效性要求 < 1s 的场景 |

## 5. 认证与授权

- **OAuth 2.0 + JWT**：Access Token 短期（15min），Refresh Token 长期（7天）
- **RBAC + 细粒度权限（ABAC 补充）**：每个微服务从 Token 中解析用户角色与权限
- **API Gateway 层统一校验 Token**，业务层校验 scope

## 6. 部署与 CI/CD

- 容器化：Docker + Kubernetes（Helm 管理 chart）
- CI/CD：GitHub Actions → 构建镜像 → 自动化测试 → 灰度发布（Canary / Blue-Green）
- 基础设施即代码：Terraform 管理云资源

## 7. 监控与可观测性

- **日志**：结构化 JSON 日志，统一收集到 Loki（或 ELK）
- **指标**：RED 指标（Rate / Error / Duration），引入 OpenTelemetry
- **追踪**：分布式追踪（Jaeger / Zipkin），每个服务透传 `trace_id`

## 8. 容错与弹性

- 超时 + 重试（指数退避）+ 熔断（Resilience4j / Hystrix）
- 幂等性：每个请求携带 `idempotency_key`，服务端去重
- 降级：非核心功能失败时返回缓存数据或空结果

## 9. 安全

- 传输：全链路 HTTPS
- 数据：敏感字段加密存储（AES-256），脱敏脱密显示
- 防攻击：限流（令牌桶）、防 SQL 注入、防 XSS（输入校验）

## 10. 数据一致性

- 最终一致性：通过事件 + 本地消息表（或 Outbox 模式）保证
- 分布式事务：仅在关键场景使用 Saga（协调或编排模式），避免两阶段提交

---

以上方案适用于中大型互联网后端系统，可根据实际业务规模裁剪。如需针对特定场景（如高并发写入、IoT、金融级一致性）做优化，可进一步定制。

— turns:1 cache:78.4% cost:$0.000420 save-vs-claude:98.3%
