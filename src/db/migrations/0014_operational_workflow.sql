-- UG-DCMS 0013 — 外部件分类、项目级准入、指定审批人与附件检索

-- Early Windows installers could apply this migration without recording it.
-- Keep every operation safe to repeat so upgrades preserve customer data.
CREATE TABLE IF NOT EXISTS external_part_class (
    code text PRIMARY KEY,
    name_cn text NOT NULL UNIQUE,
    definition text NOT NULL,
    sort_order integer NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE'))
);

ALTER TABLE approval_request DROP CONSTRAINT IF EXISTS approval_request_request_type_check;
ALTER TABLE approval_request ADD CONSTRAINT approval_request_request_type_check CHECK
 (request_type IN ('BASIC_DRAWING_NUMBER','DASH_NUMBER','DESIGN_FILE_NUMBER',
 'FILE_REVISION_RELEASE','BASELINE_RELEASE','EXTERNAL_TS_ACCEPT','EXTERNAL_PROJECT_APPROVAL',
 'NUMBER_CANCELLATION','DICTIONARY_CHANGE','CHANGE_PACKAGE','FAMILY_CLOSE','OBJECT_OBSOLETE'));

INSERT INTO external_part_class(code,name_cn,definition,sort_order) VALUES
('E01','标准紧固件','按公开标准制造的螺栓、螺钉、螺母、垫圈、销、铆钉等',1),
('E02','轴承与衬套','滚动轴承、滑动轴承、关节轴承、衬套等',2),
('E03','弹簧与弹性件','压簧、拉簧、扭簧、碟簧及其他弹性元件',3),
('E04','密封件','O形圈、密封圈、垫片、填料和密封组件',4),
('E05','软管与管路件','软管、硬管、接头、三通、弯头及管路附件',5),
('E06','阀与流体控制件','阀门、调压器、过滤器及流体控制元件',6),
('E07','线缆与导线','电线、电缆、同轴线、光缆及预制线缆',7),
('E08','连接器与端接件','连接器、插头、插座、端子、接触件及附件',8),
('E09','电路保护件','断路器、保险丝、浪涌保护器及相关器件',9),
('E10','开关与继电器','机械开关、接近开关、继电器、接触器',10),
('E11','传感器','温度、压力、位置、速度及其他传感元件',11),
('E12','照明与指示件','灯、LED、指示器、显示器及照明附件',12),
('E13','电源与变换器','电源模块、变换器、逆变器、稳压器',13),
('E14','电机与执行器','电机、作动器、电磁铁及驱动组件',14),
('E15','电子元器件','电阻、电容、电感、半导体、集成电路等',15),
('E16','印制板与电子组件','外购PCBA、功能模块和电子组件',16),
('E17','计算与通信设备','处理器、通信模块、路由器、数据设备',17),
('E18','天线与射频件','天线、耦合器、滤波器、衰减器及射频附件',18),
('E19','显示与人机接口件','显示屏、键盘、触控件、操作面板',19),
('E20','风扇与热管理件','风扇、散热器、热管、导热件及温控元件',20),
('E21','泵与压缩机','泵、压缩机、鼓风机及相关组件',21),
('E22','机械传动件','齿轮、带轮、链轮、联轴器、丝杠等',22),
('E23','结构安装件','外购支架、卡箍、导轨、减振器及安装附件',23),
('E24','内饰与舱内件','装饰件、把手、盖板、网罩及舱内用品',24),
('E25','标牌与标识件','铭牌、标签、标识牌及其成品',25),
('E26','包装与防护件','包装箱、保护帽、堵头、防尘罩等',26),
('E27','原材料与型材','板、棒、管、型材、丝材及非金属原材料',27),
('E28','化工材料与辅料','胶黏剂、涂料、润滑剂、清洗剂及工艺辅料',28),
('E29','工具与工装件','采购的专用工具、工装、量具及附件',29),
('E90','客户指定件','由客户或合同明确指定、分类仍按实物属性补充说明的外部件',90),
('E99','待分类','迁移或信息不足时临时使用；项目准入前必须改为正式分类',99)
ON CONFLICT (code) DO NOTHING;

ALTER TABLE external_part ADD COLUMN IF NOT EXISTS external_class_code text;
UPDATE external_part SET external_class_code='E99' WHERE external_class_code IS NULL;
ALTER TABLE external_part ALTER COLUMN external_class_code SET NOT NULL;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'external_part'::regclass
      AND conname = 'fk_external_part_class'
  ) THEN
    ALTER TABLE external_part ADD CONSTRAINT fk_external_part_class
      FOREIGN KEY (external_class_code) REFERENCES external_part_class(code);
  END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_external_part_class ON external_part(external_class_code);

CREATE TABLE IF NOT EXISTS external_part_project_control (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_part_id uuid NOT NULL REFERENCES external_part(id),
    project_code text NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT'
      CHECK (status IN ('DRAFT','IN_REVIEW','APPROVED','REJECTED','SUSPENDED','OBSOLETE')),
    applicability text,
    evaluation_basis text,
    approval_request_id uuid REFERENCES approval_request(id),
    approved_by uuid REFERENCES app_user(id),
    approved_at timestamptz,
    created_by uuid REFERENCES app_user(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_external_project UNIQUE(external_part_id, project_code),
    CONSTRAINT ck_external_project_approved CHECK
      (status <> 'APPROVED' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_external_project_status ON external_part_project_control(project_code,status);
DROP TRIGGER IF EXISTS trg_external_project_touch ON external_part_project_control;
CREATE TRIGGER trg_external_project_touch BEFORE UPDATE ON external_part_project_control
  FOR EACH ROW EXECUTE FUNCTION dcms_touch_updated_at();

ALTER TABLE revision_attachment ADD COLUMN IF NOT EXISTS search_text text;
CREATE INDEX IF NOT EXISTS idx_attachment_filename_trgm
  ON revision_attachment USING gin(filename gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_attachment_search_text_trgm
  ON revision_attachment USING gin(search_text gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_approval_step_assignee_pending
  ON approval_step(assignee_user_id, decision) WHERE decision='PENDING';

ALTER TABLE design_baseline ADD COLUMN IF NOT EXISTS baseline_type text NOT NULL DEFAULT 'DESIGN'
  CHECK (baseline_type IN ('FUNCTIONAL','ALLOCATED','DESIGN','PRODUCT','AS_BUILT'));
ALTER TABLE design_baseline ADD COLUMN IF NOT EXISTS scope_note text;
ALTER TABLE design_baseline ADD COLUMN IF NOT EXISTS change_reference text;
ALTER TABLE design_baseline ADD COLUMN IF NOT EXISTS project_code text NOT NULL DEFAULT 'GENERAL';
CREATE INDEX IF NOT EXISTS idx_baseline_project ON design_baseline(project_code,status);

ALTER TABLE external_technical_state ADD COLUMN IF NOT EXISTS approval_request_id uuid
  REFERENCES approval_request(id);
ALTER TABLE software_version ADD COLUMN IF NOT EXISTS approval_request_id uuid
  REFERENCES approval_request(id);

COMMENT ON TABLE external_part_class IS '长期稳定的外部件物理属性分类；代码不因项目、机型或安装位置改变';
COMMENT ON TABLE external_part_project_control IS '同一外部件在各项目中的独立准入状态，不作为飞机级控制';
