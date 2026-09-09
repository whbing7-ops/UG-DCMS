-- =====================================================================
-- UG-DCMS 迁移 0007 — 初始受控数据装载
-- 基准依据: UG-DCMS-DD-001 §3~§9 / SRS-ACC-005 / UI §14
--
-- 【装载说明 — 验收时须注意】
-- 本迁移中标注 [PLACEHOLDER] 的词典条目为可运行占位值。
-- DD §4/§5 明确规定功能分类词典与命名词典"以已冻结的《功能分类规范》
-- 《基本图号命名规则》装载到系统"。这两份规范未随本轮五份基准文件提供,
-- 故先装载占位词典使系统可运行、可测试; 上线前必须用正式规范替换。
-- 替换方式: DATA_ADMIN 通过数据字典管理界面导入, 或追加 0008 装载迁移。
-- 占位条目的 definition 字段统一以 "[占位]" 开头, 可用下列 SQL 全量列出:
--   SELECT * FROM function_item WHERE definition LIKE '[占位]%';
--   SELECT * FROM naming_core_term WHERE definition LIKE '[占位]%';
-- =====================================================================

-- ---------------------------------------------------------------------
-- 角色 — SRS-ACC-005
-- ---------------------------------------------------------------------
INSERT INTO role (code, name_cn, name_en, description, sort_order) VALUES
('SYSTEM_ADMIN',          '系统管理员',   'System Administrator',
 '用户、角色、在线会话、系统设置、并发许可管理 (UI §14)', 10),
('DATA_ADMIN',            '数据管理员',   'Data Administrator',
 '维护受控字典: 分类、功能、核心词、限定词、属性、材料、表面处理、文件类型 (UI §14)', 20),
('CONFIGURATION_MANAGER', '构型管理员',   'Configuration Manager',
 '发号、基线发布、快照生成、变更包管理、数据质量处置', 30),
('ENGINEER',              '设计工程师',   'Engineer',
 '创建与编辑草稿对象、BOM、设计文件版次; 不可修改受控字典 (AC-SEC-02)', 40),
('APPROVER',              '批准人',       'Approver',
 '审批申请; 不得批准自己发起的申请 (INV-025)', 50),
('VIEWER',                '查阅人',       'Viewer',
 '只读; 不可修改草稿/BOM/文件 (AC-SEC-01)', 60);

-- ---------------------------------------------------------------------
-- 系统设置
-- ---------------------------------------------------------------------
INSERT INTO system_setting (key, value, value_type, description, is_locked) VALUES
('max_concurrent_accounts', '10', 'INTEGER',
 '同时活动账户上限。INV-024 / SRS-ACC-002 冻结值, 修改属基准变更', true),
('session_idle_minutes',    '120', 'INTEGER', '会话空闲超时(分钟)', false),
('session_absolute_hours',  '12',  'INTEGER', '会话绝对有效期(小时)', false),
('login_max_failures',      '5',   'INTEGER', '连续登录失败锁定阈值', false),
('login_lock_minutes',      '15',  'INTEGER', '登录锁定时长(分钟)', false),
('dash_number_min',         '1',   'INTEGER', 'Dash 下限。INV-003 冻结值', true),
('dash_number_max',         '999', 'INTEGER', 'Dash 上限。SRS-PN-001 冻结值', true),
('baseline_code_prefix',    'BL-', 'STRING',  '基线编号前缀。DD §9 BaselineCode', false),
('snapshot_code_prefix',    'SNAP-', 'STRING', 'BOM 快照编号前缀', false),
('storage_root',            '/data/dcms/files', 'STRING',
 '文件存储根。系统只用 StorageKey 抽象寻址, 不保存人工路径 (DD §9)', false);

-- ---------------------------------------------------------------------
-- 初始管理员 — 首次登录强制改密
-- 初始口令: Admin@12345   (pgcrypto bcrypt, cost 12)
-- ---------------------------------------------------------------------
INSERT INTO app_user (username, full_name, email, password_hash, must_change_password)
VALUES ('admin', '系统管理员', NULL,
        crypt('Admin@12345', gen_salt('bf', 12)), true);

INSERT INTO user_role (user_id, role_code)
SELECT u.id, r.code FROM app_user u CROSS JOIN role r
WHERE u.username = 'admin' AND r.code IN ('SYSTEM_ADMIN', 'DATA_ADMIN');

