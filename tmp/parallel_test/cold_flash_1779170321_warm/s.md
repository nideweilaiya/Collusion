# TeamWiki 安全方案：JWT 认证 + 三级权限 + 审计日志

## 1. 总体架构概览

```
┌──────────┐   JWT    ┌──────────────┐   RBAC   ┌──────────┐
│  Client  │ ──────▶ │  Auth Gateway │ ──────▶ │  Service │
└──────────┘         │  (Nginx/Node)│         └──────────┘
                      │ 中间件栈      │
                      └──────────────┘
```

- **Auth Gateway** 负责 JWT 签发、刷新、验证。
- **权限中间件** 从 JWT 解析角色，与目标资源的 ACL 矩阵比对。
- **审计中间件** 在关键操作前后记录事件。
- **安全过滤器** 在请求入口处处理 XSS/CSRF/注入。

---

## 2. JWT 认证方案

### 2.1 Token 结构（Payload 声明）

```json
{
  "sub": "user_001",             // 用户唯一 ID
  "username": "alice",           // 可读登录名（非敏感信息）
  "role": "admin",               // 三级权限角色（admin/editor/viewer）
  "iat": 1710000000,             // 签发时间（unix 秒）
  "exp": 1710003600,             // 过期时间（1 小时后）
  "jti": "a1b2c3d4-e5f6-...",   // 唯一 JWT ID（防重放，可入黑名单）
  "scope": "wiki:read wiki:write" // 可选：细粒度范围，用于 API 级控制
}
```

- **签名算法**：HS256（对称）或 RS256（非对称）。推荐 RS256，服务端仅用公钥验证，私钥在认证中心。
- **Token 长度**：Payload 中不含角色之外的权限列表，保持短小。
- **不使用 refresh_token**：采用短时 access_token（15-60 分钟）+ 滑动过期策略，或配合 HttpOnly cookie 做无感刷新。

### 2.2 Token 生成与验证流程

1. **登录**：用户提供凭证 → 认证服务验证 → 生成 JWT（含角色）→ 返回 `Authorization: Bearer <token>` 或 Set-Cookie `HttpOnly; SameSite=Strict; Secure`。
2. **请求验证**：
   - 中间件取 Header 或 Cookie 中的 token。
   - 验证签名、过期时间、jti 是否在黑名单中。
   - 验证通过则注入 `req.user`（含 `sub`、`role`）。
3. **刷新机制**：
   - 不单独颁发 refresh_token。
   - 当 token 过期或剩余时间 < 5 分钟时，客户端可请求 `/auth/refresh`，该端点要求当前 token 仍在有效期内，签发新 token 并将旧 jti 加入黑名单（Redis 过期时间 = 原 token 过期时间）。
4. **黑名单**：用户登出、强制下线、密码变更时，将对应 jti 加入 Redis 黑名单，剩余 TTL 内拒绝。

### 2.3 安全性措施

- **签名密钥**：定期轮换（密钥版本化），通过密钥管理服务（KMS）或环境变量。
- **禁止敏感信息**：Payload 中不存密码、手机号等。
- **防重放**：jti 一次性使用（可选，对写操作启用，读操作可放宽）。

---

## 3. 三级权限设计

### 3.1 角色定义（三级）

| 角色   | 等级 | 描述               | 典型操作                                        |
| ------ | ---- | ------------------ | ----------------------------------------------- |
| viewer | 1    | 只读用户           | 查看页面、评论、搜索                            |
| editor | 2    | 编辑贡献者         | 创建/编辑页面、上传附件、管理自己创建的页面     |
| admin  | 3    | 管理员             | 删除页面、管理用户、修改站点设置、查看审计日志  |

### 3.2 权限矩阵（RBAC + 资源级控制）

| 操作 \ 角色        | viewer | editor | admin |
| ------------------ | ------ | ------ | ----- |
| 页面：查看         | ✔      | ✔      | ✔     |
| 页面：创建         | ✘      | ✔      | ✔     |
| 页面：编辑（自己） | ✘      | ✔      | ✔     |
| 页面：编辑（他人） | ✘      | ✘      | ✔     |
| 页面：删除         | ✘      | ✘      | ✔     |
| 附件：下载         | ✔      | ✔      | ✔     |
| 附件：上传         | ✘      | ✔      | ✔     |
| 用户管理           | ✘      | ✘      | ✔     |
| 审计日志查看       | ✘      | ✘      | ✔     |
| 系统设置           | ✘      | ✘      | ✔     |

