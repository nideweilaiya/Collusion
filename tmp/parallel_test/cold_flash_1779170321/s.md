# TeamWiki 安全方案：JWT 认证 + 三级权限 + 审计日志

## 1. 概述

本方案为 TeamWiki 设计一套完整的安全体系，涵盖 **认证（JWT）**、**授权（三级权限）**、**纵深防御（XSS/CSRF/注入）** 与 **审计日志**。目标是保障系统在用户身份验证、资源访问控制、攻击防护及事后追溯方面的安全性。

---

## 2. JWT 认证设计

### 2.1 Token 结构

使用 **RS256** 签名算法（非对称密钥），便于服务间验证。Token 包含以下声明：

```json
{
  "header": {
    "alg": "RS256",
    "typ": "JWT",
    "kid": "key-v1"
  },
  "payload": {
    "sub": "user-uuid",               // 用户唯一标识
    "role": "admin",                  // 主角色（三级之一）
    "permissions": ["wiki:write", "wiki:delete", "user:manage"],
    "session_id": "sess_xxxxx",       // 服务端 session 关联 ID
    "iat": 1680000000,
    "exp": 1680003600,                // 1 小时过期
    "iss": "teamwiki-auth",
    "aud": "teamwiki-api"
  },
  "signature": "base64url(...)"
}
```

- **`role`** 用于快速权限判定（三级角色），**`permissions`** 提供细粒度控制，方便未来扩展。
- **`session_id`** 用于强制吊销特定会话（配合服务端黑名单或 Redis）。
- **`exp`** 建议 15–60 分钟，刷新 token 使用 **refresh token**（存储于服务端，有效期 7 天）。

### 2.2 生成与验证流程

```
客户端登录 → 服务端验证凭证 → 生成 access_token（JWT） + refresh_token（随机字符串）
          ↓
      客户端存储 access_token（内存/HttpOnly Cookie）和 refresh_token（HttpOnly Cookie，路径限制）
          ↓
      每次 API 请求携带 access_token（Authorization: Bearer <token>）
          ↓
      服务端中间件验证：
          1. 检查签名和 iss/aud
          2. 检查是否在撤销列表中（session_id 在 Redis 黑名单）
          3. 解析 payload 放入 request.user
```

- **Refresh 机制**：access_token 过期后，客户端用 refresh_token 获取新 access_token；refresh_token 可设置 `exp` 和 `rotate`（轮换）。
- **撤销策略**：用户修改密码、权限变更或管理员强制退出时，将 `session_id` 加入 Redis 黑名单（记录原始 `exp`）。

### 2.3 安全存储

- 前端：access_token 不建议存 localStorage（易 XSS 窃取），应使用 **HttpOnly + Secure + SameSite=Strict** Cookie 存储，然后由服务端自动注入请求头。
- 如果前端必须手动携带 Token（如移动端），则必须在 HTTPS 传输，且注意 XSS 防护。

---

## 3. 三级权限模型

### 3.1 角色定义

| 角色   | 英文标识   | 权限范围                                                 |
|--------|------------|----------------------------------------------------------|
| 管理员 | `admin`    | 完全控制：用户管理、系统配置、所有 Wiki 的 CRUD、审计日志查看 |
| 编辑员 | `editor`   | 内容管理：创建/编辑/删除自己及他人 Wiki 页面，不可管理用户    |
| 查看员 | `viewer`   | 只读访问：浏览 Wiki 页面，不可创建、编辑或删除               |

### 3.2 权限矩阵（示例）

| 操作 / 资源              | admin | editor | viewer |
|--------------------------|-------|--------|--------|
| 用户管理（CRUD）         | √     | ×      | ×      |
| 系统配置                 | √     | ×      | ×      |
| 创建 Wiki 页面           | √     | √      | ×      |
| 编辑任意页面             | √     | √(*)   | ×      |
| 删除任意页面             | √     | √(*)   | ×      |
| 查看所有页面             | √     | √      | √      |
| 查看审计日志             | √     | ×      | ×      |

> *说明：编辑员可删除自己创建的页面，或根据空间权限删除他人页面（细粒度可再扩展）。此处按通用三级设计。*

### 3.3 权限中间件流程

```
请求 → AuthenticationMiddleware（验证 JWT）
     → AuthorizationMiddleware（检查路由/资源权限）
          1. 从 request.user 中提取 role 和 permissions
          2. 根据请求的方法与 URL 映射到所需权限（如 `wiki:write`）
          3. 检查 role 是否满足三级权限（快速放行 admin）
          4. 若 role 不足以匹配，则详细检查 permissions 列表
          5. 通过则继续，否则返回 403
```

- **细粒度控制**：使用类似 RBAC 的权限字符串，如 `wiki:write`、`user:manage`。角色是权限的集合，且在 JWT 中直接附带一级角色以减少跨服务查询。
- **资源级别隔离**：对于 Wiki 页面，可支持基于空间的权限（Space），该逻辑在业务层实现，中间件负责基础的角色与权限核验。

### 3.4 权限变更处理

- 当用户角色/权限被修改后，需立即影响下一次请求：
  - **方案 A**：将旧 access_token 的 `session_id` 加入黑名单，强制用户重新认证。
  - **方案 B**：使用短有效期 token（如 5 分钟），配合刷新机制保证权限及时更新。
- 推荐 **方案 A + B** 组合：短 token 减少黑名单压力，高安全操作时强制检查黑名单。

---

## 4. XSS / CSRF / 注入防护

### 4.1 XSS 防护

