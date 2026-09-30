-- DOA 符合性检查单主数据（体系级）。
--
-- 依据 UG-RPT-2026-003《DAS 运行模块设计输入清单》G 版第十一章的范围输入基线，
-- 以及该文件第 8.9 节的判据 EV1～EV9-2 与第十一章新增的判据 S1～S4。
-- 对象是 UG-DAM-01-附3《DOA 符合性检查单》: 66 条要求, 证明本单位的设计保证体系
-- 符合 CCAR-21 和 AP-21-18 附录D; 格式依据 AP-21-18 附录E。
--
-- 【判据 S1 —— 本迁移最重要的一条边界】
-- 体系文件中有两个都叫"符合性检查单"的东西, 不是一回事, 本迁移只建前者:
--   1) UG-DAM-01-附3  体系级, 66 条, 保存期限"长期"          → 本迁移
--   2) UG-DAP-07 步骤1 项目级, 按审定基础逐条列, 保存期限"产品/设计全生命周期"
--                                                              → 归 M3, 另建实体
-- 两者列结构、生命周期和保存期限都不同。合并成一张表的后果是双向的:
-- 体系级条目会被项目生命周期的保留规则误处置(判据 R2、R8);
-- 项目级条目会被当作体系符合性证据提交局方(判据 EV4-2 的提交口径)。
--
-- 可重复执行; 不改动任何已有业务数据。

-- ---------------------------------------------------------------------
-- 1. 检查单条目（主数据锚点）
--    66 条相对稳定, 随体系更改而更新(UG-DAP-02 步骤7)。
--    适用性两列决定"全部适用项"的分母(判据 EV5): 标为不适用的须写理由,
--    不计入覆盖率分母, 但理由本身要可复核。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_item (
    id              bigserial PRIMARY KEY,
    seq             integer     NOT NULL UNIQUE,        -- 序号, 对应附3 主表 A 列
    req_code        text        NOT NULL,               -- 要求编号, 如 CCAR-21.5
    req_name        text        NOT NULL,               -- 要求名称
    req_text        text,                               -- 要求内容/完整原文定位
    req_source      text        NOT NULL,               -- CCAR-21 / AP-21-18附录D / AC-21-48
    -- 适用性取三值, 不是布尔。附3 实际用的就是 是/否/部分 三种(部分 16 条),
    -- 布尔存不下"部分": 归为适用则丢失"只适用一部分"这个须向局方说明的事实,
    -- 归为不适用则覆盖率分母被错误缩小(判据 EV5)。
    applicable_stc  text        NOT NULL DEFAULT '是',
    applicable_pma  text        NOT NULL DEFAULT '是',
    na_reason       text,                               -- 判据 EV5: 两项均为否时须写理由
    note            text,
    -- 附3 00 草案"设计保证系统文件编号、名称、版本"栏的原文, 逐字保留。
    -- 该栏在草案里有至少三种写法(编号+条款/编号+名称+版本+章/UG-DAP-13～15 这类范围),
    -- 无法可靠机器解析。自动解析出来的错引用与对的长得一样, 反而更糟,
    -- 所以条款级引用(das_checklist_doc_ref)一律由人对照本栏逐条转录。
    source_doc_ref_raw text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_applicable_stc CHECK (applicable_stc IN ('是', '否', '部分')),
    CONSTRAINT ck_das_applicable_pma CHECK (applicable_pma IN ('是', '否', '部分')),
    CONSTRAINT ck_das_checklist_na_reason
        CHECK (applicable_stc <> '否' OR applicable_pma <> '否' OR na_reason IS NOT NULL)
);

COMMENT ON TABLE das_checklist_item IS
    'DOA 符合性检查单条目(体系级, UG-DAM-01-附3)。判据 S1: 与 UG-DAP-07 的项目级符合性检查单是不同实体。';
COMMENT ON COLUMN das_checklist_item.applicable_stc IS
    '是/否/部分。"部分"计入覆盖率分母(仍须覆盖), 但须在自评说明中写明适用到什么程度。';
COMMENT ON COLUMN das_checklist_item.na_reason IS
    '判据 EV5: 两个适用性均为否时必须写明理由; 不适用项不计入覆盖率分母, 但理由须可复核。';

