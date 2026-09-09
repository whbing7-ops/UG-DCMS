-- =====================================================================
-- UG-DCMS 迁移 0006 — 系统不变量的数据库级强制
-- 基准依据: UG-DCMS-IVV-001 §2 INV-001~INV-025 / §4 数据完整性验收
-- 原则: AC-DATA-01 "数据库唯一约束实际生效, 不仅由前端检查"
--       本迁移中的每个触发器都标注其对应不变量编号, 便于验收追溯
-- =====================================================================

-- ---------------------------------------------------------------------
-- 标识符归一化函数 — UI §12: 43025-0400 / 0430250400 / 430250400 互相匹配
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_normalize_identifier(p_value text)
RETURNS text AS $$
    SELECT ltrim(upper(regexp_replace(coalesce(p_value, ''), '[^0-9A-Za-z]', '', 'g')), '0');
$$ LANGUAGE sql IMMUTABLE;

CREATE OR REPLACE FUNCTION dcms_fill_cross_reference_norm() RETURNS trigger AS $$
BEGIN
    NEW.normalized_value := dcms_normalize_identifier(NEW.reference_value);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cross_reference_norm
    BEFORE INSERT OR UPDATE ON cross_reference
    FOR EACH ROW EXECUTE FUNCTION dcms_fill_cross_reference_norm();

-- =====================================================================
-- INV-004  已 ALLOCATED 或 CANCELLED 的号码永久不得复用
-- 配套: SRS-NUM-002/003, AT-001
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_guard_number_allocation() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-004: 号码占用记录不得删除 (% %)',
            OLD.number_type, OLD.allocated_number USING ERRCODE = '23514';
    END IF;

    -- 号码本体与作用域永久固定
    IF (NEW.number_type, NEW.basic_drawing_family_id, NEW.allocated_number, NEW.numeric_sequence)
       IS DISTINCT FROM
       (OLD.number_type, OLD.basic_drawing_family_id, OLD.allocated_number, OLD.numeric_sequence)
    THEN
        RAISE EXCEPTION 'DCMS-INV-004: 号码 % 的类型/作用域/号码值不可修改',
            OLD.allocated_number USING ERRCODE = '23514';
    END IF;

    -- 状态不得回退到可再分配状态
    IF OLD.status IN ('ALLOCATED', 'CANCELLED') AND NEW.status = 'RESERVED' THEN
        RAISE EXCEPTION 'DCMS-INV-004: 号码 % 已为 %, 不得退回 RESERVED 以供复用',
            OLD.allocated_number, OLD.status USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'CANCELLED' AND NEW.status <> 'CANCELLED' THEN
        RAISE EXCEPTION 'DCMS-INV-004: 号码 % 已作废, 不得再次启用',
            OLD.allocated_number USING ERRCODE = '23514';
    END IF;

    -- 已分配号码的归属对象不可改指 (改指即等于复用)
    IF OLD.status = 'ALLOCATED'
       AND (NEW.target_object_type, NEW.target_object_id)
           IS DISTINCT FROM (OLD.target_object_type, OLD.target_object_id)
    THEN
        RAISE EXCEPTION 'DCMS-INV-004: 号码 % 已分配给 %, 不得改指其它对象',
            OLD.allocated_number, OLD.target_object_id USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_number_allocation_guard
    BEFORE UPDATE OR DELETE ON number_allocation
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_number_allocation();