- **额外约束**：editor 仅能编辑自己创建的页面（owner 字段匹配）。admin 可覆盖 owner。
- **实现方式**：在资源（Page）上记录 `owner_id`，中间件先检查角色等级，再检查资源归属。

---

## 4. 权限中间件流程

### 4.1 中间件栈顺序（Express/Koa 示例）

```
请求 → CSRF Check → XSS Filter → 注入防护 → Auth（JWT解析） → RBAC → 审计日志 → 业务路由
```

### 4.2 认证中间件（authMiddleware）

```
1. 从 Authorization Header 或 HttpOnly Cookie 获取 token
2. 解析 JWT，验证签名 + 过期 + jti 黑名单
3. 失败 → 401 Unauthorized + 审计事件（认证失败）
4. 成功 → 将 { userId, role } 挂载到 req.user，继续
```

### 4.3 授权中间件（rbacMiddleware）

```
输入：{ requiredRoleLevel, resourceOwnerId? }
流程：
1. 从 req.user 读取当前角色等级
2. 如果当前等级 >= requiredRoleLevel → 通过
3. 如果当前等级 == editor 且 resourceOwnerId == req.user.userId → 通过
4. 否则 → 403 Forbidden + 审计事件（权限拒绝）
```

- 对于资源 ID 从路径参数或请求体中提取。
- 对于批量操作，可能需要循环检查（或用数据库查询过滤）。

### 4.4 流程示例（文字说明）

```
客户端请求 DELETE /api/pages/123
→ cookie 携带 JWT
→ XSS 过滤：对参数编码
→ CSRF 校验：检查 SameSite Cookie 或 Referer
→ 注入防护：SQL/NoSQL 参数化，命令注入过滤
→ authMiddleware：验证 JWT，解析出 role=editor, userId=u001
→ rbacMiddleware：删除需要 admin 等级（3），但 editor 等级为 2，且资源 owner 是 u002 ≠ u001
→ 返回 403，同时审计日志记录：{ actor: u001, action: delete_page, resource: 123, result: forbidden }
```

---

## 5. XSS / CSRF / 注入防护

### 5.1 XSS 防护

| 措施                   | 实现方式                                                |
| ---------------------- | ------------------------------------------------------- |
| **输出编码**           | 服务端模板自动 HTML 实体编码（如 EJS `<%=` 而非 `<%-`） |
| **富文本过滤**         | 使用 DOMPurify（服务端）或允许白名单标签（如 Markdown）  |
| **CSP Header**         | `Content-Security-Policy: default-src 'self'; script-src 'self'` |
| **HttpOnly Cookie**    | 令牌置于 HttpOnly Cookie，防止 JS 读取                  |
| **X-XSS-Protection**   | 1; mode=block（浏览器 XSS 过滤器）                      |

### 5.2 CSRF 防护

| 措施                       | 实现方式                                                     |
| -------------------------- | ------------------------------------------------------------ |
| **SameSite Cookie**        | `Set-Cookie: token=...; SameSite=Strict; Secure; HttpOnly`   |
| **CSRF Token**（额外层）   | 对表单/API 请求，检查 `X-CSRF-Token` Header，值从签名的 nonce 获取 |
| **Referer/Origin 检查**    | 拒绝非本站域的请求（注：某些场景可能干扰，作为第二防线）     |
| **敏感操作的二次确认**     | 如删除页面需再输入密码或使用 2FA                             |

### 5.3 注入防护

| 类型       | 防护措施                                                                 |
| ---------- | ------------------------------------------------------------------------ |
| **SQL 注入** | 使用 ORM/参数化查询（如 Prisma、Knex）、禁止拼接 SQL。                   |
| **NoSQL 注入** | 使用 Mongoose 类型校验、禁止直接将 req.body 传入查询。                   |
| **命令注入** | `child_process` 禁用 `exec`，优先 `execFile` + 参数白名单。              |
| **LDAP 注入** | 如果集成 LDAP，过滤 `()&\|!` 字符。                                      |
| **输入校验** | 对所有用户输入做类型、长度、格式校验（如 `zod` 或 `joi`）。              |

