-- ---------------------------------------------------------------------
-- 0048  把线下审批接成 M4 两道重闸门的替代路径
-- ---------------------------------------------------------------------
-- 依据 2026-10-02 的业务决策：**设计更改分类等系统不用管那么细, 系统实现不了的走线下
-- 审批, 证据资料上传系统即可。**
--
-- 【这一版改的是"由谁承接", 不是"要不要做"】
-- 0045 要求 9 项判据逐条录进系统才能出分类结论（DCMS-INV-104）,
-- 0046 要求第 1 步的三个条件逐条录进系统才能发布（DCMS-INV-109）。
-- 这两道闸门按的是 UG-DAW-010 第 3 章与 UG-DAP-08 第 1 步的原文, 没有错;
-- 错的是**把唯一的承接方式定成了系统录入**。对 5～8 人的编制, 一次更改录 9 行判据
-- 加 3 行条件是过重的负担, 而过重的负担不会让符合性变好 —— 只会让人绕过系统,
-- 于是系统里什么都没有, 线下也没人管。
--
-- 所以这一版给出第二条路: **线下审批 ＋ 证据上传**（0047）。两条路都要求拿得出东西:
--   系统内: 9 行判据 / 3 行条件, 各自写明依据;
--   线下:   一条已复核的线下审批记录 ＋ 至少一份证据文件 ＋ 原件存放位置。
-- 两条都没有, 才拒。
--
-- 【为什么线下那条路要求"已复核"】
-- 判据 N-总 原文: **补录数据在完成复核并发布之前不具有权威性。**
-- 一条刚补录、还没人复核的线下记录, 不能充当分类结论的依据 ——
-- 否则"走线下"就成了"自己说一声就行"。复核人与补录人是两列, 由 0047 保证可分辨。
--
-- 【保留的闸门, 以及为什么它们不重】
-- 这一版**没有**动下面这几条, 因为它们判的是结论而不是过程, 各自只花一句话:
--   DCMS-INV-105  任一判据显著影响只能是大改; 判不了按大改处理且不得签署
--   DCMS-INV-106  超权限结论留空、须写明转局方依据, 不得自动改判大改
--   DCMS-INV-107  声学与排放本单位无批准权, 结论只能来自局方
--   DCMS-INV-110  小改批准的前提须为已签署的小改分类
--   DCMS-INV-112  批准声明必须是局方规定的原文
-- 其中 106 与 112 尤其不能松: 前者是"用权限问题冒充技术判断", 后者是对外陈述的原文。
-- 这两件事不是"管得细", 是管得对。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 判断某个对象有没有可用的线下承接
-- ---------------------------------------------------------------------
-- 返回的是"有没有一条**已复核**且**带证据**的线下审批"。
-- 三个条件缺一不算: 没复核不具有权威性（判据 N-总）, 没证据等于没发生（DCMS-INV-115）。
CREATE OR REPLACE FUNCTION das_offline_covered(p_object_type text, p_object_key text)
RETURNS boolean AS $$
    SELECT EXISTS (
        SELECT 1 FROM das_offline_approval a
         WHERE a.object_type = p_object_type
           AND a.object_key = p_object_key
           AND a.reviewed_by IS NOT NULL
           AND EXISTS (SELECT 1 FROM das_offline_evidence e
                        WHERE e.approval_id = a.id));
$$ LANGUAGE sql STABLE;

COMMENT ON FUNCTION das_offline_covered IS
    '该对象有没有一条已复核且带证据文件的线下审批。三者缺一不算：没复核不具有权威性（判据 N-总），没证据等于没发生（DCMS-INV-115）。';

-- ---------------------------------------------------------------------
-- 2. M4 的分类结论: 9 项判据或线下承接, 二者之一
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_check_das_change_classification() RETURNS trigger AS $$
DECLARE
    v_total   int;
    v_done    int;
    v_missing text;
    v_sig     text;
    v_change_no text;