-- =====================================================================
-- INV-007  Released File Revision 不得覆盖原物理文件
-- 已发布版次本体冻结, 仅允许 RELEASED -> SUPERSEDED 状态迁移
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_guard_file_revision() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('RELEASED', 'SUPERSEDED') THEN
            RAISE EXCEPTION 'DCMS-INV-007: 已发布版次 Rev.% 不得删除', OLD.revision_number
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status IN ('RELEASED', 'SUPERSEDED') THEN
        IF (NEW.design_file_id, NEW.revision_number, NEW.revision_sequence, NEW.revision_date,
            NEW.change_summary, NEW.change_package_id, NEW.prepared_by, NEW.checked_by,
            NEW.approved_by, NEW.released_at)
           IS DISTINCT FROM
           (OLD.design_file_id, OLD.revision_number, OLD.revision_sequence, OLD.revision_date,
            OLD.change_summary, OLD.change_package_id, OLD.prepared_by, OLD.checked_by,
            OLD.approved_by, OLD.released_at)
        THEN
            RAISE EXCEPTION 'DCMS-INV-007: 已发布版次 Rev.% 内容冻结, 变化必须新建版次',
                OLD.revision_number USING ERRCODE = '23514';
        END IF;
        IF NEW.status NOT IN ('RELEASED', 'SUPERSEDED') THEN
            RAISE EXCEPTION 'DCMS-INV-007: 已发布版次 Rev.% 不得退回 % 状态',
                OLD.revision_number, NEW.status USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_file_revision_guard
    BEFORE UPDATE OR DELETE ON file_revision
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_file_revision();

-- 附件层: 已发布版次的附件不得新增/替换/删除 (AC-FILE-01)
-- 唯一例外是完整性巡检结果字段 (AC-DATA-05 需要回写 OK/MISMATCH)
CREATE OR REPLACE FUNCTION dcms_guard_revision_attachment() RETURNS trigger AS $$
DECLARE
    v_status text;
    v_rev    text;
BEGIN
    SELECT status, revision_number INTO v_status, v_rev
    FROM file_revision
    WHERE id = COALESCE(NEW.file_revision_id, OLD.file_revision_id);

    IF v_status NOT IN ('RELEASED', 'SUPERSEDED') THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF TG_OP = 'INSERT' THEN
        RAISE EXCEPTION 'DCMS-INV-007: 版次 Rev.% 已发布, 不得追加附件', v_rev
            USING ERRCODE = '23514';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-007: 版次 Rev.% 已发布, 附件不得删除', v_rev
            USING ERRCODE = '23514';
    ELSE
        IF (NEW.file_revision_id, NEW.attachment_role, NEW.filename, NEW.storage_key,
            NEW.mime_type, NEW.size_bytes, NEW.sha256, NEW.uploaded_by, NEW.uploaded_at)
           IS DISTINCT FROM
           (OLD.file_revision_id, OLD.attachment_role, OLD.filename, OLD.storage_key,
            OLD.mime_type, OLD.size_bytes, OLD.sha256, OLD.uploaded_by, OLD.uploaded_at)
        THEN
            RAISE EXCEPTION 'DCMS-INV-007: 版次 Rev.% 已发布, 附件不得覆盖 (仅允许回写完整性巡检结果)',
                v_rev USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_revision_attachment_guard
    BEFORE INSERT OR UPDATE OR DELETE ON revision_attachment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_revision_attachment();

-- =====================================================================
-- INV-008  BOM 不得自引用
-- INV-009  BOM 不得形成任意层级循环
-- 配套: CK-03, AC-BOM-01, AT-002。服务端强制 (ERD §8)
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_guard_bom_line() RETURNS trigger AS $$
DECLARE
    v_parent      uuid;
    v_status      text;
    v_parent_code text;
    v_path        text;
BEGIN
    SELECT parent_design_object_id, status INTO v_parent, v_status
    FROM bom_header WHERE id = NEW.bom_header_id;

    IF v_status <> 'WORKING' THEN
        RAISE EXCEPTION 'DCMS-BOM: 非 WORKING 状态的 BOM 表头不可编辑 (header=%)', NEW.bom_header_id
            USING ERRCODE = '23514';
    END IF;

    -- INV-008 / CK-03
    IF v_parent = NEW.child_design_object_id THEN
        SELECT object_code INTO v_parent_code FROM design_object WHERE id = v_parent;
        RAISE EXCEPTION 'DCMS-INV-008: BOM 不得自引用 (父项与子项同为 %)', v_parent_code
            USING ERRCODE = '23514';
    END IF;

    -- INV-009: 从新子项向下遍历, 若能回到父项即构成循环
    WITH RECURSIVE descend (obj, depth, path) AS (
        SELECT NEW.child_design_object_id, 1,
               (SELECT object_code FROM design_object WHERE id = NEW.child_design_object_id)
        UNION ALL
        SELECT bl.child_design_object_id, d.depth + 1,
               d.path || ' -> ' || dobj.object_code
        FROM descend d
        JOIN bom_header bh ON bh.parent_design_object_id = d.obj AND bh.status = 'WORKING'
        JOIN bom_line   bl ON bl.bom_header_id = bh.id
        JOIN design_object dobj ON dobj.id = bl.child_design_object_id
        WHERE d.depth < 500
    )
    SELECT path INTO v_path FROM descend WHERE obj = v_parent LIMIT 1;

    IF v_path IS NOT NULL THEN
        SELECT object_code INTO v_parent_code FROM design_object WHERE id = v_parent;
        RAISE EXCEPTION 'DCMS-INV-009: BOM 形成循环: % -> %', v_parent_code, v_path
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bom_line_guard
    BEFORE INSERT OR UPDATE ON bom_line
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_bom_line();