-- ---------------------------------------------------------------------
-- 一级技术类别 — DD §4 (固化, 与 UG-DGS-000 件号架构一致)
-- ---------------------------------------------------------------------
INSERT INTO primary_class (code, name_cn, name_en, definition, sort_order) VALUES
('T1', '机械/结构/非电气', 'Mechanical / Structural / Non-Electrical',
 '机械结构类无源件', 10),
('T2', '电气/电子',       'Electrical / Electronic',
 '电气电子类设计对象', 20),
('T3', '导线/电缆/互连',   'Wire / Cable / Interconnect',
 '线束与互连类设计对象', 30),
('SW', '软件/非图纸类',    'Software / Non-Drawing',
 '软件、固件、可加载软件等非图纸类设计对象; 仅用于命名词典适用范围标注', 90);

-- ---------------------------------------------------------------------
-- 对象层级 — DD §4
-- ---------------------------------------------------------------------
INSERT INTO object_level (code, name_cn, name_en, sort_order) VALUES
('PART',      '零件', 'Part',      10),
('ASSEMBLY',  '组件', 'Assembly',  20),
('MODULE',    '模块', 'Module',    30),
('EQUIPMENT', '设备', 'Equipment', 40),
('END_ITEM',  '成品', 'End Item',  50);

-- ---------------------------------------------------------------------
-- 二级物理分类 — [PLACEHOLDER] 受控字典, 上线前按公司分类规范替换
-- ---------------------------------------------------------------------
INSERT INTO physical_class (code, primary_class_code, name_cn, name_en, definition, sort_order) VALUES
('T1-BRACKET',   'T1', '支架/托架类',   'Bracket',            '[占位] 承载与固定用结构件', 10),
('T1-CLAMP',     'T1', '卡箍/抱箍类',   'Clamp',              '[占位] 夹持固定类结构件',   20),
('T1-PANEL',     'T1', '面板/盖板类',   'Panel / Cover',      '[占位] 板类结构件',         30),
('T1-FASTENER',  'T1', '紧固件类',      'Fastener',           '[占位] 螺纹与铆接紧固件',   40),
('T1-SEAL',      'T1', '密封件类',      'Seal',               '[占位] 密封与减振件',       50),
('T1-HOUSING',   'T1', '壳体/外罩类',   'Housing',            '[占位] 包覆与容纳类结构件', 60),
('T2-LIGHT',     'T2', '灯具/照明类',   'Light Assembly',     '[占位] 照明设备',           110),
('T2-PCB',       'T2', '印制板组件类',  'PCB Assembly',        '[占位] 印制电路板组件',     120),
('T2-CONNECTOR', 'T2', '连接器类',      'Connector',          '[占位] 电连接器',           130),
('T2-SWITCH',    'T2', '开关/控制器类', 'Switch / Controller', '[占位] 开关与控制装置',     140),
('T2-POWER',     'T2', '电源/配电类',   'Power / Distribution','[占位] 电源与配电装置',     150),
('T2-SPEAKER',   'T2', '声学器件类',    'Acoustic Device',     '[占位] 扬声器等声学器件',   160),
('T3-HARNESS',   'T3', '线束组件类',    'Wire Harness',        '[占位] 成套线束',           210),
('T3-CABLE',     'T3', '电缆/导线类',   'Cable / Wire',        '[占位] 导线与电缆',         220),
('T3-TERMINAL',  'T3', '端接件类',      'Terminal',           '[占位] 端子与端接附件',     230);

-- ---------------------------------------------------------------------
-- 功能分类 F01~F16 — [PLACEHOLDER] DD §4: 以已冻结《功能分类规范》装载
-- ---------------------------------------------------------------------
INSERT INTO function_domain (code, name_cn, name_en, definition, sort_order) VALUES
('F01', '结构承载',   'Structural Load Carrying', '[占位] 承受并传递载荷', 10),
('F02', '固定连接',   'Fixing / Attachment',      '[占位] 固定与连接',     20),
('F03', '包覆防护',   'Enclosure / Protection',   '[占位] 包覆与防护',     30),
('F04', '密封',       'Sealing',                  '[占位] 阻隔介质泄漏',   40),
('F05', '减振缓冲',   'Vibration Damping',        '[占位] 减振与缓冲',     50),
('F06', '照明',       'Illumination',             '[占位] 产生与投射光',   60),
('F07', '指示显示',   'Indication / Display',     '[占位] 状态指示与显示', 70),
('F08', '电能传输',   'Electrical Power Transfer','[占位] 传输电能',       80),
('F09', '信号传输',   'Signal Transmission',      '[占位] 传输信号',       90),
('F10', '电气控制',   'Electrical Control',       '[占位] 控制与切换',     100),
('F11', '电能转换',   'Power Conversion',         '[占位] 电能形式转换',   110),
('F12', '声学',       'Acoustic',                 '[占位] 声音发生与传播', 120),
('F13', '热管理',     'Thermal Management',       '[占位] 传热与散热',     130),
('F14', '机械运动',   'Mechanical Motion',        '[占位] 传递与约束运动', 140),
('F15', '标识信息',   'Marking / Information',    '[占位] 承载标识信息',   150),
('F16', '接口适配',   'Interface Adaptation',     '[占位] 接口转换与适配', 160);