-- ---------------------------------------------------------------------
-- 2. 文件栏: 条款级引用（判据 EV1、EV2）
--    这是正式提交局方的第 4 列"设计保证系统文件编号、名称、版本"。
--    填写说明 A12: 格式为 编号 + 名称 + 版本 + 章节号;
--    填写说明 A13: 一条要求由多份文件共同满足的, 逐一列出 → 多对多。
--    版本(doc_version)必填: 不记版本, 体系文件改版后无从判断引用是否还成立。
--    改版不删旧行, 只置 superseded_at, 以保留历史评价的依据(判据 EV9-2)。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_doc_ref (
    id             bigserial PRIMARY KEY,
    item_id        bigint      NOT NULL REFERENCES das_checklist_item (id),
    doc_code       text        NOT NULL,      -- UG-DAP-12
    doc_name       text        NOT NULL,      -- 故障失效缺陷和不安全事件报告程序
    doc_version    text        NOT NULL,      -- 判据 EV1: 版本必填
    clause         text        NOT NULL,      -- 第 6 章第 4 步
    valid_from     date        NOT NULL DEFAULT current_date,
    superseded_at  date,                      -- 置位即失效, 不删行
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_das_ref_item ON das_checklist_doc_ref (item_id);
-- 判据 S2 与 EV2 都要按"文件 + 版本"反查受影响条目, 故建此索引。
CREATE INDEX IF NOT EXISTS idx_das_ref_doc  ON das_checklist_doc_ref (doc_code, doc_version)
    WHERE superseded_at IS NULL;

COMMENT ON TABLE das_checklist_doc_ref IS
    '判据 EV1: 文件栏引用到条款级(编号+名称+版本+章节号), 不引到具体记录; 一条要求可引多份文件。';

-- ---------------------------------------------------------------------
-- 3. 符合性自评（判据 EV3、EV4-3、C5、EV9-2）
--    正式提交局方的第 5 列。填写说明 A17: 说明如何满足该要求, 引用文件的具体
--    章节和做法, **必要时说明证据(记录、表单)** —— 故本表并非只能引条款。
--    填写说明 A26: 首次发布和申请前, 适航管理负责人须结合实际范围、证件和运行
--    证据**逐项**签署确认 → assessed_by / assessed_at 逐条记录。
--    append-only: 改判不覆盖旧行, 新增一行并推进 effective_from(判据 EV9-2、P4)。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_assessment (
    id               bigserial PRIMARY KEY,
    item_id          bigint      NOT NULL REFERENCES das_checklist_item (id),
    conclusion       text        NOT NULL,   -- 符合 / 部分符合 / 不符合
    statement        text        NOT NULL,   -- 具体符合性说明
    improvement_plan text,                   -- 完善计划(结论非"符合"时)
    assessed_by      uuid        NOT NULL REFERENCES app_user (id),
    assessed_at      timestamptz NOT NULL DEFAULT now(),
    effective_from   date        NOT NULL DEFAULT current_date,
    CONSTRAINT ck_das_assessment_conclusion
        CHECK (conclusion IN ('符合', '部分符合', '不符合')),
    -- 判据 C5: 不得因为填写完成就自动改判为"符合"; 结论非"符合"的须给完善计划。
    CONSTRAINT ck_das_assessment_plan
        CHECK (conclusion = '符合' OR improvement_plan IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_das_assessment_item
    ON das_checklist_assessment (item_id, effective_from DESC);

-- 自评为 append-only: 只能新增, 不得改写或删除(判据 EV9-2)。
CREATE OR REPLACE FUNCTION dcms_guard_das_assessment() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-AUDIT: 符合性自评记录不得删除, 改判请新增一行'
            USING ERRCODE = '23514';
    END IF;
    RAISE EXCEPTION 'DCMS-AUDIT: 符合性自评记录不得修改, 改判请新增一行并推进生效日期'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_assessment_append_only ON das_checklist_assessment;
CREATE TRIGGER trg_das_assessment_append_only
    BEFORE UPDATE OR DELETE ON das_checklist_assessment
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_assessment();

-- ---------------------------------------------------------------------
-- 4. 自评说明的证据索引（判据 EV3）
--    填写说明 A17 允许并要求必要时说明证据。这里存的是**索引**, 不是把记录
--    逐条堆进检查单正文。证据可以是记录、表单或监督记录。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_evidence (
    id             bigserial PRIMARY KEY,
    assessment_id  bigint      NOT NULL REFERENCES das_checklist_assessment (id),
    evidence_kind  text        NOT NULL,   -- record / form / surveillance / other
    evidence_ref   text        NOT NULL,   -- 记录编号、表单编号或监督记录编号
    evidence_note  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_evidence_kind
        CHECK (evidence_kind IN ('record', 'form', 'surveillance', 'other'))
);

CREATE INDEX IF NOT EXISTS idx_das_evidence_assessment
    ON das_checklist_evidence (assessment_id);

-- ---------------------------------------------------------------------
-- 5. 独立监督覆盖（判据 EV4、EV4-2）
--    UG-DAP-13 步骤1: 独立监督周期不超过 12 个月, 须覆盖检查单的全部适用项。
--    UG-DAP-13 步骤6: 审核记录须如实记载, **包括符合项和不符合项** →
--    故 result 不是只记不符合。
--    【判据 EV4-2】本表对应的是附3 的**内部辅助列**, 填写说明 A2/A3 规定
--    正式提交局方的只有前 5 列, 第 6 列及以后提交前可隐藏或删除。
--    因此运行证据**不得只存在于本表**, 否则提交出去的检查单将不含任何证据。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_coverage (
    id            bigserial PRIMARY KEY,
    item_id       bigint      NOT NULL REFERENCES das_checklist_item (id),
    audit_ref     text        NOT NULL,   -- 审核/监督记录编号
    audit_kind    text        NOT NULL,   -- internal(独立监督) / authority(局方监督)
    cycle_start   date        NOT NULL,
    covered_at    date        NOT NULL,
    result        text        NOT NULL,   -- 符合项 / 不符合项
    ncr_ref       text,                   -- 不符合项时关联的 NCR 编号
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_das_coverage_kind   CHECK (audit_kind IN ('internal', 'authority')),
    CONSTRAINT ck_das_coverage_result CHECK (result IN ('符合项', '不符合项')),
    CONSTRAINT ck_das_coverage_ncr    CHECK (result = '符合项' OR ncr_ref IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_das_coverage_item
    ON das_checklist_coverage (item_id, cycle_start DESC);

-- ---------------------------------------------------------------------
-- 6. 失效传播标记（判据 EV9、EV9-1）
--    E 版曾认为条款级引用不需要失效检测, 这是错的: 链路变长不等于依赖消失。
--    三个失效源:
--      doc_revised          体系文件改版      → EV2、UG-DAP-02 步骤7
--      authorization_revoked 授权撤销触发的文件复核 → 判据 A4-2、A4-3
--      record_unavailable   记录冻结、作废或到期处置 → 判据 R4～R7
--    【判据 EV9-1】"审核已覆盖"与"证据当前有效"是两个独立状态:
--    覆盖记录(第5节)是历史事实, 不因证据失效而回退; 本表只标记自评待复核。
--    【判据 EV9-2】传播只提示复核, 不静默覆盖历史评价。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_checklist_review_flag (
    id          bigserial PRIMARY KEY,
    item_id     bigint      NOT NULL REFERENCES das_checklist_item (id),
    reason      text        NOT NULL,
    source_ref  text        NOT NULL,   -- 触发源: 文件编号+版本 / 授权ID / 记录编号
    raised_at   timestamptz NOT NULL DEFAULT now(),
    raised_by   uuid        REFERENCES app_user (id),
    cleared_at  timestamptz,
    cleared_by  uuid        REFERENCES app_user (id),
    clear_note  text,
    CONSTRAINT ck_das_flag_reason
        CHECK (reason IN ('doc_revised', 'authorization_revoked', 'record_unavailable'))
);

CREATE INDEX IF NOT EXISTS idx_das_flag_open
    ON das_checklist_review_flag (item_id) WHERE cleared_at IS NULL;

-- ---------------------------------------------------------------------
-- 7. 视图
-- ---------------------------------------------------------------------

-- 7.1 判据 S3: 按提交口径导出——只有前 5 列。
--     填写说明 A2: 正式提交局方的是序号、要求编号和名称、要求内容、
--     设计保证系统文件编号名称版本、符合性自评说明。
--     导出件须另行标明导出日期、操作人和检查单版本(由应用层补, 不在视图内)。
CREATE OR REPLACE VIEW das_checklist_submission AS
SELECT i.seq                                              AS "序号",
       i.req_code || ' ' || i.req_name                    AS "要求编号、名称",
       i.req_text                                         AS "要求内容",
       string_agg(DISTINCT r.doc_code || ' ' || r.doc_name || ' ' ||
                           r.doc_version || ' ' || r.clause, '; ')
                                                          AS "设计保证系统文件编号、名称、版本",
       a.statement                                        AS "符合性自评说明"
  FROM das_checklist_item i
  LEFT JOIN das_checklist_doc_ref r
         ON r.item_id = i.id AND r.superseded_at IS NULL
  LEFT JOIN LATERAL (
         SELECT statement FROM das_checklist_assessment x
          WHERE x.item_id = i.id AND x.effective_from <= current_date
          ORDER BY x.effective_from DESC, x.id DESC LIMIT 1) a ON true
 GROUP BY i.id, i.seq, i.req_code, i.req_name, i.req_text, a.statement
 ORDER BY i.seq;

COMMENT ON VIEW das_checklist_submission IS
    '判据 S3: 提交局方的口径, 只含前 5 列。内部辅助列(适用性、自评结论、独立监督覆盖、备注)一律不在本视图内。';

-- 7.2 判据 S2: 变更评估时按"文件 + 版本"反查受影响的检查单条目。
--     UG-DAP-02 步骤1 要求变更评估说明对适用要求符合性的影响, 不能靠人记得。
CREATE OR REPLACE VIEW das_checklist_doc_impact AS
SELECT r.doc_code, r.doc_version, r.clause,
       i.seq, i.req_code, i.req_name
  FROM das_checklist_doc_ref r
  JOIN das_checklist_item i ON i.id = r.item_id
 WHERE r.superseded_at IS NULL
 ORDER BY r.doc_code, r.doc_version, i.seq;

-- 7.3 判据 EV4、EV5: 覆盖率统计。分母只含适用项。
CREATE OR REPLACE VIEW das_checklist_coverage_stat AS
SELECT c.cycle_start,
       -- 分母含"是"和"部分": 部分适用仍须覆盖, 只有两项都是"否"才排除。
       count(*) FILTER (WHERE i.applicable_stc <> '否' OR i.applicable_pma <> '否')
                                                                             AS "适用项数",
       count(DISTINCT c.item_id)                                            AS "已覆盖项数",
       count(*) FILTER (WHERE c.result = '不符合项')                        AS "不符合项数"
  FROM das_checklist_item i
  LEFT JOIN das_checklist_coverage c ON c.item_id = i.id AND c.audit_kind = 'internal'
 GROUP BY c.cycle_start;

-- 7.4 判据 S4: 证件延续审查所需的关联视图。
--     UG-DAP-15 步骤5: 延续审查确认每 24 个月一周期的计划性监督是否完成、
--     已发现的不符合项是否已制定措施并按计划落实。
CREATE OR REPLACE VIEW das_checklist_renewal_view AS
SELECT i.seq, i.req_code, i.req_name,
       max(c.covered_at) FILTER (WHERE c.audit_kind = 'internal')   AS "最近内部监督",
       max(c.covered_at) FILTER (WHERE c.audit_kind = 'authority')  AS "最近局方监督",
       string_agg(DISTINCT c.ncr_ref, ', ')                         AS "关联不符合项",
       count(f.id) FILTER (WHERE f.cleared_at IS NULL)              AS "未关闭的待复核标记"
  FROM das_checklist_item i
  LEFT JOIN das_checklist_coverage c    ON c.item_id = i.id
  LEFT JOIN das_checklist_review_flag f ON f.item_id = i.id
 WHERE i.applicable_stc <> '否' OR i.applicable_pma <> '否'
 GROUP BY i.id, i.seq, i.req_code, i.req_name
 ORDER BY i.seq;