-- =====================================================================
-- INV-015  正式 Baseline 的 BOM 必须是不可变 Snapshot
-- Snapshot 形成后禁止 UPDATE/DELETE (ERD §6)
-- =====================================================================
CREATE TRIGGER trg_bom_snapshot_immutable
    BEFORE UPDATE OR DELETE ON bom_snapshot
    FOR EACH ROW EXECUTE FUNCTION dcms_block_write();

CREATE OR REPLACE FUNCTION dcms_guard_bom_snapshot_line() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- 只允许在快照刚建立、尚未被任何基线引用时写入行
        IF EXISTS (SELECT 1 FROM baseline_item WHERE bom_snapshot_id = NEW.bom_snapshot_id) THEN
            RAISE EXCEPTION 'DCMS-INV-015: 快照已被基线引用, 不得追加行'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'DCMS-INV-015: BOM 快照行不可修改或删除' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_bom_snapshot_line_guard
    BEFORE INSERT OR UPDATE OR DELETE ON bom_snapshot_line
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_bom_snapshot_line();

-- =====================================================================
-- INV-011  Released Baseline 不可修改或删除
-- 仅允许 RELEASED -> SUPERSEDED 与 is_current 切换 (SRS-BL-003, AT-004)
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_guard_design_baseline() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('RELEASED', 'SUPERSEDED') THEN
            RAISE EXCEPTION 'DCMS-INV-011: 已发布基线 % 不得删除', OLD.baseline_code
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status IN ('RELEASED', 'SUPERSEDED') THEN
        IF (NEW.part_number_id, NEW.baseline_sequence, NEW.baseline_code, NEW.reason,
            NEW.change_package_id, NEW.copied_from_baseline_id, NEW.content_hash,
            NEW.prepared_by, NEW.approved_by, NEW.released_at)
           IS DISTINCT FROM
           (OLD.part_number_id, OLD.baseline_sequence, OLD.baseline_code, OLD.reason,
            OLD.change_package_id, OLD.copied_from_baseline_id, OLD.content_hash,
            OLD.prepared_by, OLD.approved_by, OLD.released_at)
        THEN
            RAISE EXCEPTION 'DCMS-INV-011: 已发布基线 % 内容冻结, 变化必须创建新基线',
                OLD.baseline_code USING ERRCODE = '23514';
        END IF;
        IF NEW.status NOT IN ('RELEASED', 'SUPERSEDED') THEN
            RAISE EXCEPTION 'DCMS-INV-011: 已发布基线 % 不得退回 % 状态',
                OLD.baseline_code, NEW.status USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_design_baseline_guard
    BEFORE UPDATE OR DELETE ON design_baseline
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_design_baseline();

-- 基线明细: 父基线离开 DRAFT/IN_REVIEW 后完全冻结 (AT-004)
CREATE OR REPLACE FUNCTION dcms_guard_baseline_item() RETURNS trigger AS $$
DECLARE
    v_status text;
    v_code   text;
