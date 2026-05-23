```sql
-- 组织表
CREATE TABLE organizations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name varchar(255) NOT NULL,
    slug varchar(255) NOT NULL,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_organizations_slug UNIQUE (slug)
);
CREATE INDEX idx_organizations_slug ON organizations(slug);

-- 用户表
CREATE TABLE users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username varchar(100) NOT NULL,
    email varchar(255) NOT NULL,
    password_hash varchar(255) NOT NULL,
    display_name varchar(255),
    avatar_url text,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_users_username UNIQUE (username),
    CONSTRAINT uk_users_email UNIQUE (email)
);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_username ON users(username);

-- 空间表
CREATE TABLE spaces (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name varchar(255) NOT NULL,
    slug varchar(255) NOT NULL,
    description text,
    organization_id bigint NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_by bigint NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_spaces_org_slug UNIQUE (organization_id, slug)
);
CREATE INDEX idx_spaces_organization_id ON spaces(organization_id);
CREATE INDEX idx_spaces_slug ON spaces(slug);
CREATE INDEX idx_spaces_created_by ON spaces(created_by);

-- 页面表（支持父子层级）
CREATE TABLE pages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title varchar(500) NOT NULL,
    slug varchar(500),
    content text,
    space_id bigint NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    parent_id bigint REFERENCES pages(id) ON DELETE SET NULL,
    created_by bigint NOT NULL REFERENCES users(id),
    updated_by bigint NOT NULL REFERENCES users(id),
    is_published boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_pages_space_id ON pages(space_id);
CREATE INDEX idx_pages_parent_id ON pages(parent_id);
CREATE INDEX idx_pages_created_by ON pages(created_by);
CREATE INDEX idx_pages_updated_by ON pages(updated_by);

-- 页面版本历史表
CREATE TABLE page_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    version_number integer NOT NULL,
    title varchar(500) NOT NULL,
    content text,
    change_comment text,
    created_by bigint NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_page_versions UNIQUE (page_id, version_number)
);
CREATE INDEX idx_page_versions_page_id ON page_versions(page_id);
CREATE INDEX idx_page_versions_created_by ON page_versions(created_by);

-- 标签表
CREATE TABLE tags (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name varchar(100) NOT NULL,
    color varchar(7),
    CONSTRAINT uk_tags_name UNIQUE (name)
);
CREATE INDEX idx_tags_name ON tags(name);

-- 页面与标签关联表
CREATE TABLE page_tags (
    page_id bigint NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    tag_id bigint NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (page_id, tag_id)
);
CREATE INDEX idx_page_tags_tag_id ON page_tags(tag_id);

-- 评论表（支持回复嵌套）
CREATE TABLE comments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    user_id bigint NOT NULL REFERENCES users(id),
    parent_id bigint REFERENCES comments(id) ON DELETE CASCADE,
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_comments_page_id ON comments(page_id);
CREATE INDEX idx_comments_user_id ON comments(user_id);
CREATE INDEX idx_comments_parent_id ON comments(parent_id);

-- 附件表（关联到页面）
CREATE TABLE attachments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id bigint NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    user_id bigint NOT NULL REFERENCES users(id),
    file_name varchar(255) NOT NULL,
    file_path text NOT NULL,
    file_size bigint NOT NULL,
    mime_type varchar(255),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_attachments_page_id ON attachments(page_id);
CREATE INDEX idx_attachments_user_id ON attachments(user_id);

-- 权限表（三级：组织 / 空间 / 页面）
CREATE TABLE permissions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    organization_id bigint REFERENCES organizations(id) ON DELETE CASCADE,
    space_id bigint REFERENCES spaces(id) ON DELETE CASCADE,
    page_id bigint REFERENCES pages(id) ON DELETE CASCADE,
    role varchar(50) NOT NULL CHECK (role IN ('admin','editor','viewer')),
    granted_by bigint REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT check_single_entity CHECK (
        (organization_id IS NOT NULL AND space_id IS NULL AND page_id IS NULL) OR
        (organization_id IS NULL AND space_id IS NOT NULL AND page_id IS NULL) OR
        (organization_id IS NULL AND space_id IS NULL AND page_id IS NOT NULL)
    )
);
CREATE INDEX idx_permissions_user_id ON permissions(user_id);
CREATE INDEX idx_permissions_organization_id ON permissions(organization_id);
CREATE INDEX idx_permissions_space_id ON permissions(space_id);
CREATE INDEX idx_permissions_page_id ON permissions(page_id);

-- 审计日志表
CREATE TABLE audit_logs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id bigint REFERENCES users(id),
    action varchar(100) NOT NULL,
    entity_type varchar(100) NOT NULL,
    entity_id bigint NOT NULL,
    changes jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at);
```

— turns:1 cache:0.0% cost:$0.001496 save-vs-claude:98.0%
