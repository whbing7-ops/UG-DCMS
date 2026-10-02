-- ---------------------------------------------------------------------
-- 0050  M8 证后与对外发布（UG-DAP-11，17 步）
-- ---------------------------------------------------------------------
-- 依据 UG-DAP-11《持有人责任履行和资料发布程序》第 6 章（17 步）、第 7 章（关键控制点）、
-- CCAR-21.99（向局方提供设计更改信息）、CCAR-21.116（权益转让）、CCAR-21.119（书面许可）、
-- 设计输入第 8.1 节（M8 挂在**设计批准**对象上, 不挂在项目上）与第 8.3 节（一）。
--
-- 【按 2026-10-02 的决定选闸门】
-- 这份程序的第 7 章关键控制点里有五条"不得", 每条都只要一句话就能判, 而后果都在系统外:
--   ① 已发布的批准和资料**只能由原批准人或其上级撤销**, 不得由编制人或使用部门自行作废;
--   ② **止用通知未取得全部接收确认前, 不得认为撤销已完成**;
--   ③ **撤销期间, 相关资料不得继续用于改装、生产或放行**;
--   ④ 局方批准的资料标明实际批准依据, **不得加注虚假的本单位批准声明**;
--   ⑤ 第 13 步 b): 偏离已批准设计类的询问**按 UG-DAP-06 走设计更改, 不得以答复代替更改**;
--      且**对外答复不得超出已批准的设计数据范围**。
-- 这五条做成闸门。其余 12 步做成记录, 实质过程走线下并挂证据（0047）。
--
-- 【为什么挂在设计批准上而不是项目上】
-- 设计输入第 8.3 节（一）: 证后活动挂在**证件**上而不是项目上 —— 一个产品可能有多个 STC;
-- ICA 修订、服务通告、适航指令建议、21.99 信息、权益转让（转的是证件）、撤销召回,
-- 都按证件走。项目有结束时间, 证件长期有效。所以这里的外键指向 das_design_approval。
--
-- 【第 16 步那个 30 天是法规时限】
-- 原文把它标成"（法规）": 权益转让生效或终止后 **30 天内**书面通知局方。
-- 所以它不是本模块的一个字段, 而是时限引擎（0039）的一条参数 —— 第 9 节补进去,
-- 走规章修订闸门（DCMS-INV-073）, 内部决定改不动它。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 对外发布的资料（第 2～4 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_published_doc (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_design_approval (id),
    doc_kind     text NOT NULL REFERENCES das_release_doc_kind (code),
    doc_no       text NOT NULL,
    revision     text NOT NULL,
    title        text NOT NULL,
    -- 第 4 步与第 7 章: 本单位在 DOA 权利范围内批准的资料附局方规定的声明原文;
    -- **局方批准的资料标明实际批准依据, 不得加注虚假的本单位批准声明**。
    -- 所以批准依据要分清是哪一种, 而声明只能附在前一种上。
    approval_basis text NOT NULL,           -- DOA_SCOPE / CAAC_APPROVED
    caac_approval_ref text,                 -- 局方批准的, 记实际批准依据
    statement_text text,                    -- 本单位声明（只能是 DOA_SCOPE 的）
    approved_by  uuid NOT NULL REFERENCES app_user (id),
    approved_on  date NOT NULL,
    released_on  date,
    -- 撤销状态。第 7 章: 撤销期间相关资料不得继续用于改装、生产或放行。
    -- 刻意**不叫 status**: 它只有"有没有被撤销"这一件事, 叫 status 迟早被塞进别的状态。
    revoked_at   timestamptz,
    revocation_id bigint,                   -- 指向撤销记录（第 10 节加外键）
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dpd UNIQUE (doc_no, revision),
    CONSTRAINT ck_dpd_basis CHECK (approval_basis IN ('DOA_SCOPE', 'CAAC_APPROVED')),
    CONSTRAINT ck_dpd_text CHECK (
        length(btrim(doc_no)) > 0 AND length(btrim(revision)) > 0
        AND length(btrim(title)) > 0),
    -- 【不得加注虚假的本单位批准声明】
    -- 局方批准的资料: 必须记实际批准依据, 且**不得**附本单位声明。
    -- 写成 (basis='CAAC' AND ...) OR (...) 不行: 任一侧为 NULL 时整式为 NULL,
    -- 而 CHECK 在 NULL 时放行。用 CASE。
    CONSTRAINT ck_dpd_statement CHECK (
        CASE approval_basis
             WHEN 'CAAC_APPROVED'
                 THEN length(btrim(coalesce(caac_approval_ref, ''))) > 0
                      AND statement_text IS NULL
             ELSE caac_approval_ref IS NULL
        END)
);

