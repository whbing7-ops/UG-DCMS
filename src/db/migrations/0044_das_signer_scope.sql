-- ---------------------------------------------------------------------
-- 0044  签署授权的产品／型号／件号范围与权限限制（判据 I10.PRODUCT_SCOPE）
-- ---------------------------------------------------------------------
-- 依据手册 3.2「授权范围按产品类别分别确定, **未列入的产品类别不得签署**」、
-- UG-DAM-01-附2「【批准类型；产品／型号／件号；限制】」、
-- UG-DAW-005 第 4 章（登记批准类型、具体产品／型号或件号范围、权限限制）、
-- UG-DAP-03 第 8 节, 设计输入第九之二节的 C 版更正。
--
-- 【这一次为什么不是重犯 PR #5 的错】
-- PR #5 给 signer_authorization 加过自由文本 product_scope, 被否决并移除。
-- 否决的理由不是"不该有范围", 而是**那个字段只写入、只显示, is_authorized() 从不读它**：
-- 手册要求强制执行的一条控制, 在系统里是个装饰, 而且它会出现在授权名单上,
-- 让审查者以为系统在管。
-- 所以本迁移的硬要求是: 范围必须是**可比对的结构化引用**, 并且必须有一条真的会读它的
-- 校验路径。读不到的那一部分一律如实记为未施加, 不计入已实现。
--
-- 【设计输入第九之二节 C 版更正定下的处置, 照它做】
-- B 版曾写"等 M3 落地后用项目引用表达产品范围, 一次做对"——C 版把这句更正了:
-- 一个项目可以包含多个产品、多个型号件号和不同限制条件, **因此"同属一个项目"不能证明
-- "签署范围相符"; 项目引用是关联维度, 不是范围维度**。
-- 正确处置是两者分开建模: 产品／型号／件号范围与权限限制单独建模并单独验收,
-- 项目引用作为**附加的**关联约束（受托授权只限该受托项目, 不得顺带签自家 STC 的资料）。
-- 本迁移严格照此: das_signer_scope 是范围维度, das_signer_project_bind 是关联维度,
-- 两者各自独立, 谁也不替代谁。
--
-- 【范围校验必须知道"签的是什么"】
-- 这是本模块最要紧的一条边界。范围校验只在调用方说得出标的（件号／图号族／航空器型号）
-- 时才有意义。调用方传不出标的时**不得默默放行** —— 那正是判据 I10.DISCIPLINE 已有的
-- 缺口（调用方未传专业时不施加专业限制）, 明知故犯地再来一次是不行的。
-- 所以: 落地的强制点只有一处 —— 符合性声明的签署（M3, 标的就是该项目）,
-- 其余签署路径仍未施加范围校验, 由 das_signer_scope_unenforced 如实列出,
-- 独立性登记册里 I10.PRODUCT_SCOPE 记为**部分实现**而不是已实现。
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- 1. 范围条目
-- ---------------------------------------------------------------------
-- 一行 = 一条"列入"。手册 3.2: 未列入的产品类别不得签署 —— 所以这张表是白名单,
-- 空表意味着什么都没列入。空表怎么处理见第 3 节的声明机制。
CREATE TABLE IF NOT EXISTS das_signer_scope (
    id          bigserial PRIMARY KEY,
    auth_id     uuid NOT NULL REFERENCES signer_authorization (id) ON DELETE CASCADE,
    -- UG-DAW-005 第 4 章要登记的第一项: 批准类型。
    -- 可引用 das_project_type（STC／PMA／SUP）—— 这是结构化的, 不是文本。
    approval_type_code text NOT NULL REFERENCES das_project_type (code),
    -- 第二项: 具体产品／型号或件号范围。四种粒度, 恰好给一种。
    scope_kind  text NOT NULL,
    part_number_id uuid REFERENCES part_number (id),
    family_id   uuid,                       -- 图号族（basic_drawing_family）
    aircraft_type text,                     -- 航空器型号
    product_category text,                  -- 产品类别（手册 3.2 的口径）
    -- 第三项: 权限限制。可为空, 但为空与"没有限制"是两回事, 见 ck_dss_limit。
    limitation  text,
    no_limitation_declared boolean NOT NULL DEFAULT false,
    source_ref  text NOT NULL,              -- 纸面授权书中对应的条目
    created_by  uuid REFERENCES app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dss_kind CHECK (scope_kind IN
        ('PART_NUMBER', 'FAMILY', 'AIRCRAFT_TYPE', 'PRODUCT_CATEGORY')),
    -- 粒度与引用一一对应, 恰好给一个。写成"至少给一个"会让一行同时挂件号和型号,
    -- 而那两者的覆盖判断不一样, 一行两义迟早被当成"取并集"或"取交集", 看谁先读到。
    CONSTRAINT ck_dss_one CHECK (
        (CASE WHEN part_number_id   IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN family_id        IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN aircraft_type    IS NOT NULL THEN 1 ELSE 0 END
       + CASE WHEN product_category IS NOT NULL THEN 1 ELSE 0 END) = 1),
    CONSTRAINT ck_dss_match CHECK (
        CASE scope_kind
             WHEN 'PART_NUMBER'      THEN part_number_id IS NOT NULL
             WHEN 'FAMILY'           THEN family_id IS NOT NULL
             WHEN 'AIRCRAFT_TYPE'    THEN aircraft_type IS NOT NULL
             WHEN 'PRODUCT_CATEGORY' THEN product_category IS NOT NULL
        END),
    -- 限制栏为空必须是一句明话。
    -- 附2 的括号里写着"限制", 空着可能是"无限制", 也可能是"忘了抄"——
    -- 这两种在符合性自评里完全不同, 而一个空格分不出来。
    CONSTRAINT ck_dss_limit CHECK (
        no_limitation_declared OR length(btrim(coalesce(limitation, ''))) > 0),
    CONSTRAINT ck_dss_source CHECK (length(btrim(source_ref)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_das_signer_scope_auth ON das_signer_scope (auth_id);

COMMENT ON TABLE das_signer_scope IS
    '签署授权的产品／型号／件号范围（白名单：手册 3.2「未列入的产品类别不得签署」）。结构化引用，不是自由文本——PR #5 的自由文本 product_scope 因为没有任何校验读它而被否决移除。';

-- ---------------------------------------------------------------------
-- 2. 项目关联约束（**关联维度，不是范围维度**）
-- ---------------------------------------------------------------------
-- 设计输入第九之二节 C 版更正: 项目引用作为附加的关联约束, 例如受托授权只限该受托项目,
-- 不得顺带签自家 STC 的资料。它**不替代**范围条目 ——
-- 一个项目可以包含多个产品、多个型号件号和不同限制条件。
CREATE TABLE IF NOT EXISTS das_signer_project_bind (
    id          bigserial PRIMARY KEY,
    auth_id     uuid NOT NULL REFERENCES signer_authorization (id) ON DELETE CASCADE,
    project_id  bigint NOT NULL REFERENCES das_project (id),
    reason      text NOT NULL,              -- 为什么限定在这个项目
    created_by  uuid REFERENCES app_user (id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_dspb UNIQUE (auth_id, project_id),
    CONSTRAINT ck_dspb_reason CHECK (length(btrim(reason)) > 0)
);

COMMENT ON TABLE das_signer_project_bind IS
    '把一条签署授权限定在特定项目上（关联维度）。典型用途：受托项目的授权不得顺带签自家 STC 的资料。不替代 das_signer_scope——「同属一个项目」不能证明「签署范围相符」。';

-- ---------------------------------------------------------------------
-- 3. 没有范围条目的授权，必须显式声明
-- ---------------------------------------------------------------------
-- 存量授权一条范围都没有。按手册 3.2 的白名单口径, 它们什么都不该能签 ——
-- 但这一刀切下去会让现有签署全部停摆, 而停摆不会让符合性变好, 只会让人绕过系统。
-- 所以给一条**显式声明**的路: 写明"范围由纸面授权书把关", 带声明人与日期,
-- 并进 das_signer_scope_paper_only 交独立监督核对。
-- 关键是它不是一个默认值, 是一条签了名的陈述 —— 和 0042 的 verifier_is_external、
-- 0041 的 manual_control 同一个道理: 不填必须是一句明话, 不能是一个空格。
CREATE TABLE IF NOT EXISTS das_signer_scope_waiver (
    auth_id     uuid PRIMARY KEY REFERENCES signer_authorization (id) ON DELETE CASCADE,
    paper_ref   text NOT NULL,              -- 纸面授权书编号
    reason      text NOT NULL,
    declared_by uuid NOT NULL REFERENCES app_user (id),
    declared_on date NOT NULL DEFAULT current_date,
    created_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_dssw_text CHECK (
        length(btrim(paper_ref)) > 0 AND length(btrim(reason)) > 0)
);

COMMENT ON TABLE das_signer_scope_waiver IS
    '声明某条授权的范围由纸面授权书把关。不是默认值，是一条带声明人与日期的陈述；全部进 das_signer_scope_paper_only 交独立监督核对。';

-- ---------------------------------------------------------------------
-- 4. 覆盖判断
-- ---------------------------------------------------------------------
-- 返回三值而不是布尔:
--   true   = 范围覆盖
--   false  = 范围**不**覆盖（白名单里没有这一项 → 手册 3.2: 不得签署）
--   NULL   = **判不了**（调用方没给标的, 或该授权没有范围条目也没有声明）
-- 判不了必须与"覆盖"分开。合成布尔的话, 判不了只能折成 true 或 false 之一:
-- 折成 true 就是 PR #5 那个装饰字段的翻版, 折成 false 会把说不出标的的正常签署全拦死。
-- 调用方拿到 NULL 要自己决定怎么办, 并且这个决定是看得见的（见第 7 节视图）。
CREATE OR REPLACE FUNCTION das_signer_scope_covers(
        p_auth_id uuid,
        p_approval_type text,
        p_part_number_id uuid,
        p_family_id uuid,
        p_aircraft_type text,
        p_product_category text)
RETURNS boolean AS $$
DECLARE
    v_rows int;
BEGIN
    SELECT count(*) INTO v_rows FROM das_signer_scope WHERE auth_id = p_auth_id;
    IF v_rows = 0 THEN
        -- 有声明就是"由纸面把关", 同样判不了（不是覆盖, 也不是不覆盖）。
        RETURN NULL;
    END IF;
    -- 一个标的都没给: 判不了。**不是放行**。
    IF p_part_number_id IS NULL AND p_family_id IS NULL
       AND p_aircraft_type IS NULL AND p_product_category IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN EXISTS (
        SELECT 1 FROM das_signer_scope s
         WHERE s.auth_id = p_auth_id
           -- 批准类型给了就要相符; 没给则不以此设限（但标的仍要相符）。
           AND (p_approval_type IS NULL OR s.approval_type_code = p_approval_type)
           AND (
                (s.scope_kind = 'PART_NUMBER'
                     AND s.part_number_id = p_part_number_id)
             OR (s.scope_kind = 'FAMILY' AND s.family_id = p_family_id)
             -- 件号属于某图号族时, 族级授权覆盖该件号。
             OR (s.scope_kind = 'FAMILY' AND p_part_number_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM part_number pn
                                  WHERE pn.id = p_part_number_id
                                    AND pn.basic_drawing_family_id = s.family_id))
             OR (s.scope_kind = 'AIRCRAFT_TYPE'
                     AND s.aircraft_type = p_aircraft_type)
             OR (s.scope_kind = 'PRODUCT_CATEGORY'
                     AND s.product_category = p_product_category)
           ));
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION das_signer_scope_covers IS
    '范围是否覆盖该标的。三值：true 覆盖／false 不覆盖（白名单外，不得签署）／NULL 判不了（没给标的，或没有范围条目）。NULL 不等于放行。';

-- 项目关联约束: 授权被限定到项目时, 只能用于那些项目。
-- 同样三值: NULL = 没有任何限定（不以此设限）。
CREATE OR REPLACE FUNCTION das_signer_project_allowed(p_auth_id uuid, p_project_id bigint)
RETURNS boolean AS $$
DECLARE
    v_rows int;
BEGIN
    SELECT count(*) INTO v_rows FROM das_signer_project_bind WHERE auth_id = p_auth_id;
    IF v_rows = 0 THEN
        RETURN NULL;
    END IF;
    IF p_project_id IS NULL THEN
        RETURN NULL;
    END IF;
    RETURN EXISTS (SELECT 1 FROM das_signer_project_bind b
                    WHERE b.auth_id = p_auth_id AND b.project_id = p_project_id);
END;
$$ LANGUAGE plpgsql STABLE;

-- ---------------------------------------------------------------------
-- 5. 不变量
-- ---------------------------------------------------------------------

-- DCMS-INV-100: 范围条目的批准类型须与项目关联（若有）相容。
-- 把一条只限受托项目的授权, 配上 STC 批准类型的范围条目, 是自相矛盾的登记:
-- 受托项目不产出设计批准（判据 M3-1）, 这条范围永远用不上, 而它会出现在授权名单上。
CREATE OR REPLACE FUNCTION dcms_check_das_signer_scope() RETURNS trigger AS $$
DECLARE
    v_bound int;
    v_types text[];
BEGIN
    -- 【用数组成员判, 不用字符串 position】
    -- 先写的是 position(NEW.approval_type_code in string_agg(...)) —— 子串匹配。
    -- 现在的三个类型码 STC／PMA／SUP 互不为前缀, 碰巧是对的; 但哪天加一个
    -- 'ST' 或 'SUP2', 子串匹配就会把不相干的类型判成相符, 而这种错不会报警,
    -- 只会让一条自相矛盾的授权悄悄通过。
    SELECT count(*), array_agg(DISTINCT p.type_code)
      INTO v_bound, v_types
      FROM das_signer_project_bind b JOIN das_project p ON p.id = b.project_id
     WHERE b.auth_id = NEW.auth_id;
    IF v_bound > 0 AND NOT (NEW.approval_type_code = ANY (v_types)) THEN
        RAISE EXCEPTION 'DCMS-INV-100: 这条授权已被限定在%类项目上, 范围条目的批准类型却是 %。'
                        '限定与范围自相矛盾的登记用不上, 但它会出现在授权名单上, 让人以为有这个权限。',
                        array_to_string(v_types, '、'), NEW.approval_type_code;
    END IF;
    -- 同一授权上不得既有范围条目又声明"由纸面把关"。
    -- 两者并存时, 覆盖判断该听谁的? 听范围条目就等于声明无效, 听声明就等于范围条目是装饰。
    IF EXISTS (SELECT 1 FROM das_signer_scope_waiver WHERE auth_id = NEW.auth_id) THEN
        RAISE EXCEPTION 'DCMS-INV-100: 这条授权已声明"范围由纸面授权书把关", 不得同时登记范围条目。'
                        '两者并存时覆盖判断听谁的说不清 —— 听条目则声明无效, 听声明则条目是装饰。'
                        '要改成系统管范围, 先撤销声明。';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_signer_scope_check ON das_signer_scope;
CREATE TRIGGER trg_das_signer_scope_check
    BEFORE INSERT OR UPDATE ON das_signer_scope
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_signer_scope();

-- DCMS-INV-100（另一侧）: 加项目限定时, 已有的范围条目也不得与之矛盾。
-- 【一条约束要两边都拦】
-- 先只在范围条目那一侧判了, 于是反过来做就能绕: 先登记一条 STC 范围, 再把授权限定到
-- 受托项目, 矛盾照样成立, 而两步各自都通过了。
-- 这与 100／101 那对互斥是同一个教训 —— 那一对一开始就写了两个方向, 这一条漏了。
-- 加第一条限定会收窄: 原先没有限定时任何批准类型的范围都成立, 加上限定之后就不成立了。
-- 这正是要的效果: 限定本身就是收窄。
CREATE OR REPLACE FUNCTION dcms_check_das_signer_bind() RETURNS trigger AS $$
DECLARE
    v_types text[];
    v_bad   text;
BEGIN
    SELECT array_agg(DISTINCT p.type_code) INTO v_types
      FROM das_signer_project_bind b JOIN das_project p ON p.id = b.project_id
     WHERE b.auth_id = NEW.auth_id;
    v_types := coalesce(v_types, ARRAY[]::text[])
             || (SELECT type_code FROM das_project WHERE id = NEW.project_id);
    SELECT string_agg(DISTINCT s.approval_type_code, '、') INTO v_bad
      FROM das_signer_scope s
     WHERE s.auth_id = NEW.auth_id
       AND NOT (s.approval_type_code = ANY (v_types));
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'DCMS-INV-100: 这条授权已有批准类型为 % 的范围条目, 与限定到%类项目矛盾。'
                        '限定与范围自相矛盾的登记用不上, 但它会出现在授权名单上, 让人以为有这个权限。'
                        '要改成只限这类项目, 先撤销原授权并按新范围重新授权。',
                        v_bad, array_to_string(v_types, '、');
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_signer_bind_check ON das_signer_project_bind;
CREATE TRIGGER trg_das_signer_bind_check
    BEFORE INSERT OR UPDATE ON das_signer_project_bind
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_signer_bind();

-- DCMS-INV-101: 已有范围条目的授权不得再声明"由纸面把关"（反向同一条）。
CREATE OR REPLACE FUNCTION dcms_check_das_signer_waiver() RETURNS trigger AS $$
DECLARE
    v_n int;
BEGIN
    SELECT count(*) INTO v_n FROM das_signer_scope WHERE auth_id = NEW.auth_id;
    IF v_n > 0 THEN
        RAISE EXCEPTION 'DCMS-INV-101: 这条授权已有 % 条范围条目, 不得再声明"由纸面授权书把关"。'
                        '声明的意思是系统不管范围; 系统已经在管了, 再声明一句不管, 两者必有一个是假的。',
                        v_n;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_signer_waiver_check ON das_signer_scope_waiver;
CREATE TRIGGER trg_das_signer_waiver_check
    BEFORE INSERT OR UPDATE ON das_signer_scope_waiver
    FOR EACH ROW EXECUTE FUNCTION dcms_check_das_signer_waiver();

-- DCMS-INV-102: 范围条目与项目限定均不得删除, 只能随授权撤销一并失效。
-- 它们是授权内容的一部分, 而授权记录按判据 N14 只可撤销不可删除 ——
-- 范围能删掉, 等于可以悄悄把一条窄授权改宽, 而授权本身看上去没动过。
CREATE OR REPLACE FUNCTION dcms_guard_das_signer_scope() RETURNS trigger AS $$
BEGIN
    -- 授权行被删时级联过来的, 放行（signer_authorization 自己有不可删除的约束）。
    IF EXISTS (SELECT 1 FROM signer_authorization WHERE id = OLD.auth_id) THEN
        RAISE EXCEPTION 'DCMS-INV-102: % 不得%。授权范围是授权内容的一部分（判据 N14: 只可撤销不可删除）—— '
                        '范围能删掉, 就能悄悄把一条窄授权改宽, 而授权本身看上去没动过。'
                        '范围变了要撤销原授权并重新授权。',
                        TG_TABLE_NAME,
                        CASE TG_OP WHEN 'DELETE' THEN '删除' ELSE '改写' END;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_das_signer_scope_guard ON das_signer_scope;
CREATE TRIGGER trg_das_signer_scope_guard
    BEFORE UPDATE OR DELETE ON das_signer_scope
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_signer_scope();

DROP TRIGGER IF EXISTS trg_das_signer_bind_guard ON das_signer_project_bind;
CREATE TRIGGER trg_das_signer_bind_guard
    BEFORE UPDATE OR DELETE ON das_signer_project_bind
    FOR EACH ROW EXECUTE FUNCTION dcms_guard_das_signer_scope();

-- ---------------------------------------------------------------------
-- 6. 符合性声明的范围校验（唯一的落地强制点）
-- ---------------------------------------------------------------------
-- 这里是全系统**唯一**说得出标的的签署路径: 符合性声明签给某个项目,
-- 项目带着批准类型（= 项目类型）与航空器型号。
-- 判据 I10.PRODUCT_SCOPE 在这一处是真的在校验; 其余签署路径仍未施加,
-- 由 das_signer_scope_unenforced 如实列出。
--
-- 三值的用法在这里很关键:
--   false  → 拒绝（白名单外, 手册 3.2）
--   NULL   → 放行, 但记一笔"本次未施加范围校验"（das_compliance_statement.scope_checked）
--   true   → 放行
-- NULL 放行是明知的让步; 不放行会把没来得及登记范围的存量授权全拦死,
-- 而那不会让符合性变好。关键是它**看得见**: 自评要数得出有多少份声明是没校验范围签出去的。
ALTER TABLE das_compliance_statement
    ADD COLUMN IF NOT EXISTS scope_checked boolean;
ALTER TABLE das_compliance_statement
    ADD COLUMN IF NOT EXISTS scope_auth_id uuid REFERENCES signer_authorization (id);

COMMENT ON COLUMN das_compliance_statement.scope_checked IS
    '本次签署是否真的校验了产品范围。true 已校验且覆盖；false 不应出现（不覆盖会被拒）；NULL 判不了（签署人没有范围条目，或已声明由纸面把关）——自评要数得出有多少份声明是没校验范围签出去的。';

CREATE OR REPLACE FUNCTION dcms_check_statement_scope() RETURNS trigger AS $$
DECLARE
    v_type text;
    v_ac   text;
    v_pid  bigint;
    r      record;
    v_any  boolean := false;        -- 该签署人有没有在册的有效授权
    v_hit  uuid;                    -- 明确覆盖的那一条
    v_unk  uuid;                    -- 判不了的那一条
    v_prj_blocked boolean := false; -- 有授权被项目限定挡住
    v_scope_judged boolean := false;-- 有授权过了项目这道门、但范围不覆盖
BEGIN
    SELECT p.type_code, p.aircraft_type, p.id INTO v_type, v_ac, v_pid
      FROM das_project p WHERE p.id = NEW.project_id;

    -- 【逐条判, 不取"最近一条"】
    -- 先写的是 ORDER BY granted_at DESC LIMIT 1 —— 随便取一条。一个人可以同时持
    -- CVE 与 审核 两级授权, 范围各不相同; 取最近那条既会错拒（覆盖的那条没被看）
    -- 也会错放（宽的那条被看、窄的没被看）, 而错在哪一边取决于当初的授权顺序。
    -- 正确的判法: 任一在册有效授权覆盖即可, 全都不覆盖才拒。
    FOR r IN SELECT sa.id FROM signer_authorization sa
              WHERE sa.user_id = NEW.signed_by AND sa.revoked_at IS NULL
                AND sa.valid_from <= NEW.signed_on
                AND (sa.valid_to IS NULL OR sa.valid_to >= NEW.signed_on)
              ORDER BY sa.granted_at
    LOOP
        v_any := true;
        -- 项目限定不满足的这一条直接跳过（不是整体拒绝: 别的授权可能没有限定）
        IF das_signer_project_allowed(r.id, v_pid) IS FALSE THEN
            v_prj_blocked := true;
            CONTINUE;
        END IF;
        -- 覆盖判断只算一次: 调两遍既多一次查询, 也给了两次结果不一致的机会。
        CASE das_signer_scope_covers(r.id, v_type, NULL, NULL, v_ac, NULL)
            WHEN true THEN
                v_hit := r.id;
                EXIT;               -- 明确覆盖, 不必再看
            WHEN false THEN
                v_scope_judged := true;
            ELSE
                v_unk := coalesce(v_unk, r.id);   -- 判不了的留着兜底
        END CASE;
    END LOOP;

    IF NOT v_any THEN
        -- 没有任何有效签署授权。这里不拦: 符合性声明的签署资格由 DCMS-INV-094 管
        -- （在任责任经理, 或写明纸面授权依据）。两件事混在一处, 报错就说不清是哪一项不符。
        NEW.scope_checked := NULL;
        NEW.scope_auth_id := NULL;
        RETURN NEW;
    END IF;

    IF v_hit IS NOT NULL THEN
        NEW.scope_checked := true;
        NEW.scope_auth_id := v_hit;
        RETURN NEW;
    END IF;

    IF v_unk IS NOT NULL THEN
        -- 判不了: 放行, 但记明本次未施加范围校验（进 das_compliance_statement_scope_gap）。
        NEW.scope_checked := NULL;
        NEW.scope_auth_id := v_unk;
        RETURN NEW;
    END IF;

    -- 到这里说明每一条授权都明确不覆盖, 或都被项目限定挡住。
    -- 【两种拒绝的先后顺序要紧】
    -- 有授权过了项目这道门、只是范围不覆盖时, 该报的是范围 —— 那才是人要去改的东西。
    -- 先判 v_prj_blocked 的话, 一个人只要另外还持着一条限定在别的项目上的授权,
    -- 报出来的就永远是"授权被限定在其它项目上", 而真正的原因（型号不在范围内）被盖住。
    IF NOT v_scope_judged AND v_prj_blocked THEN
        RAISE EXCEPTION 'DCMS-INV-103: 该签署人的授权均被限定在其它项目上, 本项目不在其中。'
                        '设计输入第九之二节: 受托授权只限该受托项目, 不得顺带签自家 STC 的资料。';
    END IF;
    RAISE EXCEPTION 'DCMS-INV-103: 本项目的批准类型(%)与航空器型号(%)不在该签署人任何一条授权的范围内。'
                    '手册 3.2: 授权范围按产品类别分别确定, **未列入的产品类别不得签署**。',
                    v_type, coalesce(v_ac, '未填');
END;
$$ LANGUAGE plpgsql;

-- 顺序要紧: 本触发器改 NEW 的两列, 须在 094 那条校验之后、写入之前。
-- 触发器同为 BEFORE 时按名字排序执行, 所以这里用 z 前缀压到最后。
DROP TRIGGER IF EXISTS z_trg_statement_scope ON das_compliance_statement;
CREATE TRIGGER z_trg_statement_scope
    BEFORE INSERT ON das_compliance_statement
    FOR EACH ROW EXECUTE FUNCTION dcms_check_statement_scope();

-- ---------------------------------------------------------------------
-- 7. 视图
-- ---------------------------------------------------------------------

-- 7.1 授权的范围登记情况。
CREATE OR REPLACE VIEW das_signer_scope_registry AS
SELECT sa.id AS auth_id, u.full_name, u.employee_no, sa.level,
       sa.file_type_code, sa.discipline_code, sa.valid_from, sa.valid_to,
       (sa.revoked_at IS NOT NULL) AS revoked,
       (SELECT count(*) FROM das_signer_scope s WHERE s.auth_id = sa.id) AS scope_rows,
       (SELECT count(*) FROM das_signer_project_bind b WHERE b.auth_id = sa.id)
                                                                   AS project_binds,
       (SELECT w.paper_ref FROM das_signer_scope_waiver w WHERE w.auth_id = sa.id)
                                                                   AS paper_only_ref,
       CASE WHEN (SELECT count(*) FROM das_signer_scope s WHERE s.auth_id = sa.id) > 0
            THEN 'SYSTEM_ENFORCED'
            WHEN EXISTS (SELECT 1 FROM das_signer_scope_waiver w WHERE w.auth_id = sa.id)
            THEN 'PAPER_ONLY'
            ELSE 'UNDECLARED'
       END AS scope_state
  FROM signer_authorization sa JOIN app_user u ON u.id = sa.user_id
 ORDER BY u.full_name, sa.level;

COMMENT ON VIEW das_signer_scope_registry IS
    '每条签署授权的范围登记情况。UNDECLARED 既没登记范围也没声明由纸面把关——这一档必须清零：手册 3.2 的白名单口径下它什么都不该能签，而现在它什么都能签。';

-- 7.2 **既没登记范围、也没声明由纸面把关的授权。这一档必须清零。**
-- 它不是"还没来得及"的待办, 是一条说不出依据的授权: 按手册 3.2 的白名单口径
-- 它什么都不该能签, 而在落地强制点之外它什么都能签。
CREATE OR REPLACE VIEW das_signer_scope_undeclared AS
SELECT * FROM das_signer_scope_registry
 WHERE scope_state = 'UNDECLARED' AND NOT revoked;

-- 7.3 声明"范围由纸面授权书把关"的, 交独立监督核对纸面授权书。
CREATE OR REPLACE VIEW das_signer_scope_paper_only AS
SELECT r.auth_id, r.full_name, r.employee_no, r.level, r.paper_only_ref,
       w.reason, w.declared_on, du.full_name AS declared_by_name
  FROM das_signer_scope_registry r
  JOIN das_signer_scope_waiver w ON w.auth_id = r.auth_id
  JOIN app_user du ON du.id = w.declared_by
 WHERE NOT r.revoked
 ORDER BY w.declared_on DESC;

-- 7.4 **范围校验尚未施加的签署路径。**
-- 判据 I10.PRODUCT_SCOPE 的如实状态就在这张表里: 只有符合性声明这一条路在校验,
-- 因为只有它说得出标的。其余路径不是"漏了", 是调用方传不出标的 ——
-- 传不出标的时默默放行, 正是判据 I10.DISCIPLINE 已有的那个缺口,
-- 明知故犯地再来一次是不行的, 所以如实列出来。
CREATE OR REPLACE VIEW das_signer_scope_unenforced AS
SELECT * FROM (VALUES
    ('文件三级签署（files／approval_step）', '调用方只传文件类型与专业, 不传产品标的',
     '签署流程未与项目实体关联; 关联后才谈得上范围校验'),
    ('签署授权的授予与撤销（signers.grant／revoke）', '授予时不涉及具体标的',
     '不适用: 这是授权管理, 不是签署'),
    ('制造符合性声明（UG-DAF-12）', '尚未建模（M3 的下一片）',
     '试验前的制造符合性声明按 UG-DAP-07 第 3 步签署, 标的是试验产品'),
    ('小改批准（UG-DAP-08）', '尚未建模（M5）', '标的是具体更改对象')
  ) AS t(path, why_unenforced, note);

COMMENT ON VIEW das_signer_scope_unenforced IS
    '范围校验尚未施加的签署路径及原因。判据 I10.PRODUCT_SCOPE 的如实状态：只有符合性声明一条路在校验，因为只有它说得出标的。';

-- 7.5 没有校验范围就签出去的符合性声明。自评要数得出这个数。
CREATE OR REPLACE VIEW das_compliance_statement_scope_gap AS
SELECT c.id, c.statement_no, p.project_no, p.type_code, p.aircraft_type,
       u.full_name AS signed_by_name, c.signed_on,
       CASE WHEN c.scope_auth_id IS NULL THEN '签署人无有效签署授权（资格另由 094 管）'
            WHEN EXISTS (SELECT 1 FROM das_signer_scope_waiver w
                          WHERE w.auth_id = c.scope_auth_id)
                 THEN '该授权已声明范围由纸面授权书把关'
            ELSE '该授权没有登记范围条目'
       END AS why
  FROM das_compliance_statement c
  JOIN das_project p ON p.id = c.project_id
  JOIN app_user u ON u.id = c.signed_by
 WHERE c.scope_checked IS NULL
 ORDER BY c.signed_on DESC;

-- ---------------------------------------------------------------------
-- 8. 独立性登记册: I10.PRODUCT_SCOPE 从"未实现"改为"部分实现"
-- ---------------------------------------------------------------------
-- 改的是**如实状态**, 不是"做完了"。改动会由 0041 的触发器自动留痕。
-- 措辞要精确到说得出在哪一处施加、在哪几处没施加 —— 笼统写"已实现"就是假符合,
-- 而这一条的笼统正是判据 I10 点名禁止的。
UPDATE das_independence_rule SET
    source_state = 'PARTIAL',
    enforcement_kind = 'DB_TRIGGER',
    enforcement_object = 'z_trg_statement_scope',
    invariant_code = 'DCMS-INV-103',
    test_state = 'DEFINED',
    test_ref = 'ci/test-das-signer-scope.py：「白名单外的型号被拒」'
               '「受托授权签自家 STC 项目被拒」「没有范围条目时判不了而不是放行」'
               '「范围条目不得删改」',
    manual_control = '仅符合性声明一条签署路径施加系统校验（只有它说得出标的）；'
                     '文件三级签署、制造符合性声明、小改批准三条路径仍由纸面授权书把关，'
                     '逐条见 das_signer_scope_unenforced。'
                     '未登记范围且未声明的授权由 das_signer_scope_undeclared 列出，'
                     '该档须清零',
    gap_note = '自由文本 product_scope 已于提交 1f39b89 移除（理由见设计输入第九之二节：'
               '一个不被任何校验读取的字段比没有这个字段更糟）。'
               '0044 按第九之二节 C 版更正重做：范围与限制单独建模（das_signer_scope，'
               '结构化引用可比对），项目引用作为**附加的**关联约束'
               '（das_signer_project_bind）而不替代范围——一个项目可含多个产品、'
               '多个型号件号和不同限制，「同属一个项目」不能证明「签署范围相符」。'
               '**仍记为部分实现**：覆盖判断返回三值，调用方说不出标的时为 NULL（判不了），'
               'NULL 在符合性声明处放行但记入 das_compliance_statement_scope_gap；'
               '全系统只有符合性声明一条路说得出标的'
 WHERE code = 'I10.PRODUCT_SCOPE';
