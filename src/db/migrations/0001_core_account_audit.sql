-- =====================================================================
-- UG-DCMS 迁移 0001 — 账户、角色、会话、审计、系统设置
-- 基准依据: UG-DCMS-SRS-001 §3 §11 / UG-DCMS-ERD-001 §4 / UG-DCMS-IVV-001 INV-020 INV-024 INV-025
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------
-- 通用: updated_at 自动维护
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- 通用: 拒绝任何修改/删除 (用于不可变数据) — INV-007 INV-011 INV-015 INV-020
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_block_write() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'DCMS-IMMUTABLE: 表 % 的 % 操作被拒绝(该记录为不可变历史数据)',
        TG_TABLE_NAME, TG_OP USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- 角色 — SRS-ACC-005
-- =====================================================================
CREATE TABLE role (
    code          text PRIMARY KEY,
    name_cn       text NOT NULL,
    name_en       text NOT NULL,
    description   text,
    is_builtin    boolean NOT NULL DEFAULT true,
    sort_order    integer NOT NULL DEFAULT 0
);

-- =====================================================================
-- 用户 — SRS-ACC-001 (账户总数不设上限)
-- =====================================================================
CREATE TABLE app_user (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    username             text NOT NULL,
    full_name            text NOT NULL,
    email                text,
    employee_no          text,
    password_hash        text NOT NULL,
    is_active            boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT false,
    failed_login_count   integer NOT NULL DEFAULT 0,
    locked_until         timestamptz,
    last_login_at        timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           uuid,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    updated_by           uuid,
    CONSTRAINT uq_app_user_username UNIQUE (username)
);
CREATE UNIQUE INDEX uq_app_user_email ON app_user (lower(email)) WHERE email IS NOT NULL;
CREATE TRIGGER trg_app_user_touch BEFORE UPDATE ON app_user
    FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

CREATE TABLE user_role (
    user_id     uuid NOT NULL REFERENCES app_user (id),
    role_code   text NOT NULL REFERENCES role (code),
    granted_at  timestamptz NOT NULL DEFAULT now(),
    granted_by  uuid REFERENCES app_user (id),
    PRIMARY KEY (user_id, role_code)
);

-- =====================================================================
-- 会话 — SRS-ACC-002/003/004, INV-024, AC-SEC-05/06
-- 同一账户多 Session 只按 1 个活动账户计数 → 计数用 DISTINCT user_id
-- =====================================================================
CREATE TABLE user_session (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       uuid NOT NULL REFERENCES app_user (id),
    token_hash    text NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    revoked_at    timestamptz,
    revoked_by    uuid REFERENCES app_user (id),
    revoke_reason text,
    client_ip     text,
    user_agent    text,
    CONSTRAINT uq_user_session_token UNIQUE (token_hash)
);
CREATE INDEX idx_user_session_active ON user_session (user_id)
    WHERE revoked_at IS NULL;
CREATE INDEX idx_user_session_expiry ON user_session (expires_at)
    WHERE revoked_at IS NULL;

-- 并发许可计数锁: 登录判定用 pg_advisory_xact_lock(该常量) 串行化
-- 常量 811001 记录于此以免与其它 advisory lock 冲突
COMMENT ON TABLE user_session IS
    '活动会话。并发许可判定见 app.services.licensing，使用 advisory lock 811001 串行化。';

-- =====================================================================
-- 系统设置
-- =====================================================================
CREATE TABLE system_setting (
    key           text PRIMARY KEY,
    value         text NOT NULL,
    value_type    text NOT NULL DEFAULT 'STRING'
                  CHECK (value_type IN ('STRING', 'INTEGER', 'BOOLEAN')),
    description   text,
    is_locked     boolean NOT NULL DEFAULT false,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    updated_by    uuid REFERENCES app_user (id)
);

-- =====================================================================
-- 审计日志 — SRS-AUD-001/002, INV-020, AC-SEC-07
-- 只允许 INSERT 与 SELECT; UPDATE/DELETE 由触发器拒绝
-- =====================================================================
CREATE TABLE audit_log (
    id            bigserial PRIMARY KEY,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    user_id       uuid,
    username      text,
    action        text NOT NULL,
    object_type   text,
    object_id     text,
    object_code   text,
    old_value     jsonb,
    new_value     jsonb,
    reason        text,
    session_id    uuid,
    client_ip     text,
    request_id    text,
    result        text NOT NULL DEFAULT 'SUCCESS'
                  CHECK (result IN ('SUCCESS', 'DENIED', 'FAILURE'))
);
CREATE INDEX idx_audit_log_object ON audit_log (object_type, object_id, occurred_at DESC);
CREATE INDEX idx_audit_log_user ON audit_log (user_id, occurred_at DESC);
CREATE INDEX idx_audit_log_action ON audit_log (action, occurred_at DESC);

CREATE TRIGGER trg_audit_log_immutable
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION dcms_block_write();