COMMENT ON TABLE das_published_doc IS
    '对外发布的资料（ICA、服务通告、飞行手册补充）。挂在设计批准上而不是项目上（设计输入第 8.3 节：项目有结束时间，证件长期有效）。局方批准的资料不得加注本单位批准声明（第 7 章）。';

-- 分发清单与接收确认（第 4 步、第 7 章: 发布对象名单和接收确认必须可追溯）
CREATE TABLE IF NOT EXISTS das_doc_distribution (
    id           bigserial PRIMARY KEY,
    doc_id       bigint NOT NULL REFERENCES das_published_doc (id),
    recipient    text NOT NULL,             -- 运营人／改装单位／生产单位／供应商
    recipient_kind text NOT NULL,
    sent_on      date NOT NULL,
    acknowledged_on date,                   -- 接收确认
    ack_ref      text,
    chase_count  int NOT NULL DEFAULT 0,    -- 未确认的逐一跟催（第 8 步）
    last_chased_on date,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ddd UNIQUE (doc_id, recipient),
    CONSTRAINT ck_ddd_kind CHECK (recipient_kind IN
        ('OPERATOR', 'MODIFIER', 'PRODUCER', 'SUPPLIER', 'OTHER')),
    CONSTRAINT ck_ddd_text CHECK (length(btrim(recipient)) > 0),
    CONSTRAINT ck_ddd_ack CHECK ((acknowledged_on IS NULL) = (ack_ref IS NULL))
);

-- ---------------------------------------------------------------------
-- 2. 撤销与召回（第 7～11 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_doc_revocation (
    id           bigserial PRIMARY KEY,
    doc_id       bigint NOT NULL REFERENCES das_published_doc (id),
    -- 第 7 步的四种触发情形, 原文照录为取值。
    trigger_kind text NOT NULL,
    trigger_note text NOT NULL,
    initiated_by uuid NOT NULL REFERENCES app_user (id),
    initiated_on date NOT NULL,
    -- 第 8 步: 立即止用通知。在完成评估前先发 —— 所以它是撤销流程的第一步, 不是最后一步。
    stop_use_sent_on date,
    stop_use_scope text,                    -- 暂停使用的范围
    interim_measures text,                  -- 临时措施
    -- 第 9 步: 影响评估。
    impact_assessed_on date,
    affected_scope text,                    -- 受影响的产品、架次／序列号范围
    retrofit_done_note text,                -- 已实施改装的情况
    unsafe_condition boolean,               -- 是否构成不安全状态
    occurrence_reported_ref text,           -- 构成 CCAR-21.5 报告情形的按 UG-DAP-12 报局方
    -- 第 10 步: 撤销决定。由**原批准人或其上级**签署。
    decided_by   uuid REFERENCES app_user (id),
    decided_on   date,
    revoked_approval_ref text,              -- 被撤销的批准编号与版次
    revocation_reason text,
    corrected_doc_id bigint REFERENCES das_published_doc (id),
    -- 第 11 步: 报局方与闭环 ＋ 举一反三检查同类批准。
    caac_reported_on date,
    caac_report_ref text,
    similar_review_note text,               -- 同类排查结果
    closed_on    date,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_ddr_trigger CHECK (trigger_kind IN
        ('CAAC_FOUND_WRONG',        -- a) 局方认定分类或批准结论有误
         'DOC_ERROR',               -- b) 已发布资料存在错误、遗漏或与批准设计不一致
         'UNSAFE_FEATURE',          -- c) 已批准的设计存在不安全特征
         'AUTHORISATION_REVOKED')), -- d) 授权人员授权被撤销后复核发现签署不成立
    CONSTRAINT ck_ddr_note CHECK (length(btrim(trigger_note)) > 0),
    CONSTRAINT ck_ddr_stop CHECK (
        (stop_use_sent_on IS NULL)
        = (length(btrim(coalesce(stop_use_scope, ''))) = 0)),
    CONSTRAINT ck_ddr_decided CHECK ((decided_by IS NULL) = (decided_on IS NULL)),
    -- 撤销决定要注明被撤销的批准编号、版次和理由（第 10 步原文）。
    CONSTRAINT ck_ddr_reason CHECK (
        decided_on IS NULL
        OR (length(btrim(coalesce(revoked_approval_ref, ''))) > 0
            AND length(btrim(coalesce(revocation_reason, ''))) > 0)),
    CONSTRAINT ck_ddr_caac CHECK ((caac_reported_on IS NULL) = (caac_report_ref IS NULL))
);

ALTER TABLE das_published_doc
    ADD CONSTRAINT fk_dpd_revocation
    FOREIGN KEY (revocation_id) REFERENCES das_doc_revocation (id);