BEGIN
    SELECT status, baseline_code INTO v_status, v_code
    FROM design_baseline
    WHERE id = COALESCE(NEW.design_baseline_id, OLD.design_baseline_id);

    IF v_status NOT IN ('DRAFT', 'IN_REVIEW') THEN
        RAISE EXCEPTION 'DCMS-INV-011: 基线 % 状态为 %, 其明细不可 %',
            v_code, v_status, TG_OP USING ERRCODE = '23514';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_baseline_item_guard
    BEFORE INSERT OR UPDATE OR DELETE ON baseline_item
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_baseline_item();

-- =====================================================================
-- 基线发布前置校验 — AC-DATA-03/04, INV-017, INV-022, SRS-BL-007
-- 在 DRAFT/IN_REVIEW -> RELEASED 的状态迁移上强制
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_validate_baseline_release() RETURNS trigger AS $$
DECLARE
    v_bad   text;
    v_count integer;
BEGIN
    IF NEW.status <> 'RELEASED' OR OLD.status = 'RELEASED' THEN
        RETURN NEW;
    END IF;

    -- 必须有明细
    SELECT count(*) INTO v_count FROM baseline_item WHERE design_baseline_id = NEW.id;
    IF v_count = 0 THEN
        RAISE EXCEPTION 'DCMS-BL: 基线 % 无任何明细, 不得发布', NEW.baseline_code
            USING ERRCODE = '23514';
    END IF;

    -- AC-DATA-03: 所有 FILE_REVISION 必须为 RELEASED
    SELECT string_agg(df.file_number || ' Rev.' || fr.revision_number || '(' || fr.status || ')', ', ')
      INTO v_bad
    FROM baseline_item bi
    JOIN file_revision fr ON fr.id = bi.file_revision_id
    JOIN design_file df ON df.id = fr.design_file_id
    WHERE bi.design_baseline_id = NEW.id AND fr.status NOT IN ('RELEASED', 'SUPERSEDED');
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AC-DATA-03: 基线 % 引用了未发布版次: %', NEW.baseline_code, v_bad
            USING ERRCODE = '23514';
    END IF;

    -- AC-DATA-04 / INV-017: 所有外部技术状态必须为 ACCEPTED
    SELECT string_agg(ep.external_part_number || ' TS' || ets.state_sequence || '(' || ets.status || ')', ', ')
      INTO v_bad
    FROM baseline_item bi
    JOIN external_technical_state ets ON ets.id = bi.external_technical_state_id
    JOIN external_part ep ON ep.id = ets.external_part_id
    WHERE bi.design_baseline_id = NEW.id AND ets.status <> 'ACCEPTED';
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-017: 基线 % 引用了非 ACCEPTED 外部技术状态: %',
            NEW.baseline_code, v_bad USING ERRCODE = '23514';
    END IF;

    -- 软件版本必须已发布
    SELECT string_agg(so.software_number || ' ' || sv.version || '(' || sv.status || ')', ', ')
      INTO v_bad
    FROM baseline_item bi
    JOIN software_version sv ON sv.id = bi.software_version_id
    JOIN software_object so ON so.id = sv.software_object_id
    WHERE bi.design_baseline_id = NEW.id AND sv.status NOT IN ('RELEASED', 'SUPERSEDED');
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-BL: 基线 % 引用了未发布软件版本: %', NEW.baseline_code, v_bad
            USING ERRCODE = '23514';
    END IF;

    -- INV-022: Released P/N 必须有主设计定义
    SELECT count(*) INTO v_count
    FROM baseline_item
    WHERE design_baseline_id = NEW.id
      AND item_type = 'FILE_REVISION'
      AND item_role = 'PRIMARY_DEFINITION';
    IF v_count = 0 THEN
        RAISE EXCEPTION 'DCMS-INV-022: 基线 % 缺少 PRIMARY_DEFINITION 主设计定义, 不得发布',
            NEW.baseline_code USING ERRCODE = '23514';
    END IF;
    IF v_count > 1 THEN
        RAISE EXCEPTION 'DCMS-INV-022: 基线 % 存在 % 个主设计定义, 只允许 1 个',
            NEW.baseline_code, v_count USING ERRCODE = '23514';
    END IF;

    -- 最多一个 BOM 快照
    SELECT count(*) INTO v_count
    FROM baseline_item WHERE design_baseline_id = NEW.id AND item_type = 'BOM_SNAPSHOT';
    IF v_count > 1 THEN
        RAISE EXCEPTION 'DCMS-INV-015: 基线 % 存在 % 个 BOM 快照, 只允许 1 个',
            NEW.baseline_code, v_count USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_baseline_release_validate
    BEFORE UPDATE ON design_baseline
    FOR EACH ROW EXECUTE FUNCTION dcms_validate_baseline_release();

