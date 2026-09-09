-- =====================================================================
-- UG-DCMS 迁移 0015 — 50,000 个设计对象综合业务数据
--
-- 这是用户明确要求的验收/演示数据集。所有业务编码统一使用 DEMO50K
-- 前缀；脚本可重入（迁移框架只执行一次，INSERT 同时带冲突保护）。
-- 50,000 指 design_object 主记录数：内部件 30,000、外部件 15,000、
-- 软件 5,000。关联表另行生成，数据库新增总行数远大于 50,000。
-- =====================================================================

DO $$
BEGIN
  IF (SELECT count(*) FROM design_object WHERE object_code LIKE 'DEMO50K-%') NOT IN (0, 50000) THEN
    RAISE EXCEPTION 'DCMS-DEMO50K: 检测到不完整的既有演示数据，请先恢复数据库后重试';
  END IF;
END $$;

-- ---------------------------------------------------------------------
-- 10 个业务账户（权限仍使用冻结基准中的六个 RBAC 角色）。
-- 初始口令均为 Demo@12345，首次登录强制修改。
-- ---------------------------------------------------------------------
INSERT INTO app_user(username,full_name,email,password_hash,must_change_password)
VALUES
 ('demo_sysadmin','演示-系统管理员','demo_sysadmin@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_dataadmin','演示-数据管理员','demo_dataadmin@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_config','演示-构型经理','demo_config@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_project','演示-项目经理','demo_project@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_engineer1','演示-总体工程师','demo_engineer1@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_engineer2','演示-航电工程师','demo_engineer2@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_approver1','演示-技术批准人','demo_approver1@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_approver2','演示-质量批准人','demo_approver2@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_supplier','演示-供应商协同','demo_supplier@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true),
 ('demo_auditor','演示-审计员','demo_auditor@ug-dcms.local',crypt('Demo@12345',gen_salt('bf',12)),true)
ON CONFLICT(username) DO NOTHING;

INSERT INTO user_role(user_id,role_code)
SELECT u.id,v.role_code FROM (VALUES
 ('demo_sysadmin','SYSTEM_ADMIN'),('demo_sysadmin','DATA_ADMIN'),
 ('demo_dataadmin','DATA_ADMIN'),('demo_config','CONFIGURATION_MANAGER'),
 ('demo_project','CONFIGURATION_MANAGER'),('demo_project','APPROVER'),
 ('demo_engineer1','ENGINEER'),('demo_engineer2','ENGINEER'),
 ('demo_approver1','APPROVER'),('demo_approver2','APPROVER'),
 ('demo_supplier','VIEWER'),('demo_auditor','VIEWER')) v(username,role_code)
JOIN app_user u ON u.username=v.username
ON CONFLICT DO NOTHING;

-- 本数据集的稳定创建人/申请人/审批人。
CREATE TEMP TABLE demo_actor AS
SELECT
 max(id) FILTER(WHERE username='demo_engineer1') AS engineer1,
 max(id) FILTER(WHERE username='demo_engineer2') AS engineer2,
 max(id) FILTER(WHERE username='demo_config') AS config_mgr,
 max(id) FILTER(WHERE username='demo_project') AS project_mgr,
 max(id) FILTER(WHERE username='demo_approver1') AS approver1,
 max(id) FILTER(WHERE username='demo_approver2') AS approver2
FROM app_user;

-- ---------------------------------------------------------------------
-- 1,000 个设计族，每族 30 个 Dash P/N = 30,000 个内部设计对象。
-- 产品层次通过对象层级、命名和无环 BOM 表达；项目控制不下沉为飞机级。
-- ---------------------------------------------------------------------
WITH dict AS (
 SELECT pc.code AS physical_code,pc.primary_class_code,
        row_number() OVER(ORDER BY pc.sort_order,pc.code) AS rn,
        count(*) OVER() AS cnt
 FROM physical_class pc WHERE pc.status='ACTIVE' AND pc.primary_class_code IN('T1','T2','T3')
), core AS (
 SELECT n.id,n.primary_class_code,row_number() OVER(PARTITION BY n.primary_class_code ORDER BY n.code) rn,
        count(*) OVER(PARTITION BY n.primary_class_code) cnt
 FROM naming_core_term n WHERE n.status='ACTIVE'
), seed AS (
 SELECT g,
   CASE WHEN g%10 IN(0,1,2,3,4) THEN 'T2' WHEN g%10 IN(5,6) THEN 'T3' ELSE 'T1' END AS pclass
 FROM generate_series(1,1000) g
), picked AS (
 SELECT s.*,d.physical_code,
   CASE WHEN s.g%25=1 THEN 'PSU电源系统'
        WHEN s.g%25=2 THEN 'IMA综合模块化航电平台'
        WHEN s.g%25=3 THEN 'LRI航线可更换项目'
        WHEN s.g%25=4 THEN '复杂产品顶层组件'
        WHEN s.g%25 BETWEEN 5 AND 8 THEN '航电系统设备'
        ELSE '通用设计组件' END AS product_name
 FROM seed s JOIN LATERAL(
   SELECT physical_code FROM dict d WHERE d.primary_class_code=s.pclass
   ORDER BY d.rn OFFSET ((s.g-1)%(SELECT count(*) FROM dict x WHERE x.primary_class_code=s.pclass)) LIMIT 1
 ) d ON true
)
INSERT INTO basic_drawing_family(
 basic_drawing_number,primary_class_code,physical_class_id,object_level_code,
 family_name_cn,family_name_en,core_term_id,family_definition,allowed_variation,
 excluded_variation,status,similar_check_at,similar_check_result,reuse_decision,
 new_family_reason,approved_by,approved_at,created_by)
SELECT 'DEMO50K-BD-'||lpad(p.g::text,4,'0'),p.pclass,pc.id,
 CASE WHEN p.g%25=4 THEN 'END_ITEM' WHEN p.g%25 IN(1,2,3) THEN 'EQUIPMENT'
      WHEN p.g%5=0 THEN 'MODULE' ELSE 'ASSEMBLY' END,
 p.product_name||'-'||lpad(p.g::text,4,'0'),'Demo '||p.product_name||' '||p.g,
 c.id,'演示数据：'||p.product_name||'的稳定设计族定义','允许接口、安装和性能范围内的受控 Dash 差异',
 '不允许改变产品级功能边界或绕过变更审批','ACTIVE',now(),
 jsonb_build_object('checked',true,'dataset','DEMO50K'),'NEW_FAMILY','综合业务验收数据集',
 a.approver1,now(),a.engineer1
FROM picked p JOIN physical_class pc ON pc.code=p.physical_code
JOIN LATERAL(SELECT id FROM core c WHERE c.primary_class_code=p.pclass ORDER BY c.rn OFFSET ((p.g-1)%(SELECT count(*) FROM core x WHERE x.primary_class_code=p.pclass)) LIMIT 1)c ON true
CROSS JOIN demo_actor a ON CONFLICT(basic_drawing_number) DO NOTHING;

INSERT INTO design_object(object_type,object_code,display_name,lifecycle_status,data_maturity,created_by)
SELECT 'INTERNAL_PART','DEMO50K-I-'||lpad(g::text,5,'0'),
 CASE WHEN g<=1200 THEN
   (ARRAY['复杂产品顶层','PSU电源分配单元','IMA核心处理模块','IMA网络交换模块','远程数据集中器','LRI航电设备'])[(g%6)+1]
  ELSE (ARRAY['结构组件','电子组件','线束组件','安装组件','控制组件'])[(g%5)+1] END||'-'||g,
 CASE g%10 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' WHEN 2 THEN 'OBSOLETE' ELSE 'RELEASED' END::lifecycle_status,
 CASE WHEN g%10<2 THEN 'L2' ELSE 'L4' END,a.engineer1
FROM generate_series(1,30000) g CROSS JOIN demo_actor a
ON CONFLICT(object_code) DO NOTHING;

INSERT INTO part_number(design_object_id,basic_drawing_family_id,dash_number,full_part_number,
 formal_name_cn,formal_name_en,object_level_code,lifecycle_status,difference_summary,created_by)
SELECT d.id,f.id,((n-1)%30)+1,d.object_code,d.display_name,'Demo internal part '||n,
 CASE WHEN n<=1200 AND n%100=1 THEN 'END_ITEM' WHEN n<=1200 THEN 'EQUIPMENT'
      WHEN n%7=0 THEN 'MODULE' WHEN n%3=0 THEN 'ASSEMBLY' ELSE 'PART' END,
 d.lifecycle_status,'Dash差异：接口、材料或安装选项的受控变化',a.engineer1
FROM (SELECT id,object_code,display_name,lifecycle_status,
             substring(object_code from '[0-9]+$')::int n
      FROM design_object WHERE object_code LIKE 'DEMO50K-I-%')d
JOIN basic_drawing_family f ON f.basic_drawing_number='DEMO50K-BD-'||lpad((((d.n-1)/30)+1)::text,4,'0')
CROSS JOIN demo_actor a ON CONFLICT(full_part_number) DO NOTHING;

-- ---------------------------------------------------------------------
-- 15,000 外部件：覆盖 E01~E29/E90/E99、技术状态、项目级准入。
-- ---------------------------------------------------------------------
INSERT INTO design_object(object_type,object_code,display_name,lifecycle_status,data_maturity,created_by)
SELECT 'EXTERNAL_PART','DEMO50K-E-'||lpad(g::text,5,'0'),
 (ARRAY['连接器','电源模块','传感器','风扇','紧固件','继电器','线缆','计算模块'])[(g%8)+1]||'-'||g,
 CASE g%8 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' WHEN 2 THEN 'OBSOLETE' ELSE 'RELEASED' END::lifecycle_status,
 CASE WHEN g%8<2 THEN 'L2' ELSE 'L4' END,a.engineer2
FROM generate_series(1,15000) g CROSS JOIN demo_actor a ON CONFLICT(object_code) DO NOTHING;

WITH ns AS (SELECT id FROM namespace WHERE status='ACTIVE' ORDER BY code LIMIT 1),
cls AS (SELECT code,row_number() OVER(ORDER BY sort_order,code) rn,count(*) OVER() cnt FROM external_part_class WHERE status='ACTIVE')
INSERT INTO external_part(design_object_id,namespace_id,external_part_number,manufacturer_id,name_cn,name_en,lifecycle_status,external_class_code,created_by)
SELECT d.id,ns.id,'SUP-'||lpad(d.n::text,7,'0'),
 (SELECT id FROM manufacturer WHERE status='ACTIVE' ORDER BY code OFFSET ((d.n-1)%GREATEST((SELECT count(*) FROM manufacturer WHERE status='ACTIVE'),1)) LIMIT 1),
 d.display_name,'Demo supplier part '||d.n,d.lifecycle_status,
 (SELECT code FROM cls ORDER BY rn OFFSET ((d.n-1)%(SELECT count(*) FROM cls)) LIMIT 1),a.engineer2
FROM (SELECT id,display_name,lifecycle_status,substring(object_code from '[0-9]+$')::int n
      FROM design_object WHERE object_code LIKE 'DEMO50K-E-%')d CROSS JOIN ns CROSS JOIN demo_actor a
ON CONFLICT(namespace_id,external_part_number) DO NOTHING;

INSERT INTO external_technical_state(external_part_id,state_sequence,supplier_revision,supplier_document,
 supplier_document_date,hash_sha256,status,accepted_by,accepted_at,notes,created_by)
SELECT e.id,1,'R'||(n%10),'SPEC-DEMO50K-'||lpad(n::text,5,'0'),current_date-(n%730),
 encode(digest('DEMO50K-EXT-STATE-'||n,'sha256'),'hex'),
 CASE n%5 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' WHEN 2 THEN 'REJECTED' ELSE 'ACCEPTED' END,
 CASE WHEN n%5 IN(3,4) THEN a.approver1 END,CASE WHEN n%5 IN(3,4) THEN now()-(n%365)*interval '1 day' END,
 '供应商规范、符合性声明与鉴定证据综合评估',a.engineer2
FROM (SELECT ep.id,substring(ep.external_part_number from '[0-9]+$')::int n FROM external_part ep WHERE ep.external_part_number LIKE 'SUP-%')e
CROSS JOIN demo_actor a ON CONFLICT(external_part_id,state_sequence) DO NOTHING;

INSERT INTO external_part_project_control(external_part_id,project_code,status,applicability,evaluation_basis,
 approved_by,approved_at,created_by)
SELECT e.id,p.project_code,
 CASE (e.n+p.k)%6 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' WHEN 2 THEN 'REJECTED'
      WHEN 3 THEN 'SUSPENDED' ELSE 'APPROVED' END,
 '适用于'||p.project_code||'的PSU/IMA/LRI集成与批次构型','技术状态、环境鉴定、供应保障与项目接口评估',
 CASE WHEN (e.n+p.k)%6 IN(4,5) THEN a.approver2 END,
 CASE WHEN (e.n+p.k)%6 IN(4,5) THEN now()-(e.n%180)*interval '1 day' END,a.project_mgr
FROM (SELECT id,row_number() OVER(ORDER BY external_part_number) n FROM external_part WHERE external_part_number LIKE 'SUP-%' ORDER BY external_part_number LIMIT 6000)e
CROSS JOIN (VALUES(1,'PJT-PSU-A'),(2,'PJT-IMA-B'),(3,'PJT-COMPLEX-C'))p(k,project_code)
CROSS JOIN demo_actor a ON CONFLICT(external_part_id,project_code) DO NOTHING;

-- ---------------------------------------------------------------------
-- 5,000 软件/固件/配置数据及明确版本。
-- ---------------------------------------------------------------------
INSERT INTO design_object(object_type,object_code,display_name,lifecycle_status,data_maturity,created_by)
SELECT 'SOFTWARE','DEMO50K-S-'||lpad(g::text,5,'0'),
 (ARRAY['IMA分区应用','PSU控制固件','平台健康监控','网络配置数据','机载数据库'])[(g%5)+1]||'-'||g,
 CASE g%6 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' ELSE 'RELEASED' END::lifecycle_status,
 CASE WHEN g%6<2 THEN 'L2' ELSE 'L4' END,a.engineer2
FROM generate_series(1,5000) g CROSS JOIN demo_actor a ON CONFLICT(object_code) DO NOTHING;

INSERT INTO software_object(design_object_id,software_number,name_cn,name_en,software_type,lifecycle_status,created_by)
SELECT d.id,d.object_code,d.display_name,'Demo software '||n,
 (ARRAY['SOFTWARE','FIRMWARE','CONFIG_DATA','LOADABLE'])[(n%4)+1],d.lifecycle_status,a.engineer2
FROM (SELECT id,object_code,display_name,lifecycle_status,substring(object_code from '[0-9]+$')::int n
      FROM design_object WHERE object_code LIKE 'DEMO50K-S-%')d CROSS JOIN demo_actor a
ON CONFLICT(software_number) DO NOTHING;

INSERT INTO software_version(software_object_id,version,build,hash_sha256,status,released_at,released_by,notes,created_by)
SELECT s.id,'V'||(1+n%4)||'.'||(n%10),'B'||lpad(n::text,5,'0'),encode(digest('DEMO50K-SW-'||n,'sha256'),'hex'),
 CASE n%4 WHEN 0 THEN 'DRAFT' WHEN 1 THEN 'IN_REVIEW' ELSE 'RELEASED' END,
 CASE WHEN n%4 IN(2,3) THEN now()-(n%300)*interval '1 day' END,
 CASE WHEN n%4 IN(2,3) THEN a.approver1 END,'含PSU控制、IMA分区、加载件和构型数据场景',a.engineer2
FROM (SELECT so.id,row_number() OVER(ORDER BY software_number)n FROM software_object so WHERE software_number LIKE 'DEMO50K-S-%')s
CROSS JOIN demo_actor a ON CONFLICT(software_object_id,version,build) DO NOTHING;

UPDATE software_object so SET current_version_id=v.id
FROM software_version v WHERE v.software_object_id=so.id AND v.status='RELEASED'
AND so.software_number LIKE 'DEMO50K-S-%' AND so.current_version_id IS NULL;

-- ---------------------------------------------------------------------
-- 复杂产品 BOM：3,000 个父对象、24,000 行；子序号始终大于父序号，天然无环。
-- ---------------------------------------------------------------------
INSERT INTO bom_header(parent_design_object_id,status,working_sequence,notes,last_validated_at,
 last_validation_result,created_by)
SELECT d.id,'WORKING',1,'DEMO50K复杂产品/PSU/IMA多层工作BOM',now(),
 jsonb_build_object('valid',true,'cycle',false,'dataset','DEMO50K'),a.engineer1
FROM (SELECT id FROM design_object WHERE object_code LIKE 'DEMO50K-I-%' ORDER BY object_code LIMIT 3000)d
CROSS JOIN demo_actor a ON CONFLICT(parent_design_object_id,working_sequence) DO NOTHING;

WITH parents AS (
 SELECT bh.id,row_number() OVER(ORDER BY d.object_code)n
 FROM bom_header bh JOIN design_object d ON d.id=bh.parent_design_object_id
 WHERE d.object_code LIKE 'DEMO50K-I-%'
), children AS (
 SELECT id,substring(object_code from '[0-9]+$')::int n,object_code FROM design_object
 WHERE object_code LIKE 'DEMO50K-I-%'
)
INSERT INTO bom_line(bom_header_id,item_number,child_design_object_id,quantity,unit_code,
 reference_designator,effectivity,notes,sort_order,created_by)
SELECT p.id,lpad((k*10)::text,4,'0'),c.id,CASE WHEN k%4=0 THEN 2 ELSE 1 END,
 (SELECT code FROM unit WHERE status='ACTIVE' ORDER BY code LIMIT 1),'POS-'||k,
 '项目='||(ARRAY['PJT-PSU-A','PJT-IMA-B','PJT-COMPLEX-C'])[(p.n%3)+1],
 CASE WHEN p.n%25=1 THEN 'PSU层级构成' WHEN p.n%25=2 THEN 'IMA平台模块构成' ELSE '复杂产品多层构成' END,k*10,a.engineer1
FROM parents p CROSS JOIN generate_series(1,8)k
JOIN children c ON c.n=3000+((p.n*8+k-1)%27000)+1 CROSS JOIN demo_actor a
ON CONFLICT(bom_header_id,item_number,child_design_object_id) DO NOTHING;

-- 构型上下文、适用性规则及部分 BOM 行绑定。
INSERT INTO configuration_context(context_code,name_cn,description,attributes,created_by)
VALUES
 ('DEMO50K-CTX-PSU-A','PSU项目A构型','28V直流电源项目构型','{"project":"PJT-PSU-A","platform":"PSU","voltage":"28VDC"}',(SELECT project_mgr FROM demo_actor)),
 ('DEMO50K-CTX-IMA-B','IMA项目B构型','综合模块化航电项目构型','{"project":"PJT-IMA-B","platform":"IMA","network":"AFDX"}',(SELECT project_mgr FROM demo_actor)),
 ('DEMO50K-CTX-CPLX-C','复杂产品C构型','复杂产品综合验证构型','{"project":"PJT-COMPLEX-C","platform":"COMPLEX","batch":"C01"}',(SELECT project_mgr FROM demo_actor))
ON CONFLICT(context_code) DO NOTHING;

INSERT INTO applicability_rule(rule_code,name_cn,expression,description,created_by)
VALUES
 ('DEMO50K-RULE-PSU','仅PSU项目','{"eq":{"project":"PJT-PSU-A"}}','PSU项目级适用性',(SELECT config_mgr FROM demo_actor)),
 ('DEMO50K-RULE-IMA','仅IMA项目','{"eq":{"project":"PJT-IMA-B"}}','IMA项目级适用性',(SELECT config_mgr FROM demo_actor)),
 ('DEMO50K-RULE-CPLX','仅复杂产品项目','{"eq":{"project":"PJT-COMPLEX-C"}}','复杂产品项目级适用性',(SELECT config_mgr FROM demo_actor))
ON CONFLICT(rule_code) DO NOTHING;

INSERT INTO bom_line_applicability(bom_line_id,applicability_rule_id,created_by)
SELECT b.id,r.id,a.config_mgr FROM (
 SELECT bl.id,row_number() OVER(ORDER BY bl.id)n FROM bom_line bl JOIN bom_header bh ON bh.id=bl.bom_header_id
 JOIN design_object d ON d.id=bh.parent_design_object_id WHERE d.object_code LIKE 'DEMO50K-I-%'
)b JOIN applicability_rule r ON r.rule_code=(ARRAY['DEMO50K-RULE-PSU','DEMO50K-RULE-IMA','DEMO50K-RULE-CPLX'])[(b.n%3)+1]
CROSS JOIN demo_actor a WHERE b.n%3=0 ON CONFLICT(bom_line_id) DO NOTHING;

-- ---------------------------------------------------------------------
-- 文件、版次、可检索附件元数据。附件标记 MISSING，明确它们是搜索样本而非伪造文件。
-- ---------------------------------------------------------------------
INSERT INTO design_file(file_number,file_type_code,title_cn,title_en,owner_user_id,created_by)
SELECT 'DEMO50K-F-'||lpad(g::text,5,'0'),(SELECT code FROM file_type WHERE status='ACTIVE' ORDER BY code OFFSET ((g-1)%(SELECT count(*) FROM file_type WHERE status='ACTIVE')) LIMIT 1),
 CASE g%4 WHEN 0 THEN 'PSU电源接口控制文件' WHEN 1 THEN 'IMA平台架构设计文件'
      WHEN 2 THEN '复杂产品装配图样' ELSE '通用符合性与验证文件' END||'-'||g,
 'Demo design document '||g,a.engineer1,a.engineer1
FROM generate_series(1,3000)g CROSS JOIN demo_actor a ON CONFLICT(file_number) DO NOTHING;

INSERT INTO file_revision(design_file_id,revision_number,revision_sequence,status,revision_date,
 change_summary,prepared_by,checked_by,approved_by,released_at,created_by)
SELECT f.id,lpad(r::text,2,'0'),r+1,
 CASE WHEN r=0 AND f.n%5=0 THEN 'WORKING' WHEN r=1 AND f.n%5=1 THEN 'IN_REVIEW'
      WHEN r=0 THEN 'SUPERSEDED' ELSE 'RELEASED' END,current_date-(f.n%500)+r,
 CASE WHEN r=0 THEN '初始设计与接口定义' ELSE '项目适用性、PSU/IMA接口和验证证据更新' END,
 a.engineer1,a.engineer2,CASE WHEN NOT(r=0 AND f.n%5=0) AND NOT(r=1 AND f.n%5=1) THEN a.approver1 END,
 CASE WHEN NOT(r=0 AND f.n%5=0) AND NOT(r=1 AND f.n%5=1) THEN now()-(f.n%400)*interval '1 day' END,a.engineer1
FROM (SELECT id,row_number() OVER(ORDER BY file_number)n FROM design_file WHERE file_number LIKE 'DEMO50K-F-%')f
CROSS JOIN generate_series(0,1)r CROSS JOIN demo_actor a
ON CONFLICT(design_file_id,revision_number) DO NOTHING;

UPDATE design_file f SET current_released_revision_id=x.id
FROM LATERAL(SELECT fr.id FROM file_revision fr WHERE fr.design_file_id=f.id AND fr.status='RELEASED' ORDER BY revision_sequence DESC LIMIT 1)x
WHERE f.file_number LIKE 'DEMO50K-F-%' AND f.current_released_revision_id IS NULL;

INSERT INTO revision_attachment(file_revision_id,attachment_role,filename,storage_key,mime_type,size_bytes,
 sha256,integrity_status,search_text,uploaded_by)
SELECT fr.id,CASE fr.revision_sequence WHEN 1 THEN 'PRIMARY_NATIVE' ELSE 'RELEASED_PDF' END,
 regexp_replace(f.file_number,'DEMO50K-F-','')||CASE fr.revision_sequence WHEN 1 THEN '_PSU_IMA设计说明.docx' ELSE '_复杂产品批准图样.pdf' END,
 'demo50k/'||f.file_number||'/R'||fr.revision_number||CASE fr.revision_sequence WHEN 1 THEN '.docx' ELSE '.pdf' END,
 CASE fr.revision_sequence WHEN 1 THEN 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' ELSE 'application/pdf' END,
 10240+fr.revision_sequence*1024,encode(digest(f.file_number||fr.revision_number,'sha256'),'hex'),'MISSING',
 'DEMO50K 附件全文索引 PSU 电源分配 IMA 综合模块化航电 复杂产品 接口控制 环境鉴定 符合性声明 项目批准 基线',a.engineer1
FROM file_revision fr JOIN design_file f ON f.id=fr.design_file_id CROSS JOIN demo_actor a
WHERE f.file_number LIKE 'DEMO50K-F-%' ON CONFLICT(storage_key) DO NOTHING;

INSERT INTO design_definition_link(design_object_id,design_file_id,relation_type,applicability_note,created_by)
SELECT d.id,f.id,'PRIMARY_DEFINITION','DEMO50K项目级适用，不作为飞机级控制',a.engineer1
FROM (SELECT id,row_number() OVER(ORDER BY object_code)n FROM design_object WHERE object_code LIKE 'DEMO50K-I-%' LIMIT 3000)d
JOIN (SELECT id,row_number() OVER(ORDER BY file_number)n FROM design_file WHERE file_number LIKE 'DEMO50K-F-%')f USING(n)
CROSS JOIN demo_actor a ON CONFLICT(design_object_id,design_file_id,relation_type) DO NOTHING;

-- ---------------------------------------------------------------------
-- 审批：待审、批准、拒绝、退回、撤销全状态；发起时明确选择审批人。
-- ---------------------------------------------------------------------
INSERT INTO approval_request(request_number,request_type,object_type,object_id,object_code,title,status,
 requester_id,requested_at,closed_at,payload,diff_summary)
SELECT 'DEMO50K-APR-'||lpad(g::text,5,'0'),
 (ARRAY['BASIC_DRAWING_NUMBER','DASH_NUMBER','DESIGN_FILE_NUMBER','FILE_REVISION_RELEASE','BASELINE_RELEASE','EXTERNAL_TS_ACCEPT','EXTERNAL_PROJECT_APPROVAL','NUMBER_CANCELLATION','CHANGE_PACKAGE','OBJECT_OBSOLETE'])[(g%10)+1],
 'DESIGN_OBJECT',d.id,d.object_code,'DEMO50K业务审批-'||g,
 (ARRAY['PENDING','APPROVED','REJECTED','RETURNED','CANCELLED'])[(g%5)+1],
 CASE WHEN g%2=0 THEN a.engineer1 ELSE a.engineer2 END,now()-(g%200)*interval '1 day',
 CASE WHEN g%5<>0 THEN now()-(g%199)*interval '1 day' END,
 jsonb_build_object('project_code',(ARRAY['PJT-PSU-A','PJT-IMA-B','PJT-COMPLEX-C'])[(g%3)+1],
                    'selected_approver',CASE WHEN g%2=0 THEN a.approver1 ELSE a.approver2 END),
 jsonb_build_object('before','DRAFT','after','申请目标状态','reason','综合业务数据')
FROM generate_series(1,1000)g
JOIN LATERAL(SELECT id,object_code FROM design_object WHERE object_code='DEMO50K-I-'||lpad(g::text,5,'0'))d ON true
CROSS JOIN demo_actor a ON CONFLICT(request_number) DO NOTHING;

INSERT INTO approval_step(approval_request_id,step_order,step_name,required_role_code,assignee_user_id,is_final,
 decision,decided_by,comments,acted_at)
SELECT ar.id,1,'指定批准人审批','APPROVER',
 CASE WHEN n%2=0 THEN a.approver1 ELSE a.approver2 END,true,
 CASE ar.status WHEN 'APPROVED' THEN 'APPROVED' WHEN 'REJECTED' THEN 'REJECTED'
      WHEN 'RETURNED' THEN 'RETURNED' ELSE 'PENDING' END,
 CASE WHEN ar.status IN('APPROVED','REJECTED','RETURNED') THEN CASE WHEN n%2=0 THEN a.approver1 ELSE a.approver2 END END,
 CASE ar.status WHEN 'APPROVED' THEN '技术与构型检查通过' WHEN 'REJECTED' THEN '证据不充分，拒绝'
      WHEN 'RETURNED' THEN '退回补充项目适用性与验证证据' WHEN 'CANCELLED' THEN '发起人撤销' ELSE NULL END,
 CASE WHEN ar.status IN('APPROVED','REJECTED','RETURNED') THEN ar.requested_at+interval '1 day' END
FROM (SELECT ar.*,row_number() OVER(ORDER BY request_number)n FROM approval_request ar WHERE request_number LIKE 'DEMO50K-APR-%')ar
CROSS JOIN demo_actor a ON CONFLICT(approval_request_id,step_order) DO NOTHING;

-- ---------------------------------------------------------------------
-- 快照、基线、变更、数据质量、导入与审计场景。
-- ---------------------------------------------------------------------
INSERT INTO bom_snapshot(parent_design_object_id,snapshot_number,source_bom_header_id,hash_sha256,line_count,created_by)
SELECT bh.parent_design_object_id,'DEMO50K-SNAP-'||lpad(n::text,5,'0'),bh.id,
 encode(digest('DEMO50K-SNAP-'||n,'sha256'),'hex'),(SELECT count(*) FROM bom_line WHERE bom_header_id=bh.id),a.config_mgr
FROM (SELECT bh.*,row_number() OVER(ORDER BY d.object_code)n FROM bom_header bh JOIN design_object d ON d.id=bh.parent_design_object_id
      WHERE d.object_code LIKE 'DEMO50K-I-%' ORDER BY d.object_code LIMIT 500)bh
CROSS JOIN demo_actor a ON CONFLICT(snapshot_number) DO NOTHING;

INSERT INTO bom_snapshot_line(bom_snapshot_id,item_number,child_design_object_id,child_object_code,child_display_name,
 quantity,unit_code,reference_designator,effectivity,notes,sort_order,applicability_rule_code,applicability_expression)
SELECT s.id,bl.item_number,bl.child_design_object_id,d.object_code,d.display_name,bl.quantity,bl.unit_code,
 bl.reference_designator,bl.effectivity,bl.notes,bl.sort_order,r.rule_code,r.expression
FROM bom_snapshot s JOIN bom_header bh ON bh.id=s.source_bom_header_id JOIN bom_line bl ON bl.bom_header_id=bh.id
JOIN design_object d ON d.id=bl.child_design_object_id LEFT JOIN bom_line_applicability ba ON ba.bom_line_id=bl.id
LEFT JOIN applicability_rule r ON r.id=ba.applicability_rule_id
WHERE s.snapshot_number LIKE 'DEMO50K-SNAP-%' ON CONFLICT(bom_snapshot_id,item_number,child_design_object_id) DO NOTHING;

INSERT INTO design_baseline(part_number_id,baseline_sequence,baseline_code,status,is_current,reason,content_hash,
 validation_result,prepared_by,approved_by,released_at,created_by,baseline_type,scope_note,change_reference,project_code)
SELECT pn.id,1,'BL-DEMO50K-'||lpad(n::text,5,'0'),'DRAFT',false,
 '复杂产品/PSU/IMA项目设计成熟度与发布节点',NULL,
 jsonb_build_object('valid',false,'project_control_checked',true,'explicit_items',true),a.config_mgr,
 NULL,NULL,a.config_mgr,
 (ARRAY['FUNCTIONAL','ALLOCATED','DESIGN','PRODUCT','AS_BUILT'])[(n%5)+1],
 '明确冻结文件版次与BOM快照，项目级外部件准入已核对','CR-DEMO50K-'||lpad(n::text,5,'0'),
 (ARRAY['PJT-PSU-A','PJT-IMA-B','PJT-COMPLEX-C'])[(n%3)+1]
FROM (SELECT pn.id,row_number() OVER(ORDER BY pn.full_part_number)n FROM part_number pn WHERE pn.full_part_number LIKE 'DEMO50K-I-%' LIMIT 500)pn
CROSS JOIN demo_actor a ON CONFLICT(part_number_id,baseline_sequence) DO NOTHING;

INSERT INTO baseline_item(design_baseline_id,item_type,bom_snapshot_id,item_role,sequence,notes)
SELECT b.id,'BOM_SNAPSHOT',s.id,'PRIMARY_DEFINITION',10,'固定复杂产品结构快照'
FROM (SELECT id,row_number() OVER(ORDER BY baseline_code)n FROM design_baseline WHERE baseline_code LIKE 'BL-DEMO50K-%')b
JOIN (SELECT id,row_number() OVER(ORDER BY snapshot_number)n FROM bom_snapshot WHERE snapshot_number LIKE 'DEMO50K-SNAP-%')s USING(n)
ON CONFLICT DO NOTHING;

INSERT INTO baseline_item(design_baseline_id,item_type,file_revision_id,item_role,sequence,notes)
SELECT b.id,'FILE_REVISION',fr.id,'SUPPORTING_DEFINITION',20,'固定已批准文件版次'
FROM (SELECT id,row_number() OVER(ORDER BY baseline_code)n FROM design_baseline WHERE baseline_code LIKE 'BL-DEMO50K-%')b
JOIN (SELECT fr.id,row_number() OVER(ORDER BY f.file_number)n FROM file_revision fr JOIN design_file f ON f.id=fr.design_file_id
      WHERE f.file_number LIKE 'DEMO50K-F-%' AND fr.status='RELEASED' ORDER BY f.file_number LIMIT 500)fr USING(n)
ON CONFLICT DO NOTHING;

-- 明细必须在草稿状态写入；之后经数据库发布校验转为 IN_REVIEW/RELEASED。
UPDATE design_baseline b SET status='IN_REVIEW',validation_result=validation_result||'{"valid":false}'::jsonb
WHERE b.baseline_code LIKE 'BL-DEMO50K-%'
  AND substring(b.baseline_code from '[0-9]+$')::int%5=1;

UPDATE design_baseline b SET status='RELEASED',
 content_hash=encode(digest(b.baseline_code,'sha256'),'hex'),
 validation_result=validation_result||'{"valid":true}'::jsonb,
 approved_by=a.approver2,released_at=now()-(substring(b.baseline_code from '[0-9]+$')::int%180)*interval '1 day'
FROM demo_actor a
WHERE b.baseline_code LIKE 'BL-DEMO50K-%'
  AND substring(b.baseline_code from '[0-9]+$')::int%5>=2;

INSERT INTO change_package(change_number,title,reason,impact_summary,status,created_by)
SELECT 'CR-DEMO50K-'||lpad(g::text,5,'0'),'复杂产品工程更改-'||g,
 'PSU/IMA接口、软件版本、供应商状态或构型适用性变化','已执行Where-Used、项目准入、基线和附件影响分析',
 (ARRAY['DRAFT','IN_REVIEW','APPROVED','IMPLEMENTED','CANCELLED'])[(g%5)+1],a.config_mgr
FROM generate_series(1,500)g CROSS JOIN demo_actor a ON CONFLICT(change_number) DO NOTHING;

INSERT INTO change_package_item(change_package_id,item_type,target_id,target_code,disposition,note)
SELECT c.id,'PART_NUMBER',p.id,p.full_part_number,
 (ARRAY['ADOPT','NOT_AFFECTED','DEFER'])[(c.n%3)+1],'DEMO50K完整影响分析样本'
FROM (SELECT id,row_number() OVER(ORDER BY change_number)n FROM change_package WHERE change_number LIKE 'CR-DEMO50K-%')c
JOIN (SELECT id,full_part_number,row_number() OVER(ORDER BY full_part_number)n FROM part_number WHERE full_part_number LIKE 'DEMO50K-I-%' LIMIT 500)p USING(n)
ON CONFLICT(change_package_id,item_type,target_id) DO NOTHING;

INSERT INTO import_batch(batch_number,import_type,source_filename,source_sha256,status,target_context,total_rows,
 ok_rows,warning_rows,error_rows,created_by,committed_at,committed_by)
SELECT 'IMP-DEMO50K-'||lpad(g::text,4,'0'),
 (ARRAY['BOM','PART_NUMBER','EXTERNAL_PART','CROSS_REFERENCE','ATTRIBUTE'])[(g%5)+1],
 'DEMO50K_批量导入_'||g||'.xlsx',encode(digest('IMP-DEMO50K-'||g,'sha256'),'hex'),
 CASE WHEN g%4=0 THEN 'PREVIEW' WHEN g%4=1 THEN 'ABORTED' ELSE 'COMMITTED' END,
 jsonb_build_object('project',(ARRAY['PJT-PSU-A','PJT-IMA-B','PJT-COMPLEX-C'])[(g%3)+1]),100,90,10,0,a.engineer1,
 CASE WHEN g%4 IN(2,3) THEN now() END,CASE WHEN g%4 IN(2,3) THEN a.config_mgr END
FROM generate_series(1,100)g CROSS JOIN demo_actor a ON CONFLICT(batch_number) DO NOTHING;

INSERT INTO data_quality_issue(rule_code,object_type,object_id,object_code,severity,message,status,
 resolved_at,resolved_by,waiver_reason)
SELECT q.code,'DESIGN_OBJECT',d.id,d.object_code,q.severity,
 CASE q.severity WHEN 'ERROR' THEN '发布阻断：缺少必要定义或项目准入' WHEN 'WARNING' THEN '建议补充供应商证据' ELSE '信息完整度提示' END,
 CASE n%3 WHEN 0 THEN 'OPEN' WHEN 1 THEN 'RESOLVED' ELSE 'WAIVED' END,
 CASE WHEN n%3=1 THEN now() END,CASE WHEN n%3=1 THEN a.config_mgr END,CASE WHEN n%3=2 THEN '演示数据：经风险评估临时豁免' END
FROM (SELECT id,object_code,row_number() OVER(ORDER BY object_code)n FROM design_object WHERE object_code LIKE 'DEMO50K-%' LIMIT 1000)d
JOIN LATERAL(SELECT code,severity FROM data_quality_rule ORDER BY code OFFSET ((d.n-1)%GREATEST((SELECT count(*) FROM data_quality_rule),1)) LIMIT 1)q ON true
CROSS JOIN demo_actor a ON CONFLICT(rule_code,object_type,object_id,status) DO NOTHING;

INSERT INTO audit_log(user_id,username,action,object_type,object_id,object_code,new_value,reason,result)
SELECT CASE WHEN g%2=0 THEN a.engineer1 ELSE a.config_mgr END,
 CASE WHEN g%2=0 THEN 'demo_engineer1' ELSE 'demo_config' END,
 (ARRAY['CREATE','UPDATE','SUBMIT_APPROVAL','APPROVE','SEARCH','BASELINE_RELEASE','BACKUP_VALIDATE'])[(g%7)+1],
 'DESIGN_OBJECT',d.id::text,d.object_code,jsonb_build_object('dataset','DEMO50K','sequence',g),
 '覆盖全部业务场景的可追溯演示操作','SUCCESS'
FROM generate_series(1,2000)g
JOIN LATERAL(SELECT id,object_code FROM design_object WHERE object_code='DEMO50K-I-'||lpad((((g-1)%30000)+1)::text,5,'0'))d ON true
CROSS JOIN demo_actor a;

-- 强校验：少一条都使迁移失败并整体回滚。
DO $$
DECLARE v_total bigint; v_internal bigint; v_external bigint; v_software bigint;
BEGIN
 SELECT count(*),count(*) FILTER(WHERE object_type='INTERNAL_PART'),
        count(*) FILTER(WHERE object_type='EXTERNAL_PART'),count(*) FILTER(WHERE object_type='SOFTWARE')
 INTO v_total,v_internal,v_external,v_software FROM design_object WHERE object_code LIKE 'DEMO50K-%';
 IF (v_total,v_internal,v_external,v_software)<>(50000,30000,15000,5000) THEN
   RAISE EXCEPTION 'DCMS-DEMO50K count mismatch: total=%, internal=%, external=%, software=%',v_total,v_internal,v_external,v_software;
 END IF;
 IF (SELECT count(*) FROM app_user WHERE username LIKE 'demo_%') < 10 THEN
   RAISE EXCEPTION 'DCMS-DEMO50K: 10-account validation failed';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM design_object WHERE display_name LIKE '%PSU%') OR
    NOT EXISTS(SELECT 1 FROM design_object WHERE display_name LIKE '%IMA%') THEN
   RAISE EXCEPTION 'DCMS-DEMO50K: PSU/IMA coverage validation failed';
 END IF;
END $$;

ANALYZE design_object;
ANALYZE part_number;
ANALYZE external_part;
ANALYZE bom_line;
ANALYZE revision_attachment;