-- 止用通知的接收确认（第 8 步: 须取得接收确认, 未确认的逐一跟催）
CREATE TABLE IF NOT EXISTS das_stop_use_ack (
    id           bigserial PRIMARY KEY,
    revocation_id bigint NOT NULL REFERENCES das_doc_revocation (id),
    distribution_id bigint NOT NULL REFERENCES das_doc_distribution (id),
    sent_on      date NOT NULL,
    acknowledged_on date,
    ack_ref      text,
    chase_count  int NOT NULL DEFAULT 0,
    last_chased_on date,
    CONSTRAINT uq_dsua UNIQUE (revocation_id, distribution_id),
    CONSTRAINT ck_dsua_ack CHECK ((acknowledged_on IS NULL) = (ack_ref IS NULL))
);

-- ---------------------------------------------------------------------
-- 3. 技术询问台账（第 13 步）
-- ---------------------------------------------------------------------
-- 原文三类处理: a) 使用和安装方法类——依据已发布资料答复;
-- b) **偏离已批准设计类——按 UG-DAP-06 走设计更改, 不得以答复代替更改**;
-- c) 反映故障、失效或缺陷类——转 UG-DAP-12 登记。
-- 且: **对外答复不得超出已批准的设计数据范围。**
CREATE TABLE IF NOT EXISTS das_tech_inquiry (
    id           bigserial PRIMARY KEY,
    inquiry_no   text NOT NULL UNIQUE,
    received_from text NOT NULL,
    received_on  date NOT NULL,
    question     text NOT NULL,
    -- 三类之一。分类决定了后续走哪条路, 所以它不是一个备注字段。
    category     text NOT NULL,
    -- a) 类: 依据哪份已发布资料答复
    based_on_doc_id bigint REFERENCES das_published_doc (id),
    -- b) 类: 必须指向一条设计更改, 不得以答复代替更改
    change_no    text,
    -- c) 类: 必须指向事件报告
    occurrence_ref text,
    answer       text,
    -- 答复由适航管理负责人或其书面指定的人员审核后发出
    reviewed_by  uuid REFERENCES app_user (id),
    designation_ref text,                   -- 书面指定的依据（非 AWM 审核时）
    answered_on  date,
    -- 对外答复不得超出已批准的设计数据范围 —— 这一条系统判不了"超没超",
    -- 但判得了"有没有声明核对过"。做成必须声明, 不做成默认通过。
    within_approved_data boolean,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dti_category CHECK (category IN ('USAGE', 'DEVIATION', 'DEFECT')),
    CONSTRAINT ck_dti_text CHECK (
        length(btrim(inquiry_no)) > 0 AND length(btrim(received_from)) > 0
        AND length(btrim(question)) > 0),
    CONSTRAINT ck_dti_answered CHECK (
        (answered_on IS NULL)
        = (length(btrim(coalesce(answer, ''))) = 0)),
    -- 答复发出时: 要有审核人, 且要声明未超出已批准的设计数据范围。
    CONSTRAINT ck_dti_review CHECK (
        answered_on IS NULL
        OR (reviewed_by IS NOT NULL AND within_approved_data IS NOT NULL))
);

-- ---------------------------------------------------------------------
-- 4. 首次改装技术支持报告（第 14 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_first_article_support (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_design_approval (id),
    serial_no    text NOT NULL,
    support_mode text NOT NULL,             -- ONSITE / REMOTE
    briefing_note text NOT NULL,            -- a) 交底关键步骤和检验点
    onsite_issues text NOT NULL,            -- b) 现场问题的技术处置
    -- 超出已批准数据范围的现场问题要走 UG-DAP-06, 不得当场澄清。
    escalated_change_no text,
    deviations_found text NOT NULL,         -- c) 实际施工与说明性文件的差异
    fed_back_to_config boolean NOT NULL,    -- d) 反馈至设计和构型管理（UG-DAP-05）
    report_ref   text NOT NULL,
    reported_by  uuid NOT NULL REFERENCES app_user (id),
    reported_on  date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dfas UNIQUE (approval_id, serial_no),
    CONSTRAINT ck_dfas_mode CHECK (support_mode IN ('ONSITE', 'REMOTE')),
    CONSTRAINT ck_dfas_text CHECK (
        length(btrim(serial_no)) > 0 AND length(btrim(briefing_note)) > 0
        AND length(btrim(onsite_issues)) > 0 AND length(btrim(deviations_found)) > 0
        AND length(btrim(report_ref)) > 0)
);