-- =====================================================================
-- INV-013  每个 Released 内部 P/N 同一时刻必须且只能有一个 Current Baseline
-- FK-03 / AC-DATA-02: Current Baseline 必须属于该 P/N
-- (唯一性由 0004 的 uq_baseline_one_current 局部唯一索引保证)
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_check_part_current_baseline() RETURNS trigger AS $$
DECLARE
    v_owner  uuid;
    v_status text;
    v_curr   boolean;
BEGIN
    IF NEW.current_baseline_id IS NOT NULL THEN
        SELECT part_number_id, status, is_current
          INTO v_owner, v_status, v_curr
        FROM design_baseline WHERE id = NEW.current_baseline_id;

        IF v_owner IS DISTINCT FROM NEW.id THEN
            RAISE EXCEPTION 'DCMS-AC-DATA-02: 基线 % 不属于 P/N %, 拒绝作为 Current Baseline',
                NEW.current_baseline_id, NEW.full_part_number USING ERRCODE = '23514';
        END IF;
        IF v_status <> 'RELEASED' THEN
            RAISE EXCEPTION 'DCMS-INV-013: Current Baseline 必须为 RELEASED (当前 %)', v_status
                USING ERRCODE = '23514';
        END IF;
        IF NOT v_curr THEN
            RAISE EXCEPTION 'DCMS-INV-013: 基线未标记 is_current, 不得作为 P/N % 的当前基线',
                NEW.full_part_number USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.lifecycle_status = 'RELEASED' THEN
        RAISE EXCEPTION 'DCMS-INV-013: 已发布 P/N % 必须有 Current Baseline', NEW.full_part_number
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 约束触发器: 延迟到事务提交时校验, 使"新基线发布 -> 切换 Current"可在单事务内完成 (ERD §8)
CREATE CONSTRAINT TRIGGER trg_part_current_baseline_check
    AFTER INSERT OR UPDATE ON part_number
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION dcms_check_part_current_baseline();

-- =====================================================================
-- INV-019  Released Design Object 不得物理删除
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_guard_released_delete() RETURNS trigger AS $$
DECLARE
    v_code text;
    v_row  jsonb;
BEGIN
    IF OLD.lifecycle_status IN ('RELEASED', 'OBSOLETE') THEN
        -- 该函数被多张表复用, 各表字段不同; 用 jsonb 取值避免引用不存在的字段
        v_row  := to_jsonb(OLD);
        v_code := COALESCE(v_row ->> 'object_code',
                           v_row ->> 'full_part_number',
                           v_row ->> 'external_part_number',
                           v_row ->> 'software_number',
                           v_row ->> 'id');
        RAISE EXCEPTION 'DCMS-INV-019: 已发布/已废止对象 % 不得物理删除 (状态 %)',
            v_code, OLD.lifecycle_status USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_design_object_no_delete BEFORE DELETE ON design_object
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_released_delete();
CREATE TRIGGER trg_part_number_no_delete BEFORE DELETE ON part_number
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_released_delete();
CREATE TRIGGER trg_external_part_no_delete BEFORE DELETE ON external_part
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_released_delete();
CREATE TRIGGER trg_software_object_no_delete BEFORE DELETE ON software_object
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_released_delete();

-- 设计族: 一经批准生效即不得删除 (基本图号永不复用)
CREATE OR REPLACE FUNCTION dcms_guard_family_delete() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('ACTIVE', 'CLOSED') THEN
        RAISE EXCEPTION 'DCMS-INV-019: 已生效设计族 % 不得物理删除', OLD.basic_drawing_number
            USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_no_delete BEFORE DELETE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_family_delete();