INSERT INTO function_item (code, domain_code, name_cn, name_en, definition, sort_order)
SELECT d.code || '-01', d.code, d.name_cn, d.name_en,
       '[占位] ' || d.name_cn || ' 一般功能; 待按《功能分类规范》细化二级功能',
       d.sort_order
FROM function_domain d;

-- ---------------------------------------------------------------------
-- 命名词典 — [PLACEHOLDER] DD §5: 以已冻结《基本图号命名规则》装载
-- INV-023: 基本图号核心实体词必须来自本表
-- ---------------------------------------------------------------------
INSERT INTO naming_core_term (code, name_cn, name_en, primary_class_code, definition) VALUES
('T1-010', '抱箍',   'Clamp',       'T1', '[占位] 环抱并夹紧被固定件的结构件'),
('T1-020', '支架',   'Bracket',     'T1', '[占位] 为被支撑件提供安装基面的结构件'),
('T1-030', '盖板',   'Cover Plate', 'T1', '[占位] 覆盖开口的板类件'),
('T1-040', '壳体',   'Housing',     'T1', '[占位] 容纳并保护内部组件的结构件'),
('T1-050', '衬套',   'Bushing',     'T1', '[占位] 置于配合面间的环状件'),
('T1-060', '垫片',   'Shim',        'T1', '[占位] 用于调整间隙或密封的薄片件'),
('T1-070', '反光镜', 'Mirror',      'T1', '[占位] 反射成像器件'),
('T2-010', '灯具',   'Light',       'T2', '[占位] 照明装置总成'),
('T2-020', '扬声器', 'Speaker',     'T2', '[占位] 电声转换器件'),
('T2-030', '插座',   'Outlet',      'T2', '[占位] 提供电气接入的器件'),
('T2-040', '控制器', 'Controller',  'T2', '[占位] 实现控制逻辑的装置'),
('T2-050', '印制板', 'Circuit Card','T2', '[占位] 印制电路板及其组件'),
('T2-060', '开关',   'Switch',      'T2', '[占位] 通断电路的器件'),
('T3-010', '线束',   'Harness',     'T3', '[占位] 成套导线与端接件组合'),
('T3-020', '导线',   'Wire',        'T3', '[占位] 单根绝缘导体'),
('T3-030', '电缆',   'Cable',       'T3', '[占位] 多导体成缆制品'),
('SW-010', '软件',   'Software',    'SW', '[占位] 非图纸类可执行设计对象'),
('SW-020', '固件',   'Firmware',    'SW', '[占位] 驻留于硬件的可加载程序');

INSERT INTO naming_qualifier (code, name_cn, name_en, qualifier_type, definition) VALUES
('Q-G010', 'P形',   'P-Type',      'GEOMETRY',  '[占位] 截面呈 P 形'),
('Q-G020', 'U形',   'U-Type',      'GEOMETRY',  '[占位] 截面呈 U 形'),
('Q-G030', 'L形',   'L-Type',      'GEOMETRY',  '[占位] 截面呈 L 形'),
('Q-G040', '环形',  'Annular',     'GEOMETRY',  '[占位] 呈环状'),
('Q-S010', '整体',  'Integral',    'STRUCTURE', '[占位] 单体成型结构'),
('Q-S020', '分体',  'Split',       'STRUCTURE', '[占位] 分为两件及以上的结构'),
('Q-S030', '可调',  'Adjustable',  'STRUCTURE', '[占位] 具备可调节结构'),
('Q-F010', '阅读',  'Reading',     'FUNCTION',  '[占位] 用于阅读照明'),
('Q-F020', '转弯',  'Turn',        'FUNCTION',  '[占位] 用于转弯照明'),
('Q-F030', '滑行',  'Taxi',        'FUNCTION',  '[占位] 用于滑行照明'),
('Q-P010', 'LED',   'LED',         'PRINCIPLE', '[占位] 发光二极管原理'),
('Q-P020', '电控',  'Electrically Controlled', 'PRINCIPLE', '[占位] 以电信号控制'),
('Q-I010', '快卸',  'Quick Release', 'INTERFACE','[占位] 快速拆装接口'),
('Q-I020', '面板式','Panel Mount',  'INTERFACE', '[占位] 面板安装接口');

