下面是为 **TeamWiki** 设计的完整 PostgreSQL 数据库 Schema。  
涵盖了**用户、空间、页面、版本历史、标签、评论、附件、权限、收藏、通知、审计日志**等核心模块，并充分利用了 PostgreSQL 特有的全文检索、JSONB、UUID 等特性。

```sql
-- 启用必要扩展
CREATE EXTENSION IF NOT EXISTS pgcrypto;      -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- 模糊搜索

-- ============================================================
-- 1. 用户表
-- ============================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username        VARCHAR(50) UNIQUE NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,           -- bcrypt 或 argon2
    display_name    VARCHAR(100),
    avatar_url      TEXT,
    role            VARCHAR(20) NOT NULL DEFAULT 'member',  -- admin | member | guest
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_users_email ON users(email);

-- ============================================================
-- 2. 空间 (顶级分类, 如 Confluence 的 Space)
-- ============================================================
CREATE TABLE spaces (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(200) NOT NULL,
    slug            VARCHAR(200) UNIQUE NOT NULL,    -- URL 友好标识
    description     TEXT,
    owner_id        UUID REFERENCES users(id) ON DELETE SET NULL,
    icon_url        TEXT,
    is_public       BOOLEAN NOT NULL DEFAULT false,   -- 公开空间所有人可读
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_spaces_owner ON spaces(owner_id);

-- ============================================================
-- 3. 页面 (文档核心)
-- ============================================================
CREATE TABLE pages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    space_id        UUID NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    title           VARCHAR(500) NOT NULL,
    slug            VARCHAR(500) NOT NULL,            -- 空间内唯一 (由应用保证)
    content         TEXT,                             -- Markdown 原文
    content_html    TEXT,                             -- 渲染后的 HTML (可选)
    tsvector_content TSVECTOR,                       -- 全文搜索向量, 由触发器维护
    creator_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    last_editor_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    parent_page_id  UUID REFERENCES pages(id) ON DELETE SET NULL, -- 父子层级 (树形)
    sort_order      INTEGER NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'published', -- draft | published | archived
    view_count      INTEGER NOT NULL DEFAULT 0,
    is_public       BOOLEAN NOT NULL DEFAULT false,   -- 覆盖空间权限? 默认继承
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,                     -- 软删除 (回收站)
    UNIQUE(space_id, slug)
);

CREATE INDEX idx_pages_space       ON pages(space_id);
CREATE INDEX idx_pages_creator     ON pages(creator_id);
CREATE INDEX idx_pages_editor      ON pages(last_editor_id);
CREATE INDEX idx_pages_parent      ON pages(parent_page_id);
CREATE INDEX idx_pages_status      ON pages(status) WHERE status = 'published';
CREATE INDEX idx_pages_deleted     ON pages(deleted_at) WHERE deleted_at IS NOT NULL;
CREATE INDEX idx_pages_trgm        ON pages USING GIN (title gin_trgm_ops);  -- 模糊标题搜索
CREATE INDEX idx_pages_fts         ON pages USING GIN (tsvector_content);   -- 全文搜索

-- 全文搜索触发器
CREATE FUNCTION pages_tsvector_trigger() RETURNS trigger AS $$
BEGIN
    NEW.tsvector_content :=
        setweight(to_tsvector('english', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('english', COALESCE(NEW.content, '')), 'B');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_pages_tsvector
    BEFORE INSERT OR UPDATE OF title, content
    ON pages
    FOR EACH ROW EXECUTE FUNCTION pages_tsvector_trigger();

-- ============================================================
-- 4. 页面版本历史
-- ============================================================
CREATE TABLE page_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id         UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    title           VARCHAR(500),
    content         TEXT,
    content_html    TEXT,
    editor_id       UUID REFERENCES users(id) ON DELETE SET NULL,
    change_note     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(page_id, version_number)
);

CREATE INDEX idx_page_versions_page ON page_versions(page_id);
CREATE INDEX idx_page_versions_time ON page_versions(created_at);

-- ============================================================
-- 5. 标签
-- ============================================================
CREATE TABLE tags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(50) UNIQUE NOT NULL,
    color           VARCHAR(7) -- #RRGGBB
);

-- ============================================================
-- 6. 页面-标签 (多对多)
-- ============================================================
CREATE TABLE page_tags (
    page_id UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    tag_id  UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, tag_id)
);

CREATE INDEX idx_page_tags_tag ON page_tags(tag_id);

-- ============================================================
-- 7. 评论
-- ============================================================
CREATE TABLE comments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id             UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    parent_comment_id   UUID REFERENCES comments(id) ON DELETE CASCADE, -- 回复
    author_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content             TEXT NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ
);

CREATE INDEX idx_comments_page  ON comments(page_id);
CREATE INDEX idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX idx_comments_author ON comments(author_id);

-- ============================================================
-- 8. 附件 (文件/图片)
-- ============================================================
CREATE TABLE attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id         UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    filename        VARCHAR(255) NOT NULL,
    file_path       TEXT NOT NULL,                -- 对象存储 key 或本地路径
    file_size       INTEGER,                     -- 字节
    mime_type       VARCHAR(100),
    uploader_id     UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_attachments_page ON attachments(page_id);

-- ============================================================
-- 9. 访问控制列表 (ACL, 细粒度权限)
-- ============================================================
-- 支持的配置: 角色 + 资源级覆盖
CREATE TABLE permissions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource_type   VARCHAR(20) NOT NULL,           -- 'space' | 'page'
    resource_id     UUID NOT NULL,                  -- 对应 space/page 的 ID
    principal_type  VARCHAR(10) NOT NULL,           -- 'user' | 'group'
    principal_id    UUID NOT NULL,                  -- 对应用户/组的 ID
    permission      VARCHAR(20) NOT NULL,           -- 'read' | 'write' | 'admin'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (resource_type, resource_id, principal_type, principal_id, permission)
);

CREATE INDEX idx_permissions_resource   ON permissions(resource_type, resource_id);
CREATE INDEX idx_permissions_principal  ON permissions(principal_type, principal_id);

-- ============================================================
-- 10. 收藏 (用户关注页面)
-- ============================================================
CREATE TABLE bookmarks (
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    page_id     UUID NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, page_id)
);

-- ============================================================
-- 11. 通知
-- ============================================================
CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- 接收者
    type            VARCHAR(50) NOT NULL,     -- 'page_updated' | 'comment_added' | 'mention' ...
    reference_id    UUID,                     -- 关联的 page/comment ID
    message         TEXT,
    is_read         BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_notifications_user_read
    ON notifications(user_id, is_read, created_at DESC);

-- ============================================================
-- 12. 审计日志 (可选, 记录所有敏感操作)
-- ============================================================
CREATE TABLE activity_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(50) NOT NULL,     -- 'page.create' | 'page.delete' | 'permission.change' ...
    resource_type   VARCHAR(20),
    resource_id     UUID,
    metadata        JSONB,                    -- 携带额外上下文 (变更详情等)
    ip_address      INET,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_activity_logs_user    ON activity_logs(user_id);
CREATE INDEX idx_activity_logs_time    ON activity_logs(created_at);
CREATE INDEX idx_activity_logs_action  ON activity_logs(action);
```