---

## 6. 审计日志设计

### 6.1 审计事件分类

| 事件类别     | 事件名称                       | 说明                                 |
| ------------ | ------------------------------ | ------------------------------------ |
| **认证事件** | `login_success`                | 登录成功                             |
|              | `login_failed`                 | 登录失败（含原因：密码错误、账号锁定） |
|              | `logout`                       | 登出                                 |
|              | `token_refresh`                | Token 刷新                           |
|              | `password_change`              | 修改密码                             |
| **权限事件** | `role_changed`                 | 管理员修改用户角色                   |
|              | `permission_denied`            | 权限不足的尝试（403）                |
|              | `owner_transfer`               | 页面所有权转移                       |
| **资源操作** | `page_create`                  | 创建页面                             |
|              | `page_update`                  | 修改页面内容                         |
|              | `page_delete`                  | 删除页面                             |
|              | `page_view` *(可选，控制频率)* | 查看页面（高流量可选做抽样）         |
|              | `attachment_upload`            | 上传附件                             |
|              | `attachment_delete`            | 删除附件                             |
| **系统事件** | `user_create`                  | 新建用户                             |
|              | `user_disable`                 | 禁用用户                             |
|              | `config_change`                | 站点配置变更                         |
| **异常事件** | `rate_limit_exceeded`          | 触发频率限制                         |
|              | `suspicious_request`           | 可疑请求（SQL 注入尝试、路径遍历等） |

### 6.2 审计日志结构（JSON）

```json
{
  "id": "evt_abcdef123456",
  "timestamp": "2025-03-10T14:30:00Z",
  "event": "page_delete",
  "category": "resource_operation",
  "actor": {
    "userId": "user_001",
    "username": "alice",
    "role": "admin"
  },
  "resource": {
    "type": "page",
    "id": "page_123",
    "title": "安全方案"
  },
  "context": {
    "ip": "10.0.0.1",
    "userAgent": "Mozilla/5.0 ...",
    "requestId": "req_xyz"
  },
  "result": "success",      // success / failure / forbidden
  "detail": {}              // 额外信息，如变更前后值
}
```

### 6.3 存储与保护

- **存储**：写入专用日志数据库（如 Elasticsearch、PostgreSQL 审计表）或文件（不可变日志，如 Syslog + 签名）。
- **保留策略**：认证事件 90 天，操作事件 1 年，异常事件 2 年。
- **访问控制**：仅 admin 角色可查看，日志删除操作本身也需记录。
- **防篡改**：可选对每条日志计算 SHA256，日志链中存前一条 Hash（类似区块链）。

### 6.4 审计中间件实现要点

- 使用 **AOP** 或 **装饰器** 在路由处理中嵌入审计记录。
- 异步非阻塞写入，避免影响主请求性能。
- 敏感操作（删除、权限变更）需在操作前记录“尝试”事件，操作后记录结果。

---

## 7. 方案总结与最佳实践

| 模块       | 关键措施                                               |
| ---------- | ------------------------------------------------------ |
| JWT        | RS256 签名，jti 黑名单，短有效期，不包含敏感数据       |
| 三级权限   | viewer / editor / admin，资源归属控制                   |
| 中间件     | 认证 → 授权 → 审计，分级校验                           |
| XSS        | CSP + 输出编码 + 富文本白名单                          |
| CSRF       | SameSite Strict + 可选 CSRF Token                      |
| 注入       | 参数化查询严格、命令执行禁用 exec                       |
| 审计       | 6 类事件标准化，统一 JSON 格式，存查分离，防篡改        |

- **额外建议**：启用速率限制（`express-rate-limit`）、注册验证码、失败的登录尝试锁定。
- **升级路径**：三级权限可扩展为基于属性的访问控制（ABAC），在 JWT 中添加自定义属性。

--- 

*以上方案可直接在 TeamWiki 后端（Node.js/Go/Python）中落地，各模块解耦，便于测试和维护。*

— turns:1 cache:0.0% cost:$0.001123 save-vs-claude:98.0%