-- ---------------------------------------------------------------------
-- 受限词 — DD §5, SRS-NAM-003, AC-NAM-01
-- BLOCK = 不能提交; WARN_APPROVAL = 需批准; WARN = 仅提示
-- ---------------------------------------------------------------------
INSERT INTO restricted_term (restricted_type, control_level, pattern, is_regex, example, message_cn) VALUES
('PROJECT',   'BLOCK', '\m(STC|PMA|TSO|CTSOA|TC|MDA|DOA)\M', true, 'STC',
 '项目/审定类词汇禁止进入基本图号名称 (SRS-NAM-003)'),
('PROJECT',   'BLOCK', 'UG-(STC|PMA|CTSOA)[0-9A-Z]*', true, 'UG-STC07A25',
 '项目编号禁止进入基本图号名称'),
('REVISION',  'BLOCK', '\m(REV|Rev|R[0-9]{2}|V[0-9]+)\M', true, 'Rev.01',
 'Revision 标识禁止进入基本图号名称 (三层身份不得混用)'),
('REVISION',  'BLOCK', '(二代|三代|新版|旧版)', true, '二代',
 '版本代际词禁止进入基本图号名称'),
('MARKETING', 'BLOCK', '(新型|增强型|升级版|改进型|高性能|经济型)', true, '增强型',
 '宣传性词汇禁止进入基本图号名称'),
('CUSTOMER',  'BLOCK', '(航空|航空公司|Airlines|Airways)', true, '南方航空',
 '客户名称禁止进入基本图号名称'),
('AIRCRAFT',      'WARN_APPROVAL', '\m(A3[0-9]{2}|B7[0-9]{2}|H1[0-9]{2}|AS3[0-9]{2}|EC[0-9]{3}|ARJ21|C919)\M', true, 'A320',
 '机型词进入名称须经批准 (DD §5 WARN_APPROVAL)'),
('LOCATION',  'WARN', '(左|右|前|后|上|下|内|外|底板|顶板)', true, '左',
 '位置词一般不应进入基本图号名称, 请确认必要性'),
('NHA',       'WARN', '\m(PSU|DCPM|OU|PSCD)\M', true, 'PSU',
 '上层对象名进入名称会绑定装机位置, 请确认必要性'),
('MATERIAL',  'WARN', '(7075|6061|2024|304|316|不锈钢|铝合金|钛合金|碳纤维)', true, '7075',
 '材料信息应放入属性而非名称'),
('COLOR',     'WARN', '(黑色|白色|红色|蓝色|绿色|银色|灰色)', true, '黑色',
 '颜色信息应放入属性或表面处理而非名称'),
('PARAMETER', 'WARN', '(Ø|φ)[0-9]', true, 'Ø25',
 '具体尺寸参数应放入属性而非名称'),
('PARAMETER', 'WARN', '[0-9]+(\.[0-9]+)?\s*(mm|cm|m|W|V|A|Hz|kg|g)\M', true, '50W',
 '具体参数值应放入属性而非名称');

-- ---------------------------------------------------------------------
-- 单位 — DD §6
-- ---------------------------------------------------------------------
INSERT INTO unit_dimension (code, name_cn, name_en) VALUES
('LENGTH', '长度', 'Length'), ('MASS', '质量', 'Mass'),
('ANGLE', '角度', 'Angle'), ('VOLTAGE', '电压', 'Voltage'),
('CURRENT', '电流', 'Current'), ('POWER', '功率', 'Power'),
('FREQUENCY', '频率', 'Frequency'), ('RESISTANCE', '电阻', 'Resistance'),
('TEMPERATURE', '温度', 'Temperature'), ('COUNT', '计数', 'Count'),
('AREA', '面积', 'Area'), ('FORCE', '力', 'Force'),
('TORQUE', '扭矩', 'Torque'), ('PRESSURE', '压力', 'Pressure');