BEGIN
    SELECT change_no INTO v_change_no FROM das_design_change WHERE id = NEW.change_id;
    SELECT count(*) INTO v_total FROM das_change_criterion;
    SELECT count(*) INTO v_done FROM das_change_criterion_assessment
     WHERE change_id = NEW.change_id;

    -- 【两条路, 二者之一】
    -- 系统内逐条打勾, 或者线下审批＋证据上传（2026-10-02 的决定）。
    -- 两条都没有才拒 —— 拒的不是"没录进系统", 是"拿不出判定依据"。
    IF v_done < v_total
       AND NOT das_offline_covered('DAS_DESIGN_CHANGE', v_change_no) THEN
        SELECT string_agg(c.seq || '. ' || c.name_cn, '、' ORDER BY c.seq)
          INTO v_missing
          FROM das_change_criterion c
         WHERE NOT EXISTS (SELECT 1 FROM das_change_criterion_assessment a
                            WHERE a.change_id = NEW.change_id
                              AND a.criterion_code = c.code);
        RAISE EXCEPTION 'DCMS-INV-104: 拿不出分类的判定依据, 不得出分类结论。两条路选一条: '
                        '① 在系统里逐条给出判据结论（还差: %）; '
                        '② 走线下审批并把证据上传（das_offline_approval, object_type=DAS_DESIGN_CHANGE, '
                        'object_key=%）—— 线下那条要求记录**已复核**且**至少一份证据文件**: '
                        '判据 N-总"补录数据在完成复核并发布之前不具有权威性", 而没有扫描件的记录'
                        '比没有记录更坏, 台账上看起来有。',
                        v_missing, v_change_no;
    END IF;

    SELECT string_agg(c.name_cn, '、' ORDER BY c.seq) INTO v_sig
      FROM das_change_criterion_assessment a
      JOIN das_change_criterion c ON c.code = a.criterion_code
     WHERE a.change_id = NEW.change_id AND a.verdict = 'SIGNIFICANT';

    -- 判据原文: 判据中任一项达到"显著影响"即为重大更改。
    -- 走线下那条路时系统里没有判据行, 这一条自然无从判断 —— 由线下流程承接,
    -- 并在自评里写明（判据 B1 第六项）。
    IF v_sig IS NOT NULL AND NEW.major_minor = 'MINOR' THEN
        RAISE EXCEPTION 'DCMS-INV-105: 「%」已判为显著影响, 不得分类为小改。'
                        'UG-DAW-010 第 1 章: 判据中任一项达到"显著影响"即为重大更改。',
                        v_sig;
    END IF;

    IF NEW.state = 'BEYOND_AUTHORITY' THEN
        IF NEW.major_minor IS NOT NULL THEN
            RAISE EXCEPTION 'DCMS-INV-106: 超出分类或批准权限时结论须留空, 转局方办理（UG-DAP-06 第 4 步: '
                            '不能仅因超权限自动判为大改）。填成 % 是用权限问题冒充技术判断 —— '
                            '而重大更改要走 UG-DAP-09 向局方申请批准, 等于凭一个权限缺口给产品加了一道审定。',
                            NEW.major_minor;
        END IF;
        IF length(btrim(coalesce(NEW.caac_referral_ref, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-106: 超权限时须写明转局方办理的依据（函件或咨询编号）。'
                            '只标一个"超权限"而不转办, 这条更改就卡在那里没人管。';
        END IF;
    END IF;

    IF NEW.state = 'UNDETERMINED' THEN
        IF NEW.major_minor IS DISTINCT FROM 'MAJOR' THEN
            RAISE EXCEPTION 'DCMS-INV-105: 无法判定的按重大更改处理（UG-DAW-010 第 1 章）, '
                            '直至适航管理负责人或局方确认。此处须填 MAJOR, 实际为 %。',
                            coalesce(NEW.major_minor, '空');
        END IF;
        IF NEW.signed_by IS NOT NULL THEN
            RAISE EXCEPTION 'DCMS-INV-105: 判不了的分类不得签署。按重大更改处理是**暂行处置**, '
                            '待适航管理负责人或局方确认后再签 —— 签了就成了已分类, 而它还没定。';
        END IF;
    END IF;

    IF NEW.state = 'CLASSIFIED' THEN
        IF NEW.major_minor IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-104: 已分类却没有大改／小改结论。';
        END IF;
        IF NEW.signed_by IS NULL OR NEW.signed_on IS NULL THEN
            RAISE EXCEPTION 'DCMS-INV-104: 已分类须有签署人与签署日期（UG-DAF-02 由授权人员签署）。';
        END IF;
    END IF;

    IF NEW.acoustic_result IS NOT NULL
       AND (length(btrim(coalesce(NEW.acoustic_caac_ref, ''))) = 0
            OR NEW.acoustic_caac_on IS NULL) THEN
        RAISE EXCEPTION 'DCMS-INV-107: 声学分类本单位未申请批准权, 结论只能来自局方 —— '
                        '须记录局方编号与日期（UG-DAP-06 第 4 步）。内部给结论就是在没有权利的事项上作了批准。';
    END IF;
    IF NEW.emission_result IS NOT NULL
       AND (length(btrim(coalesce(NEW.emission_caac_ref, ''))) = 0
            OR NEW.emission_caac_on IS NULL) THEN
        RAISE EXCEPTION 'DCMS-INV-107: 排放分类本单位未申请批准权, 结论只能来自局方 —— '
                        '须记录局方编号与日期（UG-DAP-06 第 4 步）。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- 3. M4 后半（小改批准）的发布: 三个条件或线下承接, 二者之一
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_check_das_minor_release() RETURNS trigger AS $$
DECLARE
    v_total   int;
    v_ok      int;
    v_missing text;
    v_bad     text;
    v_requires boolean;
    v_kind_cn text;
    v_tmpl    text;
    v_ph      text;
    v_form_no text;
BEGIN
    SELECT form_no INTO v_form_no FROM das_minor_approval WHERE id = NEW.approval_id;
    SELECT count(*), count(*) FILTER (WHERE satisfied) INTO v_total, v_ok
      FROM das_minor_approval_condition WHERE approval_id = NEW.approval_id;

    -- 系统内三条条件齐备且满足, 或线下审批＋证据上传, 二者之一。
    IF NOT das_offline_covered('DAS_MINOR_APPROVAL', v_form_no) THEN
        IF v_total < 3 THEN
            SELECT string_agg(x.cn, '、') INTO v_missing
              FROM (VALUES ('CLASSIFIED_MINOR', 'a) 已有签署的分类表且为小改'),
                           ('WITHIN_DOA_SCOPE', 'b) 在本单位设计机构许可项目单的权利范围内'),
                           ('APPROVER_AUTHORISED', 'c) 批准人在授权范围内')) AS x(code, cn)
             WHERE NOT EXISTS (SELECT 1 FROM das_minor_approval_condition c
                                WHERE c.approval_id = NEW.approval_id
                                  AND c.condition_code = x.code);
            RAISE EXCEPTION 'DCMS-INV-109: 拿不出第 1 步的核对依据。两条路选一条: '
                            '① 在系统里逐条登记（还差: %）; ② 走线下审批并把证据上传'
                            '（object_type=DAS_MINOR_APPROVAL, object_key=%, 须已复核且有证据文件）。',
                            v_missing, v_form_no;
        END IF;
        IF v_ok < v_total THEN
            SELECT string_agg(x.cn, '、') INTO v_bad
              FROM (VALUES ('CLASSIFIED_MINOR', 'a) 已有签署的分类表且为小改'),
                           ('WITHIN_DOA_SCOPE', 'b) 在本单位设计机构许可项目单的权利范围内'),
                           ('APPROVER_AUTHORISED', 'c) 批准人在授权范围内')) AS x(code, cn)
              JOIN das_minor_approval_condition c
                ON c.approval_id = NEW.approval_id AND c.condition_code = x.code
             WHERE NOT c.satisfied;
            -- 【这一条即使走线下也不该松, 但它只在系统内登记了"不满足"时才会触发】
            -- 系统里明写着某条不满足, 却还要发布 —— 那不是"系统管得太细", 是自相矛盾。
            RAISE EXCEPTION 'DCMS-INV-109: 系统里已登记下列条件**不满足**, 不得发布: %。'
                            'UG-DAP-08 第 1 步: 任一项不满足, 不得批准。'
                            '第 7 章: 超出许可范围或批准权限的, 提交局方办理或先申请权限变更, '
                            '**不得仅因无批准权限自动改判大改** —— 分类结论保持原样。',
                            v_bad;
        END IF;
    ELSE
        -- 走线下那条路时, 系统里若已登记某条不满足, 仍然拦。理由同上: 自相矛盾。
        IF v_ok < v_total THEN
            SELECT string_agg(x.cn, '、') INTO v_bad
              FROM (VALUES ('CLASSIFIED_MINOR', 'a) 已有签署的分类表且为小改'),
                           ('WITHIN_DOA_SCOPE', 'b) 在本单位设计机构许可项目单的权利范围内'),
                           ('APPROVER_AUTHORISED', 'c) 批准人在授权范围内')) AS x(code, cn)
              JOIN das_minor_approval_condition c
                ON c.approval_id = NEW.approval_id AND c.condition_code = x.code
             WHERE NOT c.satisfied;
            RAISE EXCEPTION 'DCMS-INV-109: 已有线下承接, 但系统里明写着下列条件**不满足**: %。'
                            '线下结论与系统记录相反时不得发布 —— 两者必有一个是错的, '
                            '按 UG-DAP-14 查清再说。',
                            v_bad;
        END IF;
    END IF;

    SELECT requires_statement, name_cn INTO v_requires, v_kind_cn
      FROM das_release_doc_kind WHERE code = NEW.doc_kind;
    IF v_requires THEN
        SELECT template, placeholder INTO v_tmpl, v_ph
          FROM das_approval_statement WHERE code = 'DOA_MINOR_APPROVAL';
        IF length(btrim(coalesce(NEW.statement_text, ''))) = 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 「%」对外发布须附局方规定的批准声明（UG-DAP-08 第 5 步）。', v_kind_cn;
        END IF;
        IF v_ph IS NOT NULL AND position(v_ph in NEW.statement_text) > 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 声明里的占位符「%」还没替换成设计机构许可证编号。'
                            '本单位的 DOA 尚在申请中, 这个号还不存在 —— 所以对外发布暂时过不了这道门, '
                            '这是如实状态。硬塞一个编号进去才是问题: 那份资料会带着一个假的许可证编号发出去。',
                            v_ph;
        END IF;
        IF v_ph IS NOT NULL THEN
            IF position(split_part(v_tmpl, v_ph, 1) in NEW.statement_text) <> 1
               OR right(NEW.statement_text, length(split_part(v_tmpl, v_ph, 2)))
                  <> split_part(v_tmpl, v_ph, 2)
               OR length(NEW.statement_text)
                  <= length(split_part(v_tmpl, v_ph, 1))
                     + length(split_part(v_tmpl, v_ph, 2)) THEN
                RAISE EXCEPTION 'DCMS-INV-112: 批准声明与局方规定的原文不符（UG-DAP-08 第 7 章: 必须使用原文）。'
                                '受控原文见 das_approval_statement。改写一个字, 这段声明的法律效力就说不清了。';
            END IF;
        ELSIF NEW.statement_text <> v_tmpl THEN
            RAISE EXCEPTION 'DCMS-INV-112: 批准声明与局方规定的原文不符（UG-DAP-08 第 7 章）。';
        END IF;
    ELSE
        IF length(btrim(coalesce(NEW.statement_text, ''))) > 0 THEN
            RAISE EXCEPTION 'DCMS-INV-112: 「%」**不使用**此声明（UG-DAP-08 第 5 步原文）。'
                            '给符合性文件、审定计划或生产用设计数据附上"按权利范围进行批准"的声明, '
                            '是把不代表设计批准的资料说成了设计批准。', v_kind_cn;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- 4. 哪些结论是靠线下承接的 —— 自评要点清这个数
-- ---------------------------------------------------------------------
-- 走线下不是问题, **说不清哪些走了线下**才是问题。符合性自评要如实写明:
-- 哪些要求由系统实现、哪些由线下流程执行。这张视图就是那一栏的底稿。
CREATE OR REPLACE VIEW das_change_evidence_path AS
SELECT d.change_no, d.title, cl.state, cl.major_minor,
       (SELECT count(*) FROM das_change_criterion_assessment a
         WHERE a.change_id = d.id)                                 AS criteria_in_system,
       (SELECT count(*) FROM das_change_criterion)                 AS criteria_total,
       das_offline_covered('DAS_DESIGN_CHANGE', d.change_no)       AS offline_covered,
       CASE WHEN (SELECT count(*) FROM das_change_criterion_assessment a
                   WHERE a.change_id = d.id)
                 >= (SELECT count(*) FROM das_change_criterion) THEN 'IN_SYSTEM'
            WHEN das_offline_covered('DAS_DESIGN_CHANGE', d.change_no) THEN 'OFFLINE'
            ELSE 'NEITHER'
       END                                                         AS evidence_path
  FROM das_design_change d
  LEFT JOIN das_change_classification cl ON cl.change_id = d.id
 ORDER BY d.change_no;

COMMENT ON VIEW das_change_evidence_path IS
    '每条更改的判定依据走的是系统内还是线下。走线下不是问题，说不清哪些走了线下才是问题——符合性自评要如实写明哪些由系统实现、哪些由线下流程执行。NEITHER 一档说明两边都拿不出依据。';

CREATE OR REPLACE VIEW das_minor_evidence_path AS
SELECT a.form_no, d.change_no,
       (SELECT count(*) FROM das_minor_approval_condition c
         WHERE c.approval_id = a.id)                               AS conditions_in_system,
       das_offline_covered('DAS_MINOR_APPROVAL', a.form_no)        AS offline_covered,
       CASE WHEN (SELECT count(*) FROM das_minor_approval_condition c
                   WHERE c.approval_id = a.id) >= 3 THEN 'IN_SYSTEM'
            WHEN das_offline_covered('DAS_MINOR_APPROVAL', a.form_no) THEN 'OFFLINE'
            ELSE 'NEITHER'
       END                                                         AS evidence_path
  FROM das_minor_approval a
  JOIN das_change_classification cl ON cl.id = a.classification_id
  JOIN das_design_change d ON d.id = cl.change_id
 ORDER BY a.form_no;