| 层面       | 措施                                                                 |
|------------|----------------------------------------------------------------------|
| 输出编码   | 所有用户内容渲染使用模板引擎的转义功能（如 `{{ . | escapeHTML }}`）   |
| 富文本     | 仅允许白名单标签和属性，使用 DOMPurify 或类似库输出清洗              |
| CSP        | Content-Security-Policy `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` |
| Cookie     | 认证 Cookie 设置 `HttpOnly` + `Secure` + `SameSite=Strict`           |
| 前端存储   | 避免将敏感数据存入 localStorage，使用内存变量，减少 XSS 影响面         |

### 4.2 CSRF 防护

- **API 接口**：要求请求头携带自定义 `X-CSRF-Token`，其值从服务端生成的 token 获取（可通过 Cookie 或首屏渲染注入）。
- **替代方案（纯后端 API）**：使用 `Origin` + `Referer` 校验（仅允许白名单域名），配合 `SameSite=Strict` Cookie。
- **表单**：使用双提交 Cookie 模式（服务端在 Cookie 中设置随机 token，客户端在请求头中读取同源 Cookie 值发回验证）。
- **重要写操作（如删除）**：额外要求二次确认（如密码或验证码）。

### 4.3 注入防护

| 类型           | 防护措施                                                                 |
|----------------|--------------------------------------------------------------------------|
| SQL 注入       | 强制使用参数化查询或 ORM（如 GORM、Sequelize），禁止拼接 SQL              |
| NoSQL 注入     | 使用与底层驱动一致的查询构造器，过滤 `$gt`、`$where` 等危险操作符          |
| 命令注入       | 避免直接调用 shell 命令；如需执行，使用白名单命令并转义参数                |
| LDAP / XML 注入| 使用专业库处理输入，避免将用户输入直接嵌入查询结构                         |
| 通用原则       | 输入验证：类型、长度、格式（如 UUID、Email），拒绝不符合规范的输入         |

### 4.4 安全响应头

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

---

## 5. 审计日志系统

### 5.1 审计事件分类

| 类别           | 事件类型（示例）                                                                            | 严重级别 |
|----------------|---------------------------------------------------------------------------------------------|----------|
| **认证**       | `LOGIN_SUCCESS`, `LOGIN_FAILED`, `LOGOUT`, `TOKEN_REFRESH`, `PASSWORD_CHANGE`                | 中～高    |
| **授权**       | `PERMISSION_DENIED`, `ROLE_CHANGE`, `PRIVILEGE_ESCALATION` ,  `SESSION_KILLED`               | 高        |
| **数据操作**   | `WIKI_CREATE`, `WIKI_UPDATE`, `WIKI_DELETE`, `WIKI_VIEW`（可选择性记录敏感资源）             | 低～高    |
| **系统管理**   | `USER_CREATE`, `USER_DELETE`, `USER_ROLE_SET`, `CONFIG_UPDATE`, `SYS_BACKUP`                 | 高        |
| **异常行为**   | `RATE_LIMIT_EXCEEDED`, `SUSPICIOUS_IP`, `MALFORMED_REQUEST`, `CSRF_TOKEN_MISMATCH`          | 中        |

### 5.2 日志结构

每条审计记录包含统一字段：

```json
{
  "event_id": "uuid",
  "timestamp": "2025-03-21T10:00:00Z",
  "event_type": "WIKI_DELETE",
  "severity": "HIGH",
  "actor": {
    "user_id": "uuid",
    "username": "alice",
    "ip": "192.168.1.100",
    "user_agent": "Mozilla/5.0 ..."
  },
  "resource": {
    "type": "wiki_page",
    "id": "page-uuid",
    "name": "安全设计方案"
  },
  "action": "DELETE",
  "result": "SUCCESS",
  "details": {
    "revision": 3,
    "reason": "过期内容清理"
  }
}
```

### 5.3 日志记录策略

- **同步 vs 异步**：高安全性事件（授权失败、系统管理）同步记录，保障可靠性；普通数据操作可异步写入（内存队列 → 批量写入）。
- **存储方式**：使用专用数据库（如 PostgreSQL 独立表或 Elasticsearch）存储结构化日志，便于分析与检索。
- **保留期限**：审计日志至少保留 **180 天**（依据监管要求），过期归档或删除。
- **访问控制**：仅管理员可查看审计日志，且查询行为本身也需被记录。

### 5.4 审计中间件流程

```python
# 伪代码
def audit_middleware(request, response):
    if should_audit(request):
        event = build_event(request, response)
        if event.severity >= HIGH:
            log_sync(event)          # 同步写
        else:
            log_async(event)         # 异步写
```

- 建议通过 **装饰器或注解** 标记哪些 API 需要审计，自动捕获请求/响应上下文。

---

## 6. 整体请求处理管线

```
请求 → Rate Limiter → WAF/防火墙 → HTTPS
     → AuthenticationMiddleware（JWT 验证 + 黑名单检查）
     → AuditLogMiddleware（开始计时，记录请求）
     → AuthorizationMiddleware（角色 + 权限校验）
     → Input Validation（参数化查询/转义）
     → 业务处理
     → Output Encoding（CSP header，转义输出）
     → AuditLogMiddleware（记录结果）
     → 响应
```

---

## 7. 总结

- **JWT 认证**：使用 RS256 签名、短时效、刷新 token、黑名单撤销，确保身份可信。
- **三级权限**：基于 admin / editor / viewer 角色，结合细粒度权限字符串，中间件分层校验，易于扩展。
- **纵深防御**：XSS 采用 CSP + 输出编码，CSRF 采用 token + SameSite，注入依赖参数化查询。
- **审计日志**：事件分类清晰，结构化存储，重要事件同步记录，支持事后溯源与异常发现。

该方案为 TeamWiki 提供了多层次的保护，既满足常见安全需求，又保留了灵活性，可根据实际业务需求进一步定制角色权限和审计规则。

— turns:1 cache:0.0% cost:$0.001155 save-vs-claude:98.0%