-- ---------------------------------------------------------------------
-- 5. 在役问题的相似性评估（第 15 步）
-- ---------------------------------------------------------------------
-- 原文: 比较结构形式、材料、工艺、系统原理、使用环境和失效模式; 判定"相关／可能相关／
-- 不相关"并说明理由。**评估结论无论是否相关均须记录。**
CREATE TABLE IF NOT EXISTS das_similarity_review (
    id           bigserial PRIMARY KEY,
    source_kind  text NOT NULL,             -- EXTERNAL_EVENT / CAAC_BULLETIN
    source_ref   text NOT NULL,
    source_summary text NOT NULL,
    -- 六项比较的结论写在一处, 不拆成六列 —— 按 2026-10-02 的决定, 过程走线下。
    comparison_note text NOT NULL,
    verdict      text NOT NULL,             -- RELATED / POSSIBLY / UNRELATED
    rationale    text NOT NULL,
    -- 相关或可能相关的: 评估对适航性的影响, 必要时转 UG-DAP-12, 并考虑服务通告或建议 AD。
    airworthiness_impact text,
    occurrence_ref text,
    action_taken text,
    reviewed_by  uuid NOT NULL REFERENCES app_user (id),
    reviewed_on  date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dsr_kind CHECK (source_kind IN ('EXTERNAL_EVENT', 'CAAC_BULLETIN')),
    CONSTRAINT ck_dsr_verdict CHECK (verdict IN ('RELATED', 'POSSIBLY', 'UNRELATED')),
    CONSTRAINT ck_dsr_text CHECK (
        length(btrim(source_ref)) > 0 AND length(btrim(source_summary)) > 0
        AND length(btrim(comparison_note)) > 0 AND length(btrim(rationale)) > 0),
    -- 判为相关或可能相关的, 要给出对适航性的影响评估。
    -- 判"不相关"也要记（原文: 无论是否相关均须记录）, 而它要的只是理由, 已由上面保证。
    CONSTRAINT ck_dsr_impact CHECK (
        verdict = 'UNRELATED'
        OR length(btrim(coalesce(airworthiness_impact, ''))) > 0)
);

-- ---------------------------------------------------------------------
-- 6. STC 权益转让与书面许可（第 16～17 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_stc_transfer (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_design_approval (id),
    transfer_kind text NOT NULL,            -- TRANSFER 转让 / TERMINATION 终止
    counterparty text NOT NULL,
    counterparty_address text,
    scope_note   text NOT NULL,             -- 权限范围
    -- CCAR-21.116 的可转让条件与持续适航责任衔接, 由适航管理负责人核对。
    condition_check_note text NOT NULL,
    continued_airworthiness_handover text NOT NULL,
    agreement_ref text NOT NULL,
    reviewed_by  uuid NOT NULL REFERENCES app_user (id),
    approved_by  uuid NOT NULL REFERENCES app_user (id),   -- 责任经理批准
    effective_on date NOT NULL,             -- 生效／终止日期
    -- **生效或终止后 30 天内书面通知局方（法规）**。时限参数见第 9 节。
    caac_notified_on date,
    caac_notice_ref text,
    caac_ack_ref text,                      -- 保存通知与接收证据
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dst_kind CHECK (transfer_kind IN ('TRANSFER', 'TERMINATION')),
    CONSTRAINT ck_dst_text CHECK (
        length(btrim(counterparty)) > 0 AND length(btrim(scope_note)) > 0
        AND length(btrim(condition_check_note)) > 0
        AND length(btrim(continued_airworthiness_handover)) > 0
        AND length(btrim(agreement_ref)) > 0),
    CONSTRAINT ck_dst_notice CHECK (
        (caac_notified_on IS NULL) = (caac_notice_ref IS NULL))
);

COMMENT ON TABLE das_stc_transfer IS
    'STC 权益转让或终止（第 16 步）。生效或终止后 30 天内须书面通知局方（法规时限，见 das_deadline_param 的 L11.STC_TRANSFER_NOTICE）。不得以内部协议免除法定持有人责任——所以这里记的是通知与接收证据，不是免责。';

CREATE TABLE IF NOT EXISTS das_stc_licence (
    id           bigserial PRIMARY KEY,
    approval_id  bigint NOT NULL REFERENCES das_design_approval (id),
    licensee     text NOT NULL,
    -- 第 17 步原文要载明的各项
    products_and_scope text NOT NULL,       -- 适用产品和改装范围
    validity_note text NOT NULL,            -- 许可有效性
    controlled_data_note text NOT NULL,     -- 受控资料及修订传递
    support_note text NOT NULL,             -- 持续适航支持
    config_feedback_note text NOT NULL,     -- 构型反馈
    termination_note text NOT NULL,         -- 终止安排
    agreement_ref text NOT NULL,
    -- 适航管理负责人审查并确认局方可接受性
    acceptability_confirmed_by uuid NOT NULL REFERENCES app_user (id),
    acceptability_note text NOT NULL,
    signed_by    uuid NOT NULL REFERENCES app_user (id),   -- 责任经理或书面授权人
    signed_on    date NOT NULL,
    delivered_on date,                      -- 资料管理员归档后向实施方发放
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dsl_text CHECK (
        length(btrim(licensee)) > 0 AND length(btrim(products_and_scope)) > 0
        AND length(btrim(validity_note)) > 0
        AND length(btrim(controlled_data_note)) > 0
        AND length(btrim(support_note)) > 0
        AND length(btrim(config_feedback_note)) > 0
        AND length(btrim(termination_note)) > 0
        AND length(btrim(agreement_ref)) > 0
        AND length(btrim(acceptability_note)) > 0)
);