-- 外部技术状态与软件版本: 一经 ACCEPTED/RELEASED 即冻结
CREATE OR REPLACE FUNCTION dcms_guard_accepted_state() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('ACCEPTED', 'RELEASED', 'SUPERSEDED') THEN
            RAISE EXCEPTION 'DCMS-IMMUTABLE: % 中已确认的技术状态/版本不得删除', TG_TABLE_NAME
                USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status IN ('ACCEPTED', 'RELEASED')
       AND NEW.status NOT IN ('ACCEPTED', 'RELEASED', 'SUPERSEDED') THEN
        RAISE EXCEPTION 'DCMS-IMMUTABLE: % 中已确认状态不得退回 %', TG_TABLE_NAME, NEW.status
            USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('ACCEPTED', 'RELEASED', 'SUPERSEDED')
       AND NEW.hash_sha256 IS DISTINCT FROM OLD.hash_sha256 THEN
        RAISE EXCEPTION 'DCMS-IMMUTABLE: % 中已确认记录的 Hash 不可修改', TG_TABLE_NAME
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ext_ts_guard BEFORE UPDATE OR DELETE ON external_technical_state
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_accepted_state();
CREATE TRIGGER trg_software_version_guard BEFORE UPDATE OR DELETE ON software_version
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_accepted_state();

-- =====================================================================
-- INV-025  申请人不得作为同一对象最终批准人 — SRS-ACC-006, AC-SEC-04
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_check_approval_separation() RETURNS trigger AS $$
DECLARE
    v_requester uuid;
    v_req_no    text;
BEGIN
    SELECT requester_id, request_number INTO v_requester, v_req_no
    FROM approval_request WHERE id = NEW.approval_request_id;

    IF NEW.is_final AND NEW.assignee_user_id IS NOT NULL
       AND NEW.assignee_user_id = v_requester THEN
        RAISE EXCEPTION 'DCMS-INV-025: 申请 % 的申请人不得被指派为最终批准人', v_req_no
            USING ERRCODE = '23514';
    END IF;

    IF NEW.is_final AND NEW.decision <> 'PENDING'
       AND NEW.decided_by IS NOT NULL AND NEW.decided_by = v_requester THEN
        RAISE EXCEPTION 'DCMS-INV-025: 申请 % 的申请人不得作为最终批准人作出 % 决定',
            v_req_no, NEW.decision USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_approval_separation
    BEFORE INSERT OR UPDATE ON approval_step
    FOR EACH ROW EXECUTE FUNCTION dcms_check_approval_separation();

-- 审批步骤一经作出决定即不可改写 (审计完整性)
CREATE OR REPLACE FUNCTION dcms_guard_approval_step() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.decision <> 'PENDING' THEN
            RAISE EXCEPTION 'DCMS-AUDIT: 已作出决定的审批步骤不得删除' USING ERRCODE = '23514';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.decision <> 'PENDING' THEN
        IF (NEW.decision, NEW.decided_by, NEW.acted_at, NEW.comments, NEW.step_order, NEW.is_final)
           IS DISTINCT FROM
           (OLD.decision, OLD.decided_by, OLD.acted_at, OLD.comments, OLD.step_order, OLD.is_final)
        THEN
            RAISE EXCEPTION 'DCMS-AUDIT: 审批步骤 % 已作出决定, 不得改写', OLD.step_order
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_approval_step_guard
    BEFORE UPDATE OR DELETE ON approval_step
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_approval_step();

-- =====================================================================
-- AC-DATA-07  字典 DEPRECATED 后历史数据仍可查看, 新对象不能再选择
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_check_family_dictionary_active() RETURNS trigger AS $$
DECLARE
    v_bad text;