## 设计要点说明

| 模块 | 关键设计 |
|------|----------|
| **用户与权限** | 支持角色（`admin` / `member` / `guest`）加 ACL 覆盖，`permissions` 表可按空间或页面授予特定用户/组的 read / write / admin 权限。 |
| **空间与页面** | 页面通过 `space_id` 归属空间，`parent_page_id` 实现树形嵌套，`slug` 在空间内唯一，配合 `status` 和 `deleted_at` 支持草稿/归档/回收站。 |
| **版本控制** | `page_versions` 每次保存新增一条记录，通过 `page_id + version_number` 唯一标识，可回溯任意历史版本。 |
| **全文搜索** | 利用 PostgreSQL 内置 `tsvector` + GIN 索引，触发器自动从 `title` 和 `content` 生成搜索向量，支持权重区分（标题 > 正文）。 |
| **标签** | 独立标签表 + 多对多关联，方便跨页面筛选和标签云统计。 |
| **评论与附件** | 评论支持嵌套回复（`parent_comment_id`），软删除保护; 附件记录存储路径，可与对象存储（S3/MinIO）集成。 |
| **通知与收藏** | `bookmarks` 实现关注，`notifications` 留出扩展接口，可按类型推送。 |
| **审计** | `activity_logs` 以 JSONB 存储操作详情，为合规和排查提供完整记录。 |

## 使用建议（PostgreSQL 优化）

1. **行级安全（RLS）** ：可在 `pages`、`permissions` 上启用 RLS，根据用户角色自动过滤数据，实现多租户隔离。
2. **部分索引**：已对 `status = 'published'` 和 `deleted_at IS NOT NULL` 建立部分索引，加速常用查询。
3. **全文搜索**：使用 `tsvector_content` 的查询示例：
   ```sql
   SELECT id, title, ts_rank(tsvector_content, query) AS rank
   FROM pages, plainto_tsquery('english', '搜索关键词') AS query
   WHERE tsvector_content @@ query AND status = 'published'
   ORDER BY rank DESC LIMIT 20;
   ```
4. **软删除**：应用中所有列表查询默认加上 `deleted_at IS NULL`。
5. **连接池**：建议使用 PgBouncer 管理连接，避免长连接占用。

此 Schema 可根据实际业务进行扩展（如增加**组管理**、**导出/导入**、**阅读记录**等），核心结构已覆盖 TeamWiki 所需的大部分功能。

— turns:1 cache:96.4% cost:$0.001524 save-vs-claude:98.2%