-- ---------------------------------------------------------------------
-- 7. 持有责任的年度核对（第 12 步）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS das_holder_duty_review (
    id           bigserial PRIMARY KEY,
    year         int NOT NULL UNIQUE,
    scope_note   text NOT NULL,
    findings     text NOT NULL,
    fed_to_supervision boolean NOT NULL,    -- 纳入监督
    fed_to_management_review boolean NOT NULL,  -- 纳入管理评审
    reviewed_by  uuid NOT NULL REFERENCES app_user (id),
    reviewed_on  date NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dhdr_text CHECK (
        length(btrim(scope_note)) > 0 AND length(btrim(findings)) > 0)
);

-- ---------------------------------------------------------------------
-- 8. 五道闸门
-- ---------------------------------------------------------------------

-- DCMS-INV-122: 已发布的资料只能由**原批准人或其上级**撤销（第 7 章）。
-- "其上级"在系统里只有一个可判的意思: 在任责任经理或在任适航管理负责人 ——
-- 系统里没有组织层级表, 而硬造一个层级表会变成又一个与事实不符的常量（判据 A7）。
-- 所以判: 原批准人本人, 或在任 AM／AWM。其余一律拒。
CREATE OR REPLACE FUNCTION dcms_check_das_revocation() RETURNS trigger AS $$
DECLARE
    v_approver uuid;
    v_doc text;
