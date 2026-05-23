```sql
-- TeamWiki 知识库系统 · 完整 DDL

-- ==========================================================
-- 1. 用户表
-- ==========================================================
CREATE TABLE users (
    id            SERIAL          PRIMARY KEY,
    username      VARCHAR(100)    NOT NULL UNIQUE,
    email         VARCHAR(255)    NOT NULL UNIQUE,
    password_hash VARCHAR(255)    NOT NULL,
    display_name  VARCHAR(200),
    avatar_url    TEXT,
    is_active     BOOLEAN         DEFAULT TRUE,
    created_at    TIMESTAMPTZ     DEFAULT NOW(),
    updated_at    TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_users_is_active ON users (is_active);

-- ==========================================================
-- 2. 空间表
-- ==========================================================
CREATE TABLE spaces (
    id          SERIAL          PRIMARY KEY,
    name        VARCHAR(200)    NOT NULL,
    description TEXT,
    owner_id    INTEGER         NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    space_type  VARCHAR(50)     DEFAULT 'team',
    created_at  TIMESTAMPTZ     DEFAULT NOW(),
    updated_at  TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_spaces_name     ON spaces (name);
CREATE INDEX idx_spaces_owner_id ON spaces (owner_id);

-- ==========================================================
-- 3. 页面表
-- ==========================================================
CREATE TABLE pages (
    id           SERIAL          PRIMARY KEY,
    space_id     INTEGER         NOT NULL REFERENCES spaces (id) ON DELETE CASCADE,
    title        VARCHAR(500)    NOT NULL,
    content      TEXT,
    created_by   INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    updated_by   INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    parent_id    INTEGER         REFERENCES pages (id) ON DELETE SET NULL,
    is_published BOOLEAN         DEFAULT TRUE,
    created_at   TIMESTAMPTZ     DEFAULT NOW(),
    updated_at   TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_pages_space_id   ON pages (space_id);
CREATE INDEX idx_pages_created_by ON pages (created_by);
CREATE INDEX idx_pages_updated_by ON pages (updated_by);
CREATE INDEX idx_pages_parent_id  ON pages (parent_id);
CREATE INDEX idx_pages_title      ON pages (title);
CREATE INDEX idx_pages_created_at ON pages (created_at);

-- ==========================================================
-- 4. 版本历史表
-- ==========================================================
CREATE TABLE page_versions (
    id              SERIAL          PRIMARY KEY,
    page_id         INTEGER         NOT NULL REFERENCES pages (id) ON DELETE CASCADE,
    version_number  INTEGER         NOT NULL,
    title           VARCHAR(500)    NOT NULL,
    content         TEXT,
    changed_by      INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    changed_at      TIMESTAMPTZ     DEFAULT NOW(),
    change_comment  TEXT,
    UNIQUE (page_id, version_number)
);

CREATE INDEX idx_page_versions_page_id    ON page_versions (page_id);
CREATE INDEX idx_page_versions_changed_by ON page_versions (changed_by);

-- ==========================================================
-- 5. 标签表
-- ==========================================================
CREATE TABLE tags (
    id         SERIAL          PRIMARY KEY,
    name       VARCHAR(200)    NOT NULL UNIQUE,
    created_at TIMESTAMPTZ     DEFAULT NOW()
);

-- 唯一约束已自动创建索引，无需额外索引

-- ==========================================================
-- 6. 页面-标签关联表
-- ==========================================================
CREATE TABLE page_tags (
    page_id  INTEGER  NOT NULL REFERENCES pages (id) ON DELETE CASCADE,
    tag_id   INTEGER  NOT NULL REFERENCES tags  (id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, tag_id)
);

CREATE INDEX idx_page_tags_tag_id ON page_tags (tag_id);

-- ==========================================================
-- 7. 评论表
-- ==========================================================
CREATE TABLE comments (
    id                SERIAL          PRIMARY KEY,
    page_id           INTEGER         NOT NULL REFERENCES pages (id) ON DELETE CASCADE,
    user_id           INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    content           TEXT            NOT NULL,
    parent_comment_id INTEGER         REFERENCES comments (id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ     DEFAULT NOW(),
    updated_at        TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_comments_page_id            ON comments (page_id);
CREATE INDEX idx_comments_user_id            ON comments (user_id);
CREATE INDEX idx_comments_parent_comment_id  ON comments (parent_comment_id);

-- ==========================================================
-- 8. 权限表（三级：组织/空间/页面）
-- ==========================================================
CREATE TABLE permissions (
    id            SERIAL          PRIMARY KEY,
    resource_type VARCHAR(50)     NOT NULL,   -- 'organization', 'space', 'page'
    resource_id   INTEGER         NOT NULL,
    user_id       INTEGER         NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role          VARCHAR(50)     NOT NULL,   -- 'admin', 'editor', 'viewer'
    granted_by    INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ     DEFAULT NOW(),
    UNIQUE (resource_type, resource_id, user_id)
);

CREATE INDEX idx_permissions_resource ON permissions (resource_type, resource_id);
CREATE INDEX idx_permissions_user_id  ON permissions (user_id);

-- ==========================================================
-- 9. 附件表
-- ==========================================================
CREATE TABLE attachments (
    id           SERIAL          PRIMARY KEY,
    page_id      INTEGER         NOT NULL REFERENCES pages (id) ON DELETE CASCADE,
    file_name    VARCHAR(500)    NOT NULL,
    file_path    TEXT            NOT NULL,
    file_size    BIGINT,
    mime_type    VARCHAR(200),
    uploaded_by  INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_attachments_page_id     ON attachments (page_id);
CREATE INDEX idx_attachments_uploaded_by ON attachments (uploaded_by);

-- ==========================================================
-- 10. 审计日志表
-- ==========================================================
CREATE TABLE audit_logs (
    id            BIGSERIAL       PRIMARY KEY,
    user_id       INTEGER         REFERENCES users (id) ON DELETE SET NULL,
    action        VARCHAR(50)     NOT NULL,   -- 'create', 'update', 'delete', 'view', ...
    resource_type VARCHAR(50),
    resource_id   INTEGER,
    details       JSONB,
    ip_address    INET,
    created_at    TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_user_id        ON audit_logs (user_id);
CREATE INDEX idx_audit_logs_resource       ON audit_logs (resource_type, resource_id);
CREATE INDEX idx_audit_logs_action         ON audit_logs (action);
CREATE INDEX idx_audit_logs_created_at     ON audit_logs (created_at);
```

— turns:1 cache:0.0% cost:$0.002881 save-vs-claude:98.1%