INSERT INTO unit (code, dimension_code, symbol, name_cn, factor_to_base) VALUES
('mm', 'LENGTH', 'mm', '毫米', 1), ('cm', 'LENGTH', 'cm', '厘米', 10),
('m', 'LENGTH', 'm', '米', 1000), ('in', 'LENGTH', 'in', '英寸', 25.4),
('g', 'MASS', 'g', '克', 1), ('kg', 'MASS', 'kg', '千克', 1000),
('deg', 'ANGLE', 'deg', '度', 1), ('rad', 'ANGLE', 'rad', '弧度', 57.295779513),
('V', 'VOLTAGE', 'V', '伏', 1), ('mV', 'VOLTAGE', 'mV', '毫伏', 0.001),
('A', 'CURRENT', 'A', '安', 1), ('mA', 'CURRENT', 'mA', '毫安', 0.001),
('W', 'POWER', 'W', '瓦', 1), ('kW', 'POWER', 'kW', '千瓦', 1000),
('Hz', 'FREQUENCY', 'Hz', '赫兹', 1), ('kHz', 'FREQUENCY', 'kHz', '千赫', 1000),
('ohm', 'RESISTANCE', 'Ω', '欧姆', 1),
('degC', 'TEMPERATURE', '°C', '摄氏度', 1),
('EA', 'COUNT', 'EA', '个', 1), ('SET', 'COUNT', 'SET', '套', 1),
('mm2', 'AREA', 'mm²', '平方毫米', 1),
('N', 'FORCE', 'N', '牛', 1), ('Nm', 'TORQUE', 'N·m', '牛米', 1),
('kPa', 'PRESSURE', 'kPa', '千帕', 1);

UPDATE unit_dimension SET default_unit_code = v.u
FROM (VALUES ('LENGTH','mm'), ('MASS','g'), ('ANGLE','deg'), ('VOLTAGE','V'),
             ('CURRENT','A'), ('POWER','W'), ('FREQUENCY','Hz'), ('RESISTANCE','ohm'),
             ('TEMPERATURE','degC'), ('COUNT','EA'), ('AREA','mm2'),
             ('FORCE','N'), ('TORQUE','Nm'), ('PRESSURE','kPa')) AS v(d, u)
WHERE unit_dimension.code = v.d;

-- ---------------------------------------------------------------------
-- 材料与表面处理 — [PLACEHOLDER] 受控主数据 (SRS-ATT-003)
-- ---------------------------------------------------------------------
INSERT INTO material (code, material_family, grade, specification, condition, name_cn) VALUES
('AL-7075-T6',  'ALUMINIUM', '7075', 'AMS-QQ-A-250/12', 'T6',   '7075-T6 铝合金板'),
('AL-6061-T6',  'ALUMINIUM', '6061', 'AMS-QQ-A-250/11', 'T6',   '6061-T6 铝合金板'),
('AL-2024-T3',  'ALUMINIUM', '2024', 'AMS-QQ-A-250/4',  'T3',   '2024-T3 铝合金板'),
('SS-304',      'STEEL',     '304',  'ASTM A240',       'ANNEALED', '304 不锈钢'),
('SS-316L',     'STEEL',     '316L', 'ASTM A240',       'ANNEALED', '316L 不锈钢'),
('TI-6AL4V',    'TITANIUM',  'Ti-6Al-4V', 'AMS 4911',   'ANNEALED', 'TC4 钛合金'),
('PA66-GF30',   'POLYMER',   'PA66-GF30', NULL,         NULL,   'PA66 玻纤增强尼龙'),
('PC-FR',       'POLYMER',   'PC',   'UL94 V-0',        NULL,   '阻燃聚碳酸酯'),
('SIL-RUBBER',  'ELASTOMER', 'VMQ',  'AMS 3302',        NULL,   '硅橡胶');

INSERT INTO surface_treatment (code, process_type, specification, type_class, color, name_cn) VALUES
('ANO-II-BLK',  'ANODIZE',   'MIL-A-8625',  'Type II Class 2', '黑色', 'II型硫酸阳极化(黑)'),
('ANO-II-CLR',  'ANODIZE',   'MIL-A-8625',  'Type II Class 1', '本色', 'II型硫酸阳极化(本色)'),
('CHEM-CONV',   'CONVERSION','MIL-DTL-5541','Type I Class 3',  '本色', '铬酸盐化学转化膜'),
('PRIME-EPOXY', 'PRIMER',    'MIL-PRF-23377', 'Type I Class C','绿色', '环氧底漆'),
('PAINT-PU-WHT','TOPCOAT',   'MIL-PRF-85285', 'Type I',        '白色', '聚氨酯面漆(白)'),
('PASSIVATE',   'PASSIVATION','AMS 2700',    'Method 1',       '本色', '不锈钢钝化'),
('NONE',        'NONE',      NULL,           NULL,             NULL,   '无表面处理');

