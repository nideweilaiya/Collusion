这是一个为 TeamWiki 设计的完整认证与权限安全方案。由于您未指定具体的技术栈或现有代码库，以下基于通用最佳实践，从认证、授权、数据安全、审计四个维度展开，采用 **RBAC + ReBAC（关系型访问控制）** 混合模型。

---

## 一、认证系统 (Authentication)

### 1. 登录方式
- **账号密码：** Bcrypt / Argon2id 哈希，8+位强密码策略，注册时对接 Pwned Passwords API 阻止泄露密码。
- **OAuth 2.0 / OIDC：** 企业微信、飞书、钉钉、Google、GitHub 等第三方身份源，企业版支持 **LDAP/AD** 及 **SAML 2.0 / OIDC** 单点登录。
- **多因素认证 (MFA)：** TOTP（Authenticator） + WebAuthn（YubiKey / 平台生物识别），管理员可强制开启，或针对高危操作（删除、导出）二次验证。

### 2. 注册与邀请
- SaaS 版本：公开注册 + 邮箱验证，或 Admin 发送 **一次性邀请链接**（带签名，短时效）。
- 企业版：Admin 后台创建 / **SCIM 协议** 同步 / LDAP 自动映射。

### 3. 会话管理
- **双 Token 机制：**
  - Access Token (JWT，15分钟)：携带 `sub`, `roles`, `permissions` 快照，减少每次查询。
  - Refresh Token (Opaque Token，30天，支持轮换)：存储于 Redis，支持**单点强退**。
- **Cookie 安全：** `__Host-` 前缀 + `HttpOnly` + `Secure` + `SameSite=Lax`。
- **吊销：** Redis 黑名单 + 用户角色变更时刷新权限版本号，即时失效旧 Token。

---

## 二、授权系统 (Authorization)

### 1. 权限模型：RBAC + ReBAC
- **RBAC（角色基础）：** 系统级 & 空间级粗粒度管理，适合管理员操作。
- **ReBAC（关系型访问控制）：** 基于 Google Zanzibar / OpenFGA 思想，每个资源维护一个关系图 (`用户 - 关系 - 资源`)，适合文档级/团队级精细控制。

### 2. 权限层级结构

```
[系统级别]         [空间/团队级别]        [文档/对象级别]
  Owner               Space Admin           Page Editor
  Admin               Space Editor          Page Viewer
  Member              Space Reader          Explicit Deny
  Guest               Explicit Deny
```

- **继承规则：** 系统 → 空间 → 页面 → 子页面。子对象继承父对象权限，显式 Deny 覆盖 Allow。
- **默认 Deny：** 未明确允许即拒绝。

### 3. 核心权限清单

| 资源类型 | 操作 (Actions) |
|---|---|
| 知识库 (Space) | `space.create`, `space.read`, `space.edit`, `space.delete`, `space.manage_members`, `space.manage_settings` |
| 页面 (Page) | `page.create`, `page.read`, `page.update`, `page.delete`, `page.move`, `page.publish`, `page.archive`, `page.export`, `page.restore` |
| 评论 (Comment) | `comment.create`, `comment.read`, `comment.update`, `comment.delete` |
| 附件 (Attachment) | `attachment.upload`, `attachment.read`, `attachment.delete` |
| 系统管理 | `team.manage`, `audit.view`, `system.settings`, `user.impersonate` |

### 4. 权限检查决策流 (PDP / PEP 模式)

```
请求 → [PEP (Policy Enforcement Point)]
         ↓  提取 UserID + ResourceID + Action
         ↓
     [PDP (Policy Decision Point)]
         ├── Super Admin? → ALLOW
         ├── 查询 ReBAC 图:
         │     User 对 Space/Page 是否有关系 ("owner" / "editor" / "viewer")?
         │     ├── 显式 Deny? → DENY
         │     ├── 显式 Allow? → ALLOW
         │     └── 继承父资源 Allow? → ALLOW
         └── Default: DENY
         ↓
     返回决策 (Allow / Deny) + 日志记录
```

### 5. 技术选型建议

| 场景 | 推荐引擎 |
|---|---|
| 轻量级，团队小 | **Casbin**（多语言，模型灵活） |
| 企业级，大规模精细权限 | **OpenFGA / Auth0 FGA**（原生 ReBAC，性能优秀） |
| 与 K8s / API Gateway 深度集成 | **OPA (Open Policy Agent)** |

---

## 三、安全纵深防御 (Defense in Depth)

| 层次 | 措施 |
|---|---|
| **传输层** | 全站 TLS 1.3，HSTS Preload，Certificate Transparency |
| **应用层** | 输入校验 (Zod / JSON Schema)、DOMPurify 渲染过滤、CSRF (检查 Content-Type + Origin)、速率限制 (滑动窗口 + 令牌桶) |
| **凭据层** | 密码 Argon2id、API Key 加盐哈希、MFA 绑定设备凭证 |
| **存储层** | 数据库 TDE、敏感字段 AES-256-GCM、KMS 自动轮换密钥 |
| **灾备层** | 加密冷备份 + 异地容灾，定期演练 |

### 防常见攻击
- **暴力破解：** 5次错误锁定 15 分钟，IP 级别限流，可开启 hCaptcha / Turnstile。
- **权限提升：** 每次请求校验 `permission_version`，角色变更后旧 JWT 失效。
- **遍历漏洞：** 资源 ID 使用 UUID，接口强制查询当前用户是否有权限。

---

## 四、审计与合规 (Audit & Compliance)

### 审计日志格式
记录所有写操作 + 敏感读操作（导出、批量查询），结构如下：

```json
{
  "timestamp": "ISO 8601",
  "actor_id": "user-uuid",
  "actor_ip": "x.x.x.x",
  "session_id": "xxx",
  "action": "space.manage_members",
  "resource_type": "space",
  "resource_id": "space-uuid",
  "context": {
    "before": { "members": ["Alice", "Bob"] },
    "after":  { "members": ["Alice", "Bob", "Charlie"] }
  },
  "trace_id": "req-xxx",
  "immutable_hash": "sha256(prev_hash + payload)"
}
```

- **不可篡改性：** 哈希链 / 定时签发 + S3 Object Lock / Append Only DB。
- **保留与导出：** 默认保留 1 年，支持 SIEM（Splunk / ELK）集成。

---

## 五、自动化安全检查 (可选 CI 集成)

建议将以下检查加入 CI / Pre-commit 钩子：
1. **Secret 泄漏扫描** (Gitleaks / TruffleHog)
2. **SAST 静态扫描** (Semgrep / CodeQL 检测权限绕过模式)
3. **依赖漏洞** (Trivy / Snyk)
4. **权限配置测试**：编写集成测试覆盖每个角色的权限边界。

---

## 下一步建议

1. **如果您已有 TeamWiki 代码：** 请提供代码根目录或认证模块路径，我可以使用 `explore` 技能审查现存安全漏洞（如 JWT 硬编码、缺失权限校验、SQL 注入风险）。
2. **如果您需要具体落地：** 请告知技术栈 (Node.js / Go / Python / Java)，我可以给出对应框架的代码示例（例如 FastAPI 的 `Depends` 权限中间件，或 Spring Security 的 Method Security 配置）。
3. **如果这是方案评审：** 上述结构可直接充当架构文档初稿，可直接按模块深入讨论。

— turns:1 cache:96.3% cost:$0.002162 save-vs-claude:98.2%