BEGIN
    SELECT approved_by, doc_no || ' ' || revision INTO v_approver, v_doc
      FROM das_published_doc WHERE id = NEW.doc_id;

    IF NEW.decided_by IS NOT NULL AND NEW.decided_by <> v_approver
       AND NOT EXISTS (SELECT 1 FROM das_appointment_in_force
                        WHERE user_id = NEW.decided_by
                          AND position_code IN ('AM', 'AWM')) THEN
        RAISE EXCEPTION 'DCMS-INV-122: 资料 % 的撤销只能由**原批准人或其上级**签署（UG-DAP-11 第 7 章: '
                        '不得由编制人或使用部门自行作废）。"其上级"在系统里判为在任责任经理或'
                        '在任适航管理负责人 —— 系统里没有组织层级表, 而硬造一个会变成又一个'
                        '与事实不符的常量（判据 A7: 不得写死人数与层级）。', v_doc;
    END IF;

    -- 【止用通知未取得全部接收确认前, 不得认为撤销已完成】(第 7 章)
    IF NEW.closed_on IS NOT NULL THEN
        IF NEW.stop_use_sent_on IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-122: 撤销流程还没发过止用通知, 不得结案。'
                            'UG-DAP-11 第 8 步: **在完成评估前**先向所有已接收该资料的对象发出书面止用通知 —— '
                            '它是这条流程的第一步, 不是最后一步。';
        END IF;
        IF EXISTS (SELECT 1 FROM das_stop_use_ack
                    WHERE revocation_id = NEW.id AND acknowledged_on IS NULL) THEN
            RAISE EXCEPTION 'DCMS-INV-122: 止用通知还有接收方未确认, **不得认为撤销已完成**（第 7 章）。'
                            '未确认的要逐一跟催（第 8 步）—— 没确认就结案, 等于那边还在用这份资料, '
                            '而我们这边记着已经撤销了。';
        END IF;
        IF NEW.decided_on IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-122: 还没有签署撤销决定, 不得结案（第 10 步）。';
        END IF;
        IF NEW.caac_reported_on IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-122: 撤销情况、影响范围和纠正措施还没报局方, 不得结案（第 11 步）。';
        END IF;
        IF length(btrim(coalesce(NEW.similar_review_note, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-122: 还没有举一反三检查同类批准是否存在相同问题（第 11 步）。'
                            '一次撤销只改这一份资料, 同类的那几份还在外面。';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_revocation_check ON das_doc_revocation;
CREATE TRIGGER trg_das_revocation_check
    BEFORE INSERT OR UPDATE ON das_doc_revocation
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_revocation();

-- DCMS-INV-123: 撤销期间, 相关资料不得继续用于改装、生产或放行（第 7 章）。
-- 落点: 已撤销或在撤销流程中的资料, 不得被技术询问引为答复依据, 也不得再分发。
CREATE OR REPLACE FUNCTION dcms_check_das_doc_in_use() RETURNS trigger AS $$
DECLARE
    v_doc_id bigint;
    v_doc text;
    v_revoked timestamptz;
    v_in_progress bigint;
BEGIN
    -- 【表名只能在外层判, 不能写进 CASE 表达式里】
    -- 本函数挂在两张表上: das_doc_distribution 有 doc_id, das_tech_inquiry 有
    -- based_on_doc_id, 两张表都没有对方那一列。
    -- 写成 CASE TG_TABLE_NAME WHEN ... THEN NEW.doc_id ELSE NEW.based_on_doc_id END
    -- 是不行的: PL/pgSQL 把整个 CASE 编成一条 SQL, 两个字段引用都得解析得出,
    -- 于是在分发表上触发时报「record "new" has no field "based_on_doc_id"」,
    -- 而本该报的是 INV-123。
    -- 这与 0047 的 dcms_guard_das_offline_append 是同一个坑 —— 同一次会话里踩了两遍,
    -- 所以写在这里: **TG_TABLE_NAME 决定了哪些字段存在, 必须用 IF 分支分开写,
    -- 任何把两张表的字段放进同一个表达式的写法都会在另一张表上炸。**
    IF TG_TABLE_NAME = 'das_doc_distribution' THEN
        v_doc_id := NEW.doc_id;
    ELSE
        v_doc_id := NEW.based_on_doc_id;
    END IF;
    IF v_doc_id IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT doc_no || ' ' || revision, revoked_at INTO v_doc, v_revoked
      FROM das_published_doc WHERE id = v_doc_id;
    IF v_revoked IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-123: 资料 % 已撤销, 不得继续用于改装、生产或放行（UG-DAP-11 第 7 章）。',
                        v_doc;
    END IF;
    SELECT id INTO v_in_progress FROM das_doc_revocation
     WHERE doc_id = v_doc_id AND closed_on IS NULL LIMIT 1;
    IF v_in_progress IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-123: 资料 % 正在撤销流程中（撤销记录 #%, 未结案）, '
                        '**撤销期间相关资料不得继续用于改装、生产或放行**（第 7 章）。',
                        v_doc, v_in_progress;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_distribution_in_use ON das_doc_distribution;
CREATE TRIGGER trg_das_distribution_in_use
    BEFORE INSERT ON das_doc_distribution
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_doc_in_use();

DROP TRIGGER IF EXISTS trg_das_inquiry_in_use ON das_tech_inquiry;
CREATE TRIGGER trg_das_inquiry_in_use
    BEFORE INSERT OR UPDATE ON das_tech_inquiry
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_doc_in_use();

-- DCMS-INV-124: 技术询问的三类各走各的路（第 13 步）。
-- 其中 b) 类最要紧: **不得以答复代替更改。**
CREATE OR REPLACE FUNCTION dcms_check_das_tech_inquiry() RETURNS trigger AS $$
DECLARE
    v_state text;
BEGIN
    IF NEW.answered_on IS NULL THEN
        RETURN NEW;                     -- 还没答复, 不判路径
    END IF;
    IF NEW.category = 'DEVIATION' THEN
        IF length(btrim(coalesce(NEW.change_no, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-124: 偏离已批准设计类的询问**按 UG-DAP-06 走设计更改, '
                            '不得以答复代替更改**（UG-DAP-11 第 13 步 b）。'
                            '用一封答复函允许对方偏离已批准的设计, 等于绕过设计更改分类改了设计, '
                            '而那份设计仍然是局方批准的那一份。';
        END IF;
        SELECT cl.state INTO v_state
          FROM das_design_change d
          LEFT JOIN das_change_classification cl ON cl.change_id = d.id
         WHERE d.change_no = NEW.change_no;
        IF v_state IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-124: 更改单 % 不存在或还没有分类结论。'
                            '偏离类询问要等更改走完 UG-DAP-06 再答复。', NEW.change_no;
        END IF;
    ELSIF NEW.category = 'DEFECT' THEN
        IF length(btrim(coalesce(NEW.occurrence_ref, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-124: 反映故障、失效或缺陷类的询问须转 UG-DAP-12 登记'
                            '（第 13 步 c）, 并记明事件报告编号 —— 那是 48 小时报告链的入口。';
        END IF;
    ELSE
        IF NEW.based_on_doc_id IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-124: 使用和安装方法类的询问**依据已发布资料答复**（第 13 步 a）, '
                            '须指明依据哪一份。凭记忆答复, 答的可能是上一版。';
        END IF;
    END IF;
    IF NOT NEW.within_approved_data THEN
        RAISE EXCEPTION 'DCMS-INV-124: 对外答复不得超出已批准的设计数据范围（第 13 步）。'
                        '核对结论为"超出"时不得发出 —— 超出的那部分要走设计更改。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_tech_inquiry_check ON das_tech_inquiry;
CREATE TRIGGER trg_das_tech_inquiry_check
    BEFORE INSERT OR UPDATE ON das_tech_inquiry
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_tech_inquiry();

-- DCMS-INV-125: 撤销记录、发布资料、权益转让与许可不得删除或改写。
-- 它们都是对外生效过的东西: 资料发给了运营人, 转让通知报给了局方, 许可给了实施方。
CREATE OR REPLACE FUNCTION dcms_guard_das_holder_append() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DCMS-INV-125: % 不得删除。资料发给过运营人、转让通知报给过局方、'
                        '许可给过实施方 —— 删掉等于那件事从未发生过。', TG_TABLE_NAME;
    END IF;
    RAISE EXCEPTION 'DCMS-INV-125: % 不得改写。更正请另记一条并写明理由'
                    '（已发布资料的更正走第 10 步的撤销与更正）。', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_stc_transfer_append ON das_stc_transfer;
CREATE TRIGGER trg_das_stc_transfer_append
    BEFORE DELETE ON das_stc_transfer
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_holder_append();

DROP TRIGGER IF EXISTS trg_das_stc_licence_append ON das_stc_licence;
CREATE TRIGGER trg_das_stc_licence_append
    BEFORE DELETE ON das_stc_licence
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_holder_append();

DROP TRIGGER IF EXISTS trg_das_published_doc_append ON das_published_doc;
CREATE TRIGGER trg_das_published_doc_append
    BEFORE DELETE ON das_published_doc
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_holder_append();

DROP TRIGGER IF EXISTS trg_das_revocation_append ON das_doc_revocation;
CREATE TRIGGER trg_das_revocation_append
    BEFORE DELETE ON das_doc_revocation
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_holder_append();

-- ---------------------------------------------------------------------
-- 9. 第 16 步那个 30 天是法规时限 → 进时限引擎
-- ---------------------------------------------------------------------
-- 原文把它标为"（法规）": 权益转让生效或终止后 30 天内书面通知局方。
-- 所以它不是本模块的一个字段, 而是 0039 的一条参数, 受规章修订闸门保护。
INSERT INTO das_deadline_param
    (code, name_cn, module_code, constraint_source, statutory_basis, trigger_mode,
     start_point, calendar_basis, ceiling_value, ceiling_unit, ceiling_basis,
     change_authority, source_doc, note)
VALUES
('L11.STC_TRANSFER_NOTICE', 'STC 权益转让通知局方时限', 'M8', 'STATUTORY',
 'CCAR-21.116 / UG-DAP-11 第 16 步', 'EVENT',
 '权益转让协议生效或终止之日', 'CALENDAR', 30, 'DAY',
 'CCAR-21.116、UG-DAP-11 第 16 步标注"（法规）"',
 '规章修订（内部不可调整）', 'UG-DAP-11 第 6 章第 16 步',
 '通知须载明受让人姓名、地址、权限范围和生效日期, 并保存通知与接收证据。'
 '**不得以内部协议免除法定持有人责任** —— 所以这条时限不因协议另有约定而变')
ON CONFLICT (code) DO NOTHING;

INSERT INTO das_deadline_value
    (param_code, value_num, value_unit, effective_from, is_baseline, baseline_source,
     activated_at)
SELECT 'L11.STC_TRANSFER_NOTICE', 30, 'DAY', DATE '2000-01-01', true,
       'CCAR-21.116 / UG-DAP-11 第 16 步（初值生效日取系统纪元, 规章生效日待核实）',
       now()
 WHERE NOT EXISTS (SELECT 1 FROM das_deadline_value
                    WHERE param_code = 'L11.STC_TRANSFER_NOTICE');

-- ---------------------------------------------------------------------
-- 10. 视图
-- ---------------------------------------------------------------------

-- 10.1 **止用通知未全部确认的撤销**。第 7 章: 未取得全部接收确认前不得认为撤销已完成。
-- 这张表盯的是"那边还在用、我们这边以为已撤销"的那段窗口。
CREATE OR REPLACE VIEW das_revocation_pending_ack AS
SELECT r.id, d.doc_no, d.revision, d.title, r.trigger_kind, r.initiated_on,
       r.stop_use_sent_on,
       count(a.id)                                                 AS recipients,
       count(*) FILTER (WHERE a.acknowledged_on IS NULL)            AS unacknowledged,
       string_agg(dd.recipient, '、') FILTER (WHERE a.acknowledged_on IS NULL)
                                                                   AS pending_recipients,
       max(a.chase_count)                                          AS max_chases
  FROM das_doc_revocation r
  JOIN das_published_doc d ON d.id = r.doc_id
  LEFT JOIN das_stop_use_ack a ON a.revocation_id = r.id
  LEFT JOIN das_doc_distribution dd ON dd.id = a.distribution_id
 WHERE r.closed_on IS NULL
 GROUP BY r.id, d.doc_no, d.revision, d.title, r.trigger_kind, r.initiated_on,
          r.stop_use_sent_on
 ORDER BY r.initiated_on;

COMMENT ON VIEW das_revocation_pending_ack IS
    '止用通知还有接收方未确认的撤销。UG-DAP-11 第 7 章：未取得全部接收确认前不得认为撤销已完成——这张表盯的是「那边还在用、我们这边以为已撤销」的那段窗口。';

-- 10.2 撤销流程走到哪一步了（第 7～11 步）。
CREATE OR REPLACE VIEW das_revocation_progress AS
SELECT r.id, d.doc_no, d.revision, r.trigger_kind, r.initiated_on,
       (r.stop_use_sent_on IS NOT NULL)                            AS step8_stop_use,
       (r.impact_assessed_on IS NOT NULL)                          AS step9_impact,
       (r.decided_on IS NOT NULL)                                  AS step10_decided,
       (r.caac_reported_on IS NOT NULL)                            AS step11_reported,
       (length(btrim(coalesce(r.similar_review_note, ''))) > 0)    AS step11_similar,
       r.closed_on,
       (current_date - r.initiated_on)                             AS days_open
  FROM das_doc_revocation r
  JOIN das_published_doc d ON d.id = r.doc_id
 ORDER BY r.closed_on NULLS FIRST, r.initiated_on;

-- 10.3 发布资料的分发与确认（第 7 章: 发布对象名单和接收确认必须可追溯）。
CREATE OR REPLACE VIEW das_doc_distribution_status AS
SELECT d.id AS doc_id, d.doc_no, d.revision, d.title, d.approval_basis,
       (d.revoked_at IS NOT NULL)                                  AS revoked,
       count(dd.id)                                                AS recipients,
       count(*) FILTER (WHERE dd.acknowledged_on IS NULL)           AS unacknowledged,
       string_agg(dd.recipient, '、') FILTER (WHERE dd.acknowledged_on IS NULL)
                                                                   AS pending_recipients
  FROM das_published_doc d
  LEFT JOIN das_doc_distribution dd ON dd.doc_id = d.id
 GROUP BY d.id, d.doc_no, d.revision, d.title, d.approval_basis, d.revoked_at
 ORDER BY d.doc_no, d.revision;

-- 10.4 技术询问台账, 按三类分。
CREATE OR REPLACE VIEW das_tech_inquiry_register AS
SELECT i.id, i.inquiry_no, i.received_from, i.received_on, i.category,
       CASE i.category WHEN 'USAGE' THEN 'a) 使用和安装方法'
                       WHEN 'DEVIATION' THEN 'b) 偏离已批准设计（须走设计更改）'
                       ELSE 'c) 反映故障、失效或缺陷（转 UG-DAP-12）' END AS category_cn,
       d.doc_no AS based_on_doc, i.change_no, i.occurrence_ref,
       i.answered_on, i.within_approved_data,
       (i.designation_ref IS NOT NULL)                             AS by_designee,
       (i.received_on IS NOT NULL AND i.answered_on IS NULL)        AS open
  FROM das_tech_inquiry i
  LEFT JOIN das_published_doc d ON d.id = i.based_on_doc_id
 ORDER BY i.received_on DESC;

-- 10.5 相似性评估台账。判"不相关"的也在里面 —— 原文: 无论是否相关均须记录。
CREATE OR REPLACE VIEW das_similarity_register AS
SELECT s.id, s.source_kind, s.source_ref, s.verdict,
       CASE s.verdict WHEN 'RELATED' THEN '相关'
                      WHEN 'POSSIBLY' THEN '可能相关'
                      ELSE '不相关' END                            AS verdict_cn,
       s.rationale, s.airworthiness_impact, s.occurrence_ref, s.action_taken,
       s.reviewed_on
  FROM das_similarity_review s
 ORDER BY s.reviewed_on DESC;

-- 10.6 **权益转让通知局方超期的**（第 16 步的法规 30 天）。
CREATE OR REPLACE VIEW das_stc_transfer_notice_due AS
SELECT t.id, a.certificate_no, t.transfer_kind, t.counterparty, t.effective_on,
       t.caac_notified_on,
       (t.effective_on + 30)                                       AS due_on,
       CASE WHEN t.caac_notified_on IS NULL
            THEN (current_date - (t.effective_on + 30))
            ELSE (t.caac_notified_on - (t.effective_on + 30))
       END                                                         AS days_late,
       (t.caac_notified_on IS NULL)                                AS pending
  FROM das_stc_transfer t
  JOIN das_design_approval a ON a.id = t.approval_id
 WHERE t.caac_notified_on IS NULL
    OR t.caac_notified_on > t.effective_on + 30
 ORDER BY t.effective_on;

COMMENT ON VIEW das_stc_transfer_notice_due IS
    '权益转让通知局方逾期或未通知的（第 16 步：生效或终止后 30 天内，法规时限）。上限值在 das_deadline_param 的 L11.STC_TRANSFER_NOTICE，受规章修订闸门保护。';