BEGIN
    -- 仅在新建或换词时校验; 历史对象不受词条废止影响
    IF TG_OP = 'UPDATE'
       AND (NEW.core_term_id, NEW.qualifier_1_id, NEW.qualifier_2_id, NEW.physical_class_id)
           IS NOT DISTINCT FROM
           (OLD.core_term_id, OLD.qualifier_1_id, OLD.qualifier_2_id, OLD.physical_class_id)
    THEN
        RETURN NEW;
    END IF;

    SELECT name_cn INTO v_bad FROM naming_core_term
     WHERE id = NEW.core_term_id AND status = 'DEPRECATED';
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AC-DATA-07: 核心实体词 "%" 已废止, 不可用于新对象', v_bad
            USING ERRCODE = '23514';
    END IF;

    SELECT name_cn INTO v_bad FROM naming_qualifier
     WHERE id IN (NEW.qualifier_1_id, NEW.qualifier_2_id) AND status = 'DEPRECATED' LIMIT 1;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AC-DATA-07: 稳定限定词 "%" 已废止, 不可用于新对象', v_bad
            USING ERRCODE = '23514';
    END IF;

    SELECT name_cn INTO v_bad FROM physical_class
     WHERE id = NEW.physical_class_id AND status = 'DEPRECATED';
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-AC-DATA-07: 二级物理分类 "%" 已废止, 不可用于新对象', v_bad
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_dictionary_active
    BEFORE INSERT OR UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_check_family_dictionary_active();

-- =====================================================================
-- 一致性辅助: design_object 与其具体身份表的 object_type 必须匹配
-- 支撑 INV-001 / INV-005
-- =====================================================================
CREATE OR REPLACE FUNCTION dcms_check_object_type_match() RETURNS trigger AS $$
DECLARE
    v_type     text;
    v_expected text;
BEGIN
    v_expected := CASE TG_TABLE_NAME
                      WHEN 'part_number'     THEN 'INTERNAL_PART'
                      WHEN 'external_part'   THEN 'EXTERNAL_PART'
                      WHEN 'software_object' THEN 'SOFTWARE'
                  END;
    SELECT object_type INTO v_type FROM design_object WHERE id = NEW.design_object_id;
    IF v_type <> v_expected THEN
        RAISE EXCEPTION 'DCMS-INV-001: % 必须映射 object_type=% 的设计对象 (实际 %)',
            TG_TABLE_NAME, v_expected, v_type USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_part_number_type_match BEFORE INSERT OR UPDATE ON part_number
    FOR EACH ROW EXECUTE FUNCTION dcms_check_object_type_match();
CREATE TRIGGER trg_external_part_type_match BEFORE INSERT OR UPDATE ON external_part
    FOR EACH ROW EXECUTE FUNCTION dcms_check_object_type_match();
CREATE TRIGGER trg_software_object_type_match BEFORE INSERT OR UPDATE ON software_object
    FOR EACH ROW EXECUTE FUNCTION dcms_check_object_type_match();

-- =====================================================================
-- 不变量登记表 — 供 IVV 验收追溯 (IVV §8: 五份基准与实现可追溯)
-- =====================================================================
CREATE TABLE invariant_registry (
    code            text PRIMARY KEY,
    statement_cn    text NOT NULL,
    enforced_by     text NOT NULL
                    CHECK (enforced_by IN ('DB_CONSTRAINT', 'DB_TRIGGER',
                                           'DB_STRUCTURE', 'APP_SERVICE', 'DB_AND_APP')),
    enforcement_ref text NOT NULL,
    test_ref        text
);

INSERT INTO invariant_registry (code, statement_cn, enforced_by, enforcement_ref, test_ref) VALUES
('INV-001', '一个 Full P/N 只能对应一个 DesignObject', 'DB_CONSTRAINT',
 'uq_part_number_design_object / uq_full_part_number / uq_design_object_code / trg_*_type_match', 'test_inv_001'),
('INV-002', '一个 Basic Drawing 内同一 Dash 只能使用一次', 'DB_CONSTRAINT',
 'uq_part_family_dash', 'test_inv_002'),
('INV-003', 'Dash 000 永远禁止', 'DB_CONSTRAINT', 'ck_dash_range', 'test_inv_003'),
('INV-004', '已 ALLOCATED 或 CANCELLED 的号码永久不得复用', 'DB_AND_APP',
 'trg_number_allocation_guard / uq_number_allocation / NumberService', 'test_inv_004'),