-- ---------------------------------------------------------------------
-- 属性枚举与定义 — SRS-ATT-001
-- ---------------------------------------------------------------------
INSERT INTO attribute_enum_group (code, name_cn) VALUES
('FLAMMABILITY', '阻燃等级'), ('IP_RATING', '防护等级'), ('MOUNT_TYPE', '安装方式');

INSERT INTO attribute_enum_value (enum_group_code, value_code, name_cn, sort_order) VALUES
('FLAMMABILITY', 'UL94-V0', 'UL94 V-0', 10),
('FLAMMABILITY', 'UL94-V1', 'UL94 V-1', 20),
('FLAMMABILITY', 'FAR25-853', 'FAR 25.853 附录F', 30),
('IP_RATING', 'IP20', 'IP20', 10), ('IP_RATING', 'IP54', 'IP54', 20),
('IP_RATING', 'IP65', 'IP65', 30), ('IP_RATING', 'IP67', 'IP67', 40),
('MOUNT_TYPE', 'PANEL', '面板安装', 10), ('MOUNT_TYPE', 'BRACKET', '支架安装', 20),
('MOUNT_TYPE', 'CLAMP', '卡箍安装', 30), ('MOUNT_TYPE', 'ADHESIVE', '粘接安装', 40);

INSERT INTO attribute_definition
    (code, name_cn, name_en, data_type, unit_dimension_code, default_unit_code,
     enum_group_code, reference_target, is_searchable, is_comparable, definition) VALUES
('LENGTH_OVERALL', '总长',     'Overall Length',   'NUMBER', 'LENGTH', 'mm', NULL, NULL, true,  true,  '对象最大外形长度'),
('WIDTH_OVERALL',  '总宽',     'Overall Width',    'NUMBER', 'LENGTH', 'mm', NULL, NULL, true,  true,  '对象最大外形宽度'),
('HEIGHT_OVERALL', '总高',     'Overall Height',   'NUMBER', 'LENGTH', 'mm', NULL, NULL, true,  true,  '对象最大外形高度'),
('THICKNESS',      '厚度',     'Thickness',        'NUMBER', 'LENGTH', 'mm', NULL, NULL, true,  true,  '板厚或壁厚'),
('BORE_DIAMETER',  '内径',     'Bore Diameter',    'NUMBER', 'LENGTH', 'mm', NULL, NULL, true,  true,  '内孔或夹持直径'),
('MASS_NOMINAL',   '标称质量', 'Nominal Mass',     'NUMBER', 'MASS',   'g',  NULL, NULL, true,  true,  '单件标称质量'),
('MATERIAL_REF',   '材料',     'Material',         'REFERENCE', NULL,  NULL, NULL, 'MATERIAL', true, true, '引用材料主数据'),
('SURFACE_REF',    '表面处理', 'Surface Treatment','REFERENCE', NULL,  NULL, NULL, 'SURFACE_TREATMENT', true, true, '引用表面处理主数据'),
('VOLTAGE_NOMINAL','标称电压', 'Nominal Voltage',  'NUMBER', 'VOLTAGE','V',  NULL, NULL, true,  true,  '额定工作电压'),
('POWER_NOMINAL',  '标称功率', 'Nominal Power',    'NUMBER', 'POWER',  'W',  NULL, NULL, true,  true,  '额定功率'),
('CURRENT_MAX',    '最大电流', 'Maximum Current',  'NUMBER', 'CURRENT','A',  NULL, NULL, true,  true,  '最大工作电流'),
('FLAMMABILITY',   '阻燃等级', 'Flammability',     'ENUM',   NULL,     NULL, 'FLAMMABILITY', NULL, true, true, '阻燃性能等级'),
('IP_RATING',      '防护等级', 'IP Rating',        'ENUM',   NULL,     NULL, 'IP_RATING',    NULL, true, true, '外壳防护等级'),
('MOUNT_TYPE',     '安装方式', 'Mounting Type',    'ENUM',   NULL,     NULL, 'MOUNT_TYPE',   NULL, true, true, '安装接口形式'),
('WIRE_GAUGE',     '线规',     'Wire Gauge',       'TEXT',   NULL,     NULL, NULL, NULL, true,  true,  'AWG 或截面积标注'),
('CONDUCTOR_COUNT','芯数',     'Conductor Count',  'INTEGER',NULL,     NULL, NULL, NULL, true,  true,  '导体数量');

-- 属性模板: 由二级物理分类驱动 (SRS-ATT-002)
INSERT INTO attribute_template (code, name_cn, physical_class_id)
SELECT 'TPL-' || pc.code, pc.name_cn || ' 属性模板', pc.id FROM physical_class pc;

-- 通用几何/材料属性挂到全部模板
INSERT INTO attribute_template_item (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id,
       ad.code IN ('MATERIAL_REF'),
       ad.code IN ('LENGTH_OVERALL', 'MATERIAL_REF'),
       CASE ad.code WHEN 'LENGTH_OVERALL' THEN 10 WHEN 'WIDTH_OVERALL' THEN 20
                    WHEN 'HEIGHT_OVERALL' THEN 30 WHEN 'MASS_NOMINAL' THEN 40
                    WHEN 'MATERIAL_REF' THEN 50 ELSE 60 END
FROM attribute_template t
JOIN attribute_template at2 ON at2.id = t.id
JOIN physical_class pc ON pc.id = t.physical_class_id
JOIN attribute_definition ad ON ad.code IN
     ('LENGTH_OVERALL', 'WIDTH_OVERALL', 'HEIGHT_OVERALL', 'MASS_NOMINAL',
      'MATERIAL_REF', 'SURFACE_REF')
WHERE pc.primary_class_code IN ('T1', 'T2', 'T3');

-- 电气属性只挂 T2 模板
INSERT INTO attribute_template_item (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id, false, ad.code = 'VOLTAGE_NOMINAL', 110
FROM attribute_template t
JOIN physical_class pc ON pc.id = t.physical_class_id AND pc.primary_class_code = 'T2'
JOIN attribute_definition ad ON ad.code IN
     ('VOLTAGE_NOMINAL', 'POWER_NOMINAL', 'CURRENT_MAX', 'IP_RATING', 'FLAMMABILITY');

-- 线缆属性只挂 T3 模板
INSERT INTO attribute_template_item (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id, false, ad.code = 'WIRE_GAUGE', 120
FROM attribute_template t
JOIN physical_class pc ON pc.id = t.physical_class_id AND pc.primary_class_code = 'T3'
JOIN attribute_definition ad ON ad.code IN ('WIRE_GAUGE', 'CONDUCTOR_COUNT');

-- ---------------------------------------------------------------------
-- 文件类型 — DD §7
-- ---------------------------------------------------------------------
INSERT INTO file_type (code, name_cn, name_en, category, definition) VALUES
('DWG',   '零件图/装配图', 'Drawing',                 'DESIGN',        '定义几何与技术要求的图纸'),
('PSCD',  '产品规范与构型定义', 'Product Spec & Configuration Definition', 'DESIGN', '产品级规范与构型定义文件'),
('SPEC',  '技术规范',     'Technical Specification',  'DESIGN',        '性能与技术要求规范'),
('WD',    '电气原理图',   'Wiring Diagram',           'DESIGN',        '电气原理与接线定义'),
('BOMDOC','BOM 文件',     'BOM Document',             'DESIGN',        '成套明细表文件表达'),
('ICD',   '接口控制文件', 'Interface Control Document','INTERFACE',    '接口定义文件'),
('QTP',   '试验大纲',     'Qualification Test Plan',  'QUALIFICATION', '鉴定试验大纲'),
('QTR',   '试验报告',     'Qualification Test Report','QUALIFICATION', '鉴定试验报告'),
('ANLS',  '分析报告',     'Analysis Report',          'QUALIFICATION', '强度/热/EMC 等分析报告'),
('SWRD',  '软件版本说明', 'Software Release Document','DESIGN',        '软件版本发布说明'),
('REF',   '参考资料',     'Reference Document',       'REFERENCE',     '仅参考, 不构成设计定义');

-- ---------------------------------------------------------------------
-- 命名空间与制造商 — INV-016, AT-005
-- ---------------------------------------------------------------------
INSERT INTO namespace (code, name_cn, name_en, kind) VALUES
('MOLEX',    'Molex',        'Molex',            'MANUFACTURER'),
('TE',       'TE Connectivity','TE Connectivity','MANUFACTURER'),
('AMPHENOL', 'Amphenol',     'Amphenol',         'MANUFACTURER'),
('DEUTSCH',  'Deutsch',      'Deutsch',          'MANUFACTURER'),
('OTHER',    '其它来源',     'Other',            'OTHER'),
('MS',       '军用标准件',   'Military Standard','STANDARD'),
('NAS',      '航空标准件',   'National Aerospace Standard', 'STANDARD'),
('AN',       'AN 标准件',    'AN Standard',      'STANDARD'),
('GB',       '国家标准件',   'GB Standard',      'STANDARD'),
('HB',       '航空行业标准件','HB Standard',     'STANDARD'),
('AIRBUS',   '空客',         'Airbus',           'OEM'),
('BOEING',   '波音',         'Boeing',           'OEM'),
('AIRBUS-H', '空客直升机',   'Airbus Helicopters','OEM'),
('UG-LEGACY','公司历史编号', 'UG Legacy',        'INTERNAL_LEGACY');

INSERT INTO manufacturer (code, name_cn, name_en, namespace_id)
SELECT n.code, n.name_cn, n.name_en, n.id FROM namespace n WHERE n.kind = 'MANUFACTURER';

-- ---------------------------------------------------------------------
-- 数据质量规则 — SRS-DQ-001 (ERROR 级阻止发布)
-- ---------------------------------------------------------------------
INSERT INTO data_quality_rule (code, name_cn, severity, object_type, description, blocks_release) VALUES
('DQ-PN-001', 'P/N 缺少主设计定义',       'ERROR',   'PART_NUMBER',
 'Released P/N 必须存在 PRIMARY_DEFINITION 设计定义关系 (INV-022)', true),
('DQ-PN-002', 'P/N 缺少 Current Baseline','ERROR',   'PART_NUMBER',
 'Released P/N 必须有且仅有一个 Current Baseline (INV-013)', true),
('DQ-PN-003', 'P/N 缺少关键属性',         'WARNING', 'PART_NUMBER',
 '属性模板中标记为关键属性的项未填写', false),
('DQ-PN-004', 'P/N 缺少主功能',           'WARNING', 'PART_NUMBER',
 '未指定 PRIMARY 功能分类', false),
('DQ-FAM-001','设计族边界未定义',         'WARNING', 'BASIC_DRAWING_FAMILY',
 'Allowed Variation 或 Excluded Variation 为空', false),
('DQ-FAM-002','设计族名称含受限词',       'ERROR',   'BASIC_DRAWING_FAMILY',
 '基本图号名称命中 BLOCK 级受限词 (AC-NAM-01)', true),
('DQ-BOM-001','BOM 子项重复出现',         'WARNING', 'BOM_HEADER',
 '同一子项在同一 BOM 中重复出现 (SRS-BOM-005: 允许但默认警告)', false),
('DQ-BOM-002','BOM 子项为草稿状态',       'WARNING', 'BOM_HEADER',
 '子项 lifecycle_status 仍为 DRAFT', false),
('DQ-BOM-003','BOM 存在循环',             'ERROR',   'BOM_HEADER',
 'BOM 形成任意层级循环 (INV-009)', true),
('DQ-EXT-001','外部件无 ACCEPTED 技术状态','ERROR',  'EXTERNAL_PART',
 '进入正式基线的外部件必须有 ACCEPTED 技术状态 (INV-017)', true),
('DQ-EXT-002','外部件缺少制造商',         'WARNING', 'EXTERNAL_PART',
 '未指定 manufacturer', false),
('DQ-FILE-001','已发布附件完整性异常',    'ERROR',   'REVISION_ATTACHMENT',
 'SHA-256 巡检结果为 MISMATCH 或 MISSING (AC-DATA-05 / AT-007)', true),
('DQ-FILE-002','已发布版次缺少 Released PDF','WARNING','FILE_REVISION',
 'RELEASED 版次未附 RELEASED_PDF 表达', false),
('DQ-NUM-001','编号存在空缺',             'INFO',    'NUMBER_ALLOCATION',
 '族内 Dash 序列存在未占用空缺 (SRS-NUM-004)', false);

-- ---------------------------------------------------------------------
-- 装载自检
-- ---------------------------------------------------------------------
DO $$
DECLARE v_placeholder integer;
BEGIN
    SELECT (SELECT count(*) FROM function_item      WHERE definition LIKE '[占位]%')
         + (SELECT count(*) FROM naming_core_term   WHERE definition LIKE '[占位]%')
         + (SELECT count(*) FROM naming_qualifier   WHERE definition LIKE '[占位]%')
         + (SELECT count(*) FROM physical_class     WHERE definition LIKE '[占位]%')
      INTO v_placeholder;
    RAISE NOTICE 'UG-DCMS 初始字典装载完成; 其中占位词条 % 条, 上线前须以正式规范替换', v_placeholder;
END $$;