('INV-005', 'Basic Drawing 与 Dash P/N 是独立实体', 'DB_STRUCTURE',
 'basic_drawing_family 与 part_number 分表 1:N', 'test_inv_005'),
('INV-006', '文件身份与 File Revision 必须分离', 'DB_STRUCTURE',
 'design_file 与 file_revision 分表', 'test_inv_006'),
('INV-007', 'Released File Revision 不得覆盖原物理文件', 'DB_TRIGGER',
 'trg_file_revision_guard / trg_revision_attachment_guard', 'test_inv_007'),
('INV-008', 'BOM 不得自引用', 'DB_TRIGGER', 'trg_bom_line_guard', 'test_inv_008'),
('INV-009', 'BOM 不得形成任意层级循环', 'DB_TRIGGER', 'trg_bom_line_guard', 'test_inv_009'),
('INV-010', 'QTY/ITEM/Designator 属于 BOM 关系, 不属于 Part Master', 'DB_STRUCTURE',
 '字段仅存在于 bom_line / bom_snapshot_line', 'test_inv_010'),
('INV-011', 'Released Baseline 不可修改或删除', 'DB_TRIGGER',
 'trg_design_baseline_guard / trg_baseline_item_guard', 'test_inv_011'),
('INV-012', 'Baseline 不得包含 Latest/Current 等浮动版次引用', 'DB_STRUCTURE',
 'baseline_item 仅有确定版次外键 + ck_baseline_item_target', 'test_inv_012'),
('INV-013', '每个 Released 内部 P/N 同一时刻必须且只能有一个 Current Baseline', 'DB_AND_APP',
 'uq_baseline_one_current / trg_part_current_baseline_check', 'test_inv_013'),
('INV-014', '文件新 Revision 发布不得自动改变任何 P/N 当前技术状态', 'APP_SERVICE',
 'FileRevisionService.release 不触碰 part_number.current_baseline_id', 'test_inv_014'),
('INV-015', '正式 Baseline 的 BOM 必须是不可变 Snapshot', 'DB_TRIGGER',
 'trg_bom_snapshot_immutable / trg_bom_snapshot_line_guard', 'test_inv_015'),
('INV-016', 'External Part 必须以 Namespace + External P/N 唯一识别', 'DB_CONSTRAINT',
 'uq_external_part_ns_pn', 'test_inv_016'),
('INV-017', '进入 Baseline 的外部件必须引用 Accepted Technical State', 'DB_TRIGGER',
 'trg_baseline_release_validate', 'test_inv_017'),
('INV-018', 'Software 对象身份与 Version 分离', 'DB_STRUCTURE',
 'software_object 与 software_version 分表', 'test_inv_018'),
('INV-019', 'Released Design Object 不得物理删除', 'DB_TRIGGER',
 'trg_*_no_delete', 'test_inv_019'),
('INV-020', 'Audit Log 不得由普通业务账户 UPDATE/DELETE', 'DB_TRIGGER',
 'trg_audit_log_immutable + 数据库角色权限', 'test_inv_020'),
('INV-021', '同一 DesignObject 最多一个 PRIMARY 功能', 'DB_CONSTRAINT',
 'uq_object_function_primary (partial unique index)', 'test_inv_021'),
('INV-022', 'Released P/N 必须有 Primary Design Definition', 'DB_TRIGGER',
 'trg_baseline_release_validate / uq_definition_primary', 'test_inv_022'),
('INV-023', '基本图号核心实体词必须来自受控词典', 'DB_CONSTRAINT',
 'basic_drawing_family.core_term_id NOT NULL FK -> naming_core_term', 'test_inv_023'),
('INV-024', '账户数量不限, 但不同活动账户同时最多 10 个', 'APP_SERVICE',
 'LicenseService (advisory lock 811001) + system_setting.max_concurrent_accounts', 'test_inv_024'),
('INV-025', '申请人不得作为同一对象最终批准人', 'DB_TRIGGER',
 'trg_approval_separation', 'test_inv_025');

CREATE TRIGGER trg_invariant_registry_immutable
    BEFORE DELETE ON invariant_registry
    FOR EACH ROW EXECUTE FUNCTION dcms_block_write();
