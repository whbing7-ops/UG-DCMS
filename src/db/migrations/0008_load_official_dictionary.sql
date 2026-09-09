-- =====================================================================
-- UG-DCMS 迁移 0008 — 装载正式受控词典, 替换 0007 的占位词条
--
-- 装载依据:
--   UG-EDS-CLS-001《设计对象二级分类规范》R00        附录A / §6~§9 / §12
--   UG-EDS-NAM-001《基本图号命名规则》R00.01         §5 / §6 / §7 / §9
--   两者上位依据 UG-DGS-000《设计规范总则》Rev.00
--
-- 本迁移做四件事:
--   1. 清除 0007 装载的 [占位] 词条 (仅在系统尚无正式设计数据时允许)
--   2. 装载 60 条二级物理分类 + 148 条核心工程实体名称 + 60 条稳定限定词
--   3. 按 NAM §7 重建受限词词典
--   4. 增加两份规范新引入的强制规则 (见 §4 说明)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1 前置保护: 已有正式设计数据时不得直接替换分类/命名词典
--   CLS-001 §13: 已存在历史对象不要求因分类字典调整而自动批量改类
-- ---------------------------------------------------------------------
DO $$
DECLARE v_fam integer; v_pn integer; v_loaded integer;
BEGIN
    -- 已装载过则明确报出, 否则会一路撞到"重复键"这种看不出原因的错误。
    SELECT count(*) INTO v_loaded FROM physical_class WHERE definition NOT LIKE '[占位]%';
    IF v_loaded >= 60 THEN
        RAISE EXCEPTION 'DCMS-0008: 正式词典已装载(二级分类 % 条), 本迁移无需重复执行', v_loaded
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_fam FROM basic_drawing_family;
    SELECT count(*) INTO v_pn  FROM part_number;
    IF v_fam > 0 OR v_pn > 0 THEN
        RAISE EXCEPTION
            'DCMS-0008: 系统已有 % 个设计族、% 个 P/N。占位词典替换只允许在空系统上执行。'
            ' 已有数据的词典迁移须按 CLS-001 §13 单独评估, 编写带映射关系的迁移脚本。',
            v_fam, v_pn USING ERRCODE = '23514';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2 清除占位词条
--   字典表设有禁止物理删除触发器(DD §1)。此处是受控的初始装载替换,
--   在同一事务内临时停用并立即恢复; 生产运行期不得以此方式删除词条。
-- ---------------------------------------------------------------------
ALTER TABLE attribute_template  DISABLE TRIGGER trg_attribute_template_no_delete;
ALTER TABLE physical_class      DISABLE TRIGGER trg_physical_class_no_delete;
ALTER TABLE naming_core_term    DISABLE TRIGGER trg_naming_core_term_no_delete;
ALTER TABLE naming_qualifier    DISABLE TRIGGER trg_naming_qualifier_no_delete;
ALTER TABLE restricted_term     DISABLE TRIGGER trg_restricted_term_no_delete;

DELETE FROM attribute_template_item;
DELETE FROM attribute_template;
DELETE FROM physical_class   WHERE definition LIKE '[占位]%';
DELETE FROM naming_core_term WHERE definition LIKE '[占位]%';
DELETE FROM naming_qualifier WHERE definition LIKE '[占位]%';
DELETE FROM restricted_term;

-- ---------------------------------------------------------------------
-- 3.1 二级物理分类 — CLS-001 附录A (60 条)
--     CLS-SYS-001: 系统内置, 普通用户只能选择
-- ---------------------------------------------------------------------
INSERT INTO physical_class (code, primary_class_code, name_cn, name_en, definition, sort_order) VALUES
('T1-01', 'T1', '板形件', 'Plate-Form', '一个尺寸明显小于另外两个尺寸,主体呈平板、薄板、折弯板或片状。典型对象: 面板、盖板、隔板、钣金支架', 10),
('T1-02', 'T1', '壳形件', 'Shell-Form', '主体形成明显三维包络、腔体或薄壁围护空间。典型对象: 机壳、罩壳、盒体、外罩', 20),
('T1-03', 'T1', '块形件', 'Block-Form', '三个方向尺寸相对接近,以实体块状或厚壁整体结构为主体。典型对象: 安装座、基座、块状机加工件', 30),
('T1-04', 'T1', '杆形件', 'Rod-Form', '长度明显大于横截面尺寸,主体呈非旋转配合的细长杆状。典型对象: 拉杆、撑杆、连杆', 40),
('T1-05', 'T1', '轴形件', 'Shaft-Form', '以回转轴线、圆柱配合面或轴类阶梯结构为主要几何特征。典型对象: 转轴、销轴、轴销、螺柱', 50),
('T1-06', 'T1', '管形件', 'Tube-Form', '以细长中空管状结构为主体。典型对象: 硬管、导管、直管', 60),
('T1-07', 'T1', '环形件', 'Ring-Form', '以闭合或近闭合环状、圈状、箍状结构为主要外形。典型对象: 抱箍、卡环、挡圈', 70),
('T1-08', 'T1', '盘形件', 'Disc-Form', '以圆形或近圆形薄盘结构为主体,径向尺寸明显大于厚度。典型对象: 垫圈、圆盘、圆形盖板', 80),
('T1-09', 'T1', '套筒件', 'Sleeve-Form', '以短中空圆柱或包覆式筒状结构为主体,并具有明显套装或轴孔配合特征。典型对象: 衬套、轴套、隔套', 90),
('T1-10', 'T1', '球形件', 'Sphere-Form', '球面、近球面或球头是主要识别、配合或运动几何特征。典型对象: 球头、球头销、球芯', 100),
('T1-11', 'T1', '锥形件', 'Cone-Form', '圆锥、截锥或锥面构成主要外形或配合特征。典型对象: 锥套、锥塞', 110),
('T1-12', 'T1', '弹簧件', 'Spring-Form', '最终实体由专门的弹簧几何结构形成,以可恢复弹性变形为显著特征。典型对象: 螺旋弹簧、片簧、碟簧', 120),
('T1-13', 'T1', '膜片件', 'Membrane-Form', '极薄且整体柔性明显,主体呈膜状或柔性片状。典型对象: 隔膜、膜片、柔性垫片', 130),
('T1-14', 'T1', '线形件', 'Wire-Form', '由金属丝或细长柔性线材形成,截面尺寸远小于长度。典型对象: 钢丝件、锁线、细丝成形件', 140),
('T1-15', 'T1', '网状件', 'Mesh-Form', '主体呈网孔、筛网、编织网或多孔网状结构。典型对象: 金属网、滤网', 150),
('T1-16', 'T1', '波纹件', 'Corrugated-Form', '波纹、褶皱或连续折皱是决定对象形态的主要结构特征。典型对象: 波纹管、波纹罩', 160),
('T1-17', 'T1', '异形件', 'Complex-Form', '由多种基本几何形态组成且不存在可合理确定的单一主导形态。典型对象: 复杂整体铸件、复杂一体机加件', 170),
('T1-99', 'T1', '其他机械形体', 'Other Mechanical Form', '确实无法归入既有 T1 二级分类的特殊对象。典型对象: 须说明原因并经批准', 180),
('T2-01', 'T2', '裸板', 'Bare Board', '以未装配电子元器件的印制导体基板为主体。典型对象: PCB、FPC、刚挠板', 190),
('T2-02', 'T2', '装配板', 'Assembled Board', '以装有电子元器件的印制板为主体,板体仍是主要物理载体。典型对象: PCBA、电源板、控制板', 200),
('T2-03', 'T2', '封装器件', 'Packaged Device', '电子结构封装于单一器件封装体内,通常作为更高层装配的元件。典型对象: IC、电阻、电容、二极管', 210),
('T2-04', 'T2', '插合件', 'Mating Part', '具有明确可分离插合界面、配合壳体或多接触位结构。典型对象: 插头、插座、板端连接器', 220),
('T2-05', 'T2', '端接件', 'Termination Part', '以单点或少量导体的端接、压接、焊接或接触实体为主。典型对象: 端子、触点、插针、插孔', 230),
('T2-06', 'T2', '绕制件', 'Wound Part', '以导线绕组、线圈、磁芯或绕制结构构成主要实体。典型对象: 变压器、电感、线圈', 240),
('T2-07', 'T2', '电芯', 'Cell', '以封装电化学单元形成独立实体。典型对象: 圆柱电芯、方形电芯、软包电芯', 250),
('T2-08', 'T2', '机电件', 'Electromechanical Part', '电气部分和机械运动结构在同一实体内不可分离。典型对象: 继电器、电机、螺线管、微动开关', 260),
('T2-09', 'T2', '膜式件', 'Film-Type Part', '主要电气结构形成于薄膜、箔层或柔性层状载体。典型对象: 薄膜加热片、柔性电极件', 270),
('T2-10', 'T2', '条柱件', 'Bar/Column Part', '主体呈棒状、柱状或细长刚性电气实体。典型对象: 柱状传感器、棒状天线单元', 280),
('T2-11', 'T2', '环盘件', 'Ring/Disc Part', '主体呈明显环形或盘状电气结构。典型对象: 环形传感单元、盘式电气单元', 290),
('T2-12', 'T2', '母排', 'Busbar', '以刚性、大截面导电条或导电板作为主要实体。典型对象: Busbar、导电排', 300),
('T2-13', 'T2', '模块', 'Module', '多个电气电子元件集成并封装为独立模块实体,通常不具有完整设备级外壳界面。典型对象: DC/DC 模块、转换模块', 310),
('T2-14', 'T2', '盒式设备', 'Box-Type Equipment', '具有独立壳体、接口和内部电子构成,整体呈盒体或机箱式完整设备形态。典型对象: LRU、控制盒、电源设备', 320),
('T2-15', 'T2', '面板设备', 'Panel Equipment', '以平面面板作为主要安装界面和外形识别面的完整电气设备。典型对象: 控制面板、显示控制面板', 330),
('T2-16', 'T2', '开放组件', 'Open Assembly', '由多个电气元件构成但不具有完整封闭外壳,且主体不是印制板。典型对象: 开放式电气组件、端子组件', 340),
('T2-17', 'T2', '异形电气件', 'Complex Electrical Part', '复杂电气实体,不存在可合理确定的单一上述物理形态。典型对象: 特殊集成电气件', 350),
('T2-99', 'T2', '其他电气形体', 'Other Electrical Form', '确实无法归入既有 T2 二级分类的特殊对象。典型对象: 须说明原因并经批准', 360),
('T3-01', 'T3', '导线组件', 'Wire Assembly', '以单根独立绝缘导线为主体并完成受控成端。典型对象: 单根成端导线', 370),
('T3-02', 'T3', '线束', 'Harness', '以多根离散导线的组合、分支、绑扎和成端结构为主体。典型对象: Wire Harness', 380),
('T3-03', 'T3', '圆形电缆', 'Round Cable', '以一体外护套圆截面多芯电缆为主体。典型对象: Multi-core Cable', 390),
('T3-04', 'T3', '双绞电缆', 'Twisted-Pair Cable', '以成对绞合导体结构为主要传输单元。典型对象: Twisted Pair Cable', 400),
('T3-05', 'T3', '同轴电缆', 'Coaxial Cable', '以中心导体、介质、屏蔽层构成的同轴结构为主体。典型对象: Coaxial Cable', 410),
('T3-06', 'T3', '三同轴电缆', 'Triaxial Cable', '以三同轴导体和双屏蔽结构为主体。典型对象: Triaxial Cable', 420),
('T3-07', 'T3', '扁平电缆', 'Flat Cable', '以多导体平行排列的扁平带状结构为主体。典型对象: Ribbon Cable、FFC', 430),
('T3-08', 'T3', '光纤电缆', 'Fiber Optic Cable', '以光纤作为主要传输介质。典型对象: Fiber Optic Cable', 440),
('T3-09', 'T3', '编织带', 'Braid', '以金属丝编织形成柔性带状导体。典型对象: Bonding Braid', 450),
('T3-10', 'T3', '柔性互连', 'Flexible Interconnect', '以柔性印制导体或薄膜导体形成互连主体。典型对象: Flexible Interconnect', 460),
('T3-11', 'T3', '混合电缆', 'Hybrid Cable', '一个不可分电缆本体中包含两种以上明显不同传输介质。典型对象: 电光复合缆', 470),
('T3-12', 'T3', '特种电缆', 'Special Cable', '具有明显特殊结构且不属于前述形态。典型对象: 矿物绝缘电缆等', 480),
('T3-99', 'T3', '其他互连形体', 'Other Interconnect Form', '确实无法归入既有 T3 二级分类的特殊对象。典型对象: 须说明原因并经批准', 490),
('SW-01', 'SW', '可执行软件', 'Executable Software', '以可执行代码或可独立运行的软件映像作为受控对象。典型对象: 应用程序、嵌入式可执行映像', 500),
('SW-02', 'SW', '固件', 'Firmware', '与特定硬件紧密耦合并作为独立受控软件对象发布的固件映像。典型对象: MCU Firmware', 510),
('SW-03', 'SW', '引导程序', 'Bootloader', '负责启动、初始化、加载或软件切换的独立受控程序。典型对象: Bootloader', 520),
('SW-04', 'SW', '可编程逻辑', 'Programmable Logic', '用于 FPGA、CPLD、PLD 等器件的逻辑配置对象。典型对象: Bitstream、JEDEC 配置', 530),
('SW-05', 'SW', '配置数据', 'Configuration Data', '不构成可执行代码,但决定功能组合、资源分配或运行构型的受控数据。典型对象: 功能启用配置、分区配置', 540),
('SW-06', 'SW', '参数数据', 'Parameter Data', '以参数值、标定值或阈值集合为主体的受控数据。典型对象: Calibration Data、Parameter Set', 550),
('SW-07', 'SW', '数据库', 'Database', '以结构化数据库内容作为独立受控设计对象。典型对象: 机载数据库', 560),
('SW-08', 'SW', '数据包', 'Data Package', '以成套数据文件或数据集合方式独立发布的受控对象。典型对象: 地图包、协议数据包', 570),
('SW-09', 'SW', '脚本', 'Script', '以解释执行或自动化脚本形式作为独立受控设计对象。典型对象: Lua、Python、Shell 脚本', 580),
('SW-10', 'SW', '模型数据', 'Model Data', '以可执行模型、控制模型或模型参数化文件为独立受控对象。典型对象: 控制模型、算法模型', 590),
('SW-99', 'SW', '其他软件对象', 'Other Software Object', '确实无法归入既有 SW 二级分类的对象。典型对象: 须说明原因并经批准', 600);

-- ---------------------------------------------------------------------
-- 3.2 核心工程实体名称 — NAM-001 §5 (148 条)
--     INV-023 / NAM §1: 用户不得自由创造核心实体名称
-- ---------------------------------------------------------------------
INSERT INTO naming_core_term (code, name_cn, name_en, primary_class_code) VALUES
('T1-001', '板', 'PLATE', 'T1'),
('T1-002', '面板', 'PANEL', 'T1'),
('T1-003', '盖板', 'COVER PLATE', 'T1'),
('T1-004', '隔板', 'PARTITION', 'T1'),
('T1-005', '基板', 'BASE PLATE', 'T1'),
('T1-006', '支架', 'BRACKET', 'T1'),
('T1-007', '支座', 'SUPPORT', 'T1'),
('T1-008', '安装座', 'MOUNT', 'T1'),
('T1-009', '基座', 'BASE', 'T1'),
('T1-010', '抱箍', 'CLAMP', 'T1'),
('T1-011', '夹块', 'CLAMP BLOCK', 'T1'),
('T1-012', '压板', 'RETAINER PLATE', 'T1'),
('T1-013', '壳体', 'HOUSING', 'T1'),
('T1-014', '罩', 'SHROUD', 'T1'),
('T1-015', '盖', 'COVER', 'T1'),
('T1-016', '框架', 'FRAME', 'T1'),
('T1-017', '梁', 'BEAM', 'T1'),
('T1-018', '肋', 'RIB', 'T1'),
('T1-019', '加强件', 'REINFORCEMENT', 'T1'),
('T1-020', '衬套', 'BUSHING', 'T1'),
('T1-021', '套筒', 'SLEEVE', 'T1'),
('T1-022', '隔套', 'SPACER', 'T1'),
('T1-023', '轴', 'SHAFT', 'T1'),
('T1-024', '销', 'PIN', 'T1'),
('T1-025', '销钉', 'DOWEL', 'T1'),
('T1-026', '球头', 'BALL END', 'T1'),
('T1-027', '球头销', 'BALL STUD', 'T1'),
('T1-028', '杆', 'ROD', 'T1'),
('T1-029', '连杆', 'LINK', 'T1'),
('T1-030', '拉杆', 'TIE ROD', 'T1'),
('T1-031', '撑杆', 'STRUT', 'T1'),
('T1-032', '管', 'TUBE', 'T1'),
('T1-033', '导管', 'DUCT', 'T1'),
('T1-034', '法兰', 'FLANGE', 'T1'),
('T1-035', '垫片', 'GASKET', 'T1'),
('T1-036', '垫圈', 'WASHER', 'T1'),
('T1-037', '挡圈', 'RETAINING RING', 'T1'),
('T1-038', '卡环', 'SNAP RING', 'T1'),
('T1-039', '密封圈', 'SEAL RING', 'T1'),
('T1-040', '密封垫', 'SEAL', 'T1'),
('T1-041', '弹簧', 'SPRING', 'T1'),
('T1-042', '膜片', 'DIAPHRAGM', 'T1'),
('T1-043', '导轨', 'GUIDE RAIL', 'T1'),
('T1-044', '滑块', 'SLIDER', 'T1'),
('T1-045', '铰链', 'HINGE', 'T1'),
('T1-046', '锁扣', 'LATCH', 'T1'),
('T1-047', '手柄', 'HANDLE', 'T1'),
('T1-048', '旋钮', 'KNOB', 'T1'),
('T1-049', '按钮帽', 'BUTTON CAP', 'T1'),
('T1-050', '螺栓', 'BOLT', 'T1'),
('T1-051', '螺钉', 'SCREW', 'T1'),
('T1-052', '螺柱', 'STUD', 'T1'),
('T1-053', '螺母', 'NUT', 'T1'),
('T1-054', '铆钉', 'RIVET', 'T1'),
('T1-055', '键', 'KEY', 'T1'),
('T1-056', '锁片', 'LOCK PLATE', 'T1'),
('T1-057', '防护网', 'GUARD MESH', 'T1'),
('T1-058', '滤网', 'FILTER SCREEN', 'T1'),
('T1-059', '波纹管', 'BELLOWS', 'T1'),
('T1-060', '软管', 'HOSE', 'T1'),
('T2-001', '印制板', 'PRINTED CIRCUIT BOARD', 'T2'),
('T2-002', '板组件', 'CIRCUIT CARD ASSEMBLY', 'T2'),
('T2-003', '模块', 'MODULE', 'T2'),
('T2-004', '单元', 'UNIT', 'T2'),
('T2-005', '设备', 'EQUIPMENT', 'T2'),
('T2-006', '控制器', 'CONTROLLER', 'T2'),
('T2-007', '转换器', 'CONVERTER', 'T2'),
('T2-008', '适配器', 'ADAPTER', 'T2'),
('T2-009', '连接器', 'CONNECTOR', 'T2'),
('T2-010', '插头', 'PLUG', 'T2'),
('T2-011', '插座', 'RECEPTACLE', 'T2'),
('T2-012', '端子', 'TERMINAL', 'T2'),
('T2-013', '触点', 'CONTACT', 'T2'),
('T2-014', '插针', 'PIN CONTACT', 'T2'),
('T2-015', '插孔', 'SOCKET CONTACT', 'T2'),
('T2-016', '开关', 'SWITCH', 'T2'),
('T2-017', '继电器', 'RELAY', 'T2'),
('T2-018', '断路器', 'CIRCUIT BREAKER', 'T2'),
('T2-019', '熔断器', 'FUSE', 'T2'),
('T2-020', '电阻器', 'RESISTOR', 'T2'),
('T2-021', '电容器', 'CAPACITOR', 'T2'),
('T2-022', '电感器', 'INDUCTOR', 'T2'),
('T2-023', '变压器', 'TRANSFORMER', 'T2'),
('T2-024', '线圈', 'COIL', 'T2'),
('T2-025', '二极管', 'DIODE', 'T2'),
('T2-026', '晶体管', 'TRANSISTOR', 'T2'),
('T2-027', '集成电路', 'INTEGRATED CIRCUIT', 'T2'),
('T2-028', '晶振', 'CRYSTAL OSCILLATOR', 'T2'),
('T2-029', '传感器', 'SENSOR', 'T2'),
('T2-030', '探测器', 'DETECTOR', 'T2'),
('T2-031', '执行器', 'ACTUATOR', 'T2'),
('T2-032', '电机', 'MOTOR', 'T2'),
('T2-033', '风机', 'FAN', 'T2'),
('T2-034', '泵', 'PUMP', 'T2'),
('T2-035', '加热器', 'HEATER', 'T2'),
('T2-036', '显示器', 'DISPLAY', 'T2'),
('T2-037', '指示器', 'INDICATOR', 'T2'),
('T2-038', '灯', 'LIGHT', 'T2'),
('T2-039', '天线', 'ANTENNA', 'T2'),
('T2-040', '滤波器', 'FILTER', 'T2'),
('T2-041', '放大器', 'AMPLIFIER', 'T2'),
('T2-042', '耦合器', 'COUPLER', 'T2'),
('T2-043', '分配器', 'DISTRIBUTOR', 'T2'),
('T2-044', '合路器', 'COMBINER', 'T2'),
('T2-045', '分路器', 'SPLITTER', 'T2'),
('T2-046', '电池', 'BATTERY', 'T2'),
('T2-047', '电芯', 'CELL', 'T2'),
('T2-048', '母排', 'BUSBAR', 'T2'),
('T2-049', '接地条', 'GROUND BAR', 'T2'),
('T2-050', '屏蔽罩', 'SHIELD', 'T2'),
('T2-051', '电磁铁', 'SOLENOID', 'T2'),
('T2-052', '扬声器', 'SPEAKER', 'T2'),
('T2-053', '麦克风', 'MICROPHONE', 'T2'),
('T2-054', '摄像机', 'CAMERA', 'T2'),
('T2-055', '编码器', 'ENCODER', 'T2'),
('T2-056', '解码器', 'DECODER', 'T2'),
('T2-057', '网关', 'GATEWAY', 'T2'),
('T2-058', '路由器', 'ROUTER', 'T2'),
('T2-059', '交换机', 'NETWORK SWITCH', 'T2'),
('T2-060', '收发机', 'TRANSCEIVER', 'T2'),
('T2-061', '发射机', 'TRANSMITTER', 'T2'),
('T2-062', '接收机', 'RECEIVER', 'T2'),
('T2-063', '调制解调器', 'MODEM', 'T2'),
('T3-001', '导线组件', 'WIRE ASSEMBLY', 'T3'),
('T3-002', '线束', 'WIRE HARNESS', 'T3'),
('T3-003', '电缆组件', 'CABLE ASSEMBLY', 'T3'),
('T3-004', '同轴电缆组件', 'COAXIAL CABLE ASSEMBLY', 'T3'),
('T3-005', '三同轴电缆组件', 'TRIAXIAL CABLE ASSEMBLY', 'T3'),
('T3-006', '双绞电缆组件', 'TWISTED-PAIR CABLE ASSEMBLY', 'T3'),
('T3-007', '扁平电缆组件', 'FLAT CABLE ASSEMBLY', 'T3'),
('T3-008', '光纤组件', 'FIBER OPTIC ASSEMBLY', 'T3'),
('T3-009', '编织带', 'BRAID', 'T3'),
('T3-010', '搭接带', 'BONDING STRAP', 'T3'),
('T3-011', '柔性互连组件', 'FLEXIBLE INTERCONNECT ASSEMBLY', 'T3'),
('T3-012', '跳线组件', 'JUMPER ASSEMBLY', 'T3'),
('T3-013', '转接线束', 'ADAPTER HARNESS', 'T3'),
('T3-014', '混合电缆组件', 'HYBRID CABLE ASSEMBLY', 'T3'),
('SW-001', '软件', 'SOFTWARE', 'SW'),
('SW-002', '固件', 'FIRMWARE', 'SW'),
('SW-003', '引导程序', 'BOOTLOADER', 'SW'),
('SW-004', '驱动程序', 'DRIVER', 'SW'),
('SW-005', '可编程逻辑', 'PROGRAMMABLE LOGIC', 'SW'),
('SW-006', '配置数据', 'CONFIGURATION DATA', 'SW'),
('SW-007', '参数数据', 'PARAMETER DATA', 'SW'),
('SW-008', '数据库', 'DATABASE', 'SW'),
('SW-009', '数据包', 'DATA PACKAGE', 'SW'),
('SW-010', '脚本', 'SCRIPT', 'SW'),
('SW-011', '模型数据', 'MODEL DATA', 'SW');

UPDATE naming_core_term SET definition =
    '依据 UG-EDS-NAM-001《基本图号命名规则》R00.01 §5 固化的核心工程实体名称'
 WHERE definition IS NULL;

-- ---------------------------------------------------------------------
-- 3.3 稳定限定词 — NAM-001 §6 (60 条)
--     §6.1 几何与构造 26 条; §6.2 固有功能 34 条
-- ---------------------------------------------------------------------
INSERT INTO naming_qualifier (code, name_cn, name_en, qualifier_type) VALUES
('Q-GEO-01', 'P形', 'P-SHAPED', 'GEOMETRY'),
('Q-GEO-02', 'U形', 'U-SHAPED', 'GEOMETRY'),
('Q-GEO-03', 'L形', 'L-SHAPED', 'GEOMETRY'),
('Q-GEO-04', 'Z形', 'Z-SHAPED', 'GEOMETRY'),
('Q-GEO-05', '环形', 'ANNULAR', 'GEOMETRY'),
('Q-GEO-06', '盘形', 'DISC', 'GEOMETRY'),
('Q-GEO-07', '锥形', 'CONICAL', 'GEOMETRY'),
('Q-GEO-08', '球形', 'SPHERICAL', 'GEOMETRY'),
('Q-GEO-09', '中空', 'HOLLOW', 'GEOMETRY'),
('Q-GEO-10', '薄壁', 'THIN-WALLED', 'GEOMETRY'),
('Q-STR-01', '单耳', 'SINGLE-LUG', 'STRUCTURE'),
('Q-STR-02', '双耳', 'DOUBLE-LUG', 'STRUCTURE'),
('Q-STR-03', '法兰', 'FLANGED', 'STRUCTURE'),
('Q-STR-04', '开口', 'OPEN', 'STRUCTURE'),
('Q-STR-05', '闭口', 'CLOSED', 'STRUCTURE'),
('Q-STR-06', '分体', 'SPLIT', 'STRUCTURE'),
('Q-STR-07', '整体', 'INTEGRAL', 'STRUCTURE'),
('Q-STR-08', '可调', 'ADJUSTABLE', 'STRUCTURE'),
('Q-STR-09', '快拆', 'QUICK-RELEASE', 'STRUCTURE'),
('Q-STR-10', '多分支', 'MULTI-BRANCH', 'STRUCTURE'),
('Q-STR-11', '屏蔽', 'SHIELDED', 'STRUCTURE'),
('Q-STR-12', '同轴', 'COAXIAL', 'STRUCTURE'),
('Q-STR-13', '三同轴', 'TRIAXIAL', 'STRUCTURE'),
('Q-STR-14', '双绞', 'TWISTED-PAIR', 'STRUCTURE'),
('Q-STR-15', '扁平', 'FLAT', 'STRUCTURE'),
('Q-STR-16', '刚挠', 'RIGID-FLEX', 'STRUCTURE'),
('Q-FUN-01', '直流电源', 'DC POWER', 'FUNCTION'),
('Q-FUN-02', '交流电源', 'AC POWER', 'FUNCTION'),
('Q-FUN-03', '电源控制', 'POWER CONTROL', 'FUNCTION'),
('Q-FUN-04', '电源分配', 'POWER DISTRIBUTION', 'FUNCTION'),
('Q-FUN-05', '电源转换', 'POWER CONVERSION', 'FUNCTION'),
('Q-FUN-06', '信号调理', 'SIGNAL CONDITIONING', 'FUNCTION'),
('Q-FUN-07', '信号转换', 'SIGNAL CONVERSION', 'FUNCTION'),
('Q-FUN-08', '数据采集', 'DATA ACQUISITION', 'FUNCTION'),
('Q-FUN-09', '数据处理', 'DATA PROCESSING', 'FUNCTION'),
('Q-FUN-10', '视频采集', 'VIDEO CAPTURE', 'FUNCTION'),
('Q-FUN-11', '视频处理', 'VIDEO PROCESSING', 'FUNCTION'),
('Q-FUN-12', '音频采集', 'AUDIO CAPTURE', 'FUNCTION'),
('Q-FUN-13', '音频处理', 'AUDIO PROCESSING', 'FUNCTION'),
('Q-FUN-14', '通信控制', 'COMMUNICATION CONTROL', 'FUNCTION'),
('Q-FUN-15', '网络通信', 'NETWORK COMMUNICATION', 'FUNCTION'),
('Q-FUN-16', '射频', 'RF', 'FUNCTION'),
('Q-FUN-17', '卫星通信', 'SATELLITE COMMUNICATION', 'FUNCTION'),
('Q-FUN-18', '温度', 'TEMPERATURE', 'FUNCTION'),
('Q-FUN-19', '压力', 'PRESSURE', 'FUNCTION'),
('Q-FUN-20', '位置', 'POSITION', 'FUNCTION'),
('Q-FUN-21', '速度', 'SPEED', 'FUNCTION'),
('Q-FUN-22', '加速度', 'ACCELERATION', 'FUNCTION'),
('Q-FUN-23', '流量', 'FLOW', 'FUNCTION'),
('Q-FUN-24', '光电', 'PHOTOELECTRIC', 'FUNCTION'),
('Q-FUN-25', '逻辑控制', 'LOGIC CONTROL', 'FUNCTION'),
('Q-FUN-26', '运动控制', 'MOTION CONTROL', 'FUNCTION'),
('Q-FUN-27', '状态监测', 'CONDITION MONITORING', 'FUNCTION'),
('Q-FUN-28', '显示', 'DISPLAY', 'FUNCTION'),
('Q-FUN-29', '照明', 'LIGHTING', 'FUNCTION'),
('Q-FUN-30', '指示', 'INDICATION', 'FUNCTION'),
('Q-FUN-31', '测试', 'TEST', 'FUNCTION'),
('Q-FUN-32', '自检', 'SELF-TEST', 'FUNCTION'),
('Q-INT-01', '接口', 'INTERFACE', 'INTERFACE'),
('Q-INT-02', '转接', 'ADAPTER', 'INTERFACE');

UPDATE naming_qualifier SET definition =
    '依据 UG-EDS-NAM-001《基本图号命名规则》R00.01 §6 固化的稳定限定词'
 WHERE definition IS NULL;

ALTER TABLE attribute_template  ENABLE TRIGGER trg_attribute_template_no_delete;
ALTER TABLE physical_class      ENABLE TRIGGER trg_physical_class_no_delete;
ALTER TABLE naming_core_term    ENABLE TRIGGER trg_naming_core_term_no_delete;
ALTER TABLE naming_qualifier    ENABLE TRIGGER trg_naming_qualifier_no_delete;
ALTER TABLE restricted_term     ENABLE TRIGGER trg_restricted_term_no_delete;

-- ---------------------------------------------------------------------
-- 3.4 受限词词典 — NAM-001 §7
--
-- ★ 需业主决策的基准冲突（已按 NAM 装载，可一条 SQL 切回 DD）★
--   UG-DCMS-DD-001 §5 与 UG-EDS-NAM-001 §7 对同类受限词给出的控制级别不一致:
--     受限类型      DD-001 §5        NAM-001 §7
--     机型          WARN_APPROVAL    默认禁止, 例外须批准
--     位置          WARN             默认禁止
--     NHA           WARN             默认禁止
--     材料          WARN             默认禁止
--     颜色          WARN             默认禁止
--     具体参数      WARN             基本图号名称默认禁止(可用于 Dash 名称)
--     表面处理      DD 未列          默认禁止
--   DD-001 文件控制条款规定"发生冲突时必须发起基准变更, 不得由开发自行解释"。
--   本迁移按 NAM(较严)装载, 因为 NAM 是命名规则的专业归口规范, 且从严不会
--   放过 DD 本应拦截的名称。若决定以 DD-001 §5 为准, 执行:
--       UPDATE restricted_term SET control_level='WARN'
--        WHERE restricted_type IN ('LOCATION','NHA','MATERIAL','COLOR','PARAMETER');
--       UPDATE restricted_term SET control_level='WARN_APPROVAL'
--        WHERE restricted_type='AIRCRAFT';
--   无论采用哪一侧, 都应形成一份基准变更记录(BCR)归档。
--
--   另注: 见 §4.3, 基本图号名称改为系统按词典组合生成, 用户无法自由输入,
--   因此受限词检查在基本图号层面已成为兜底手段; 其主要作用域是 Dash P/N 名称。
-- ---------------------------------------------------------------------
-- 受限词类型域需补充 SURFACE (0002 建表时依据 DD §5, 未含表面处理)
ALTER TABLE restricted_term DROP CONSTRAINT restricted_term_restricted_type_check;
ALTER TABLE restricted_term ADD CONSTRAINT restricted_term_restricted_type_check
    CHECK (restricted_type IN ('PROJECT', 'CUSTOMER', 'REVISION', 'MARKETING',
                               'AIRCRAFT', 'LOCATION', 'NHA', 'MATERIAL',
                               'SURFACE', 'COLOR', 'PARAMETER'));

-- 注: 下列 ASCII 边界写法 (^|[^0-9A-Za-z]) 不可改回 PostgreSQL 的 \m \M。
--     \m/\M 依据 [[:alnum:]] 判定词边界, 而 CJK 字符属于 alnum,
--     "A320左侧7075支架" 中 A320 之后不存在词边界, \M 会导致机型词漏检。
INSERT INTO restricted_term
    (restricted_type, control_level, pattern, is_regex, example, message_cn) VALUES
-- 项目 / 审批类型 — 禁止
('PROJECT',   'BLOCK', '(^|[^0-9A-Za-z])(STC|PMA|CTSOA|MDA|TSO|DOA)([^0-9A-Za-z]|$)', true, 'STC',
 'NAM §7: 项目/审批类型词禁止进入名称'),
('PROJECT',   'BLOCK', 'UG-(STC|PMA|CTSOA|MDA)[0-9A-Z]*', true, 'UG-STC07A25',
 'NAM §7: 项目编号禁止进入名称'),
-- 客户 — 禁止
('CUSTOMER',  'BLOCK', '(航空公司|航空股份|Airlines|Airways|航空有限)', true, '南方航空',
 'NAM §7: 客户名称禁止进入名称'),
-- 版次 / 代际 — 禁止
('REVISION',  'BLOCK', '(^|[^0-9A-Za-z])(REV|Rev|Revision|R[0-9]{2}|V[0-9]+)([^0-9A-Za-z]|$)', true, 'Rev.01',
 'NAM §7: 版次标识禁止进入名称; 三层身份(图号/Dash/Revision)不得混用'),
('REVISION',  'BLOCK', '(新版|旧版|二代|三代|一代)', true, '二代',
 'NAM §7: 代际词禁止进入名称'),
-- 宣传性 — 禁止
('MARKETING', 'BLOCK', '(新型|改进型|增强型|优化型|升级版|高性能|经济型)', true, '增强型',
 'NAM §7: 宣传性词汇禁止进入名称'),
-- 机型 — 默认禁止, 例外须批准
('AIRCRAFT',  'WARN_APPROVAL', '(^|[^0-9A-Za-z])(A3[0-9]{2}|A2[0-9]{2}|B7[0-9]{2}|H1[0-9]{2}|H2[0-9]{2}|AS3[0-9]{2}|EC[0-9]{3}|ARJ21|C919|C929)([^0-9A-Za-z]|$)', true, 'A320',
 'NAM §7: 机型词默认禁止, 例外须经批准'),
-- 位置 — 默认禁止
('LOCATION',  'BLOCK', '(左侧|右侧|前舱|后舱|底板|顶板|上部|下部|内侧|外侧)', true, '左侧',
 'NAM §7: 安装位置词默认禁止进入名称'),
('LOCATION',  'BLOCK', '^(左|右|前|后|上|下|内|外)', true, '左',
 'NAM §7: 以方位词开头的名称默认禁止'),
-- NHA — 默认禁止
('NHA',       'BLOCK', '(^|[^0-9A-Za-z])(DCPM|PSU|PSCD|OU|LRU)([^0-9A-Za-z]|$)', true, 'PSU',
 'NAM §7: 上层对象(NHA)名称默认禁止进入名称'),
-- 材料 — 默认禁止
('MATERIAL',  'BLOCK', '(7075|6061|2024|2A12|304|316|321|TC4|不锈钢|铝合金|钛合金|碳纤维|尼龙|聚碳酸酯)', true, '7075',
 'NAM §7: 材料信息应记入材料属性, 不得进入名称'),
-- 表面处理 — 默认禁止
('SURFACE',   'BLOCK', '(阳极氧化|阳极化|镀镍|镀锌|镀铬|喷漆|钝化|化学转化|喷砂)', true, '阳极氧化',
 'NAM §7: 表面处理信息应记入表面处理属性, 不得进入名称'),
-- 颜色 — 默认禁止
('COLOR',     'BLOCK', '(黑色|白色|红色|蓝色|绿色|黄色|银色|灰色|透明)', true, '黑色',
 'NAM §7: 颜色信息应记入属性或表面处理, 不得进入名称'),
-- 具体参数 — 基本图号名称默认禁止, 可用于 Dash 名称
('PARAMETER', 'BLOCK', '(Ø|φ|Φ)[0-9]', true, 'Ø25',
 'NAM §7: 具体尺寸参数在基本图号名称中禁止; 可用于 Dash P/N 名称'),
('PARAMETER', 'BLOCK', '[0-9]+(\.[0-9]+)?\s*(mm|cm|kW|kHz|mV|mA|Nm|kg|Hz|W|V|A|N|g|m)([^0-9A-Za-z]|$)', true, '50W',
 'NAM §7: 具体参数值在基本图号名称中禁止; 可用于 Dash P/N 名称');

-- ---------------------------------------------------------------------
-- 3.5 重建属性模板 — SRS-ATT-002: 二级物理分类驱动属性模板
-- ---------------------------------------------------------------------
INSERT INTO attribute_template (code, name_cn, physical_class_id)
SELECT 'TPL-' || pc.code, pc.name_cn || ' 属性模板', pc.id
  FROM physical_class pc WHERE pc.primary_class_code IN ('T1', 'T2', 'T3');

INSERT INTO attribute_template_item
    (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id,
       ad.code = 'MATERIAL_REF',
       ad.code IN ('LENGTH_OVERALL', 'MATERIAL_REF'),
       CASE ad.code WHEN 'LENGTH_OVERALL' THEN 10 WHEN 'WIDTH_OVERALL' THEN 20
                    WHEN 'HEIGHT_OVERALL' THEN 30 WHEN 'MASS_NOMINAL' THEN 40
                    WHEN 'MATERIAL_REF' THEN 50 ELSE 60 END
  FROM attribute_template t
  JOIN physical_class pc ON pc.id = t.physical_class_id
  JOIN attribute_definition ad
    ON ad.code IN ('LENGTH_OVERALL','WIDTH_OVERALL','HEIGHT_OVERALL',
                   'MASS_NOMINAL','MATERIAL_REF','SURFACE_REF');

INSERT INTO attribute_template_item
    (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id, false, ad.code = 'VOLTAGE_NOMINAL', 110
  FROM attribute_template t
  JOIN physical_class pc ON pc.id = t.physical_class_id AND pc.primary_class_code = 'T2'
  JOIN attribute_definition ad
    ON ad.code IN ('VOLTAGE_NOMINAL','POWER_NOMINAL','CURRENT_MAX','IP_RATING','FLAMMABILITY');

INSERT INTO attribute_template_item
    (attribute_template_id, attribute_definition_id, is_required, is_key_attribute, sort_order)
SELECT t.id, ad.id, false, ad.code = 'WIRE_GAUGE', 120
  FROM attribute_template t
  JOIN physical_class pc ON pc.id = t.physical_class_id AND pc.primary_class_code = 'T3'
  JOIN attribute_definition ad ON ad.code IN ('WIRE_GAUGE','CONDUCTOR_COUNT');

-- =====================================================================
-- 4 两份规范新引入的强制规则
-- =====================================================================

-- ---------------------------------------------------------------------
-- 4.1 CLS-SYS-002: 一级分类与二级分类固定映射
--     选择 T1 后只能出现 T1 二级分类, T2/T3/SW 同理
-- NAM §8: 核心工程实体名称必须取自该一级类别对应的名称总表
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_check_family_class_consistency() RETURNS trigger AS $$
DECLARE
    v_pc_class   text;
    v_term_class text;
    v_pc_code    text;
    v_term_code  text;
BEGIN
    SELECT primary_class_code, code INTO v_pc_class, v_pc_code
      FROM physical_class WHERE id = NEW.physical_class_id;
    IF v_pc_class <> NEW.primary_class_code THEN
        RAISE EXCEPTION
            'DCMS-CLS-SYS-002: 二级分类 % 属于 %, 与所选一级分类 % 不匹配',
            v_pc_code, v_pc_class, NEW.primary_class_code USING ERRCODE = '23514';
    END IF;

    SELECT primary_class_code, code INTO v_term_class, v_term_code
      FROM naming_core_term WHERE id = NEW.core_term_id;
    IF v_term_class <> NEW.primary_class_code THEN
        RAISE EXCEPTION
            'DCMS-NAM-§8: 核心实体词 % 属于 %, 与所选一级分类 % 不匹配',
            v_term_code, v_term_class, NEW.primary_class_code USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_class_consistency
    BEFORE INSERT OR UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_check_family_class_consistency();

-- ---------------------------------------------------------------------
-- 4.2 CLS-SYS-007: 选择 XX-99 时必须填写理由并经批准
-- ---------------------------------------------------------------------
ALTER TABLE basic_drawing_family ADD COLUMN classification_note text;
COMMENT ON COLUMN basic_drawing_family.classification_note IS
    'CLS-SYS-007: 选用 XX-99 其他类时的无法归类理由';

CREATE OR REPLACE FUNCTION dcms_check_fallback_classification() RETURNS trigger AS $$
DECLARE v_code text;
BEGIN
    SELECT code INTO v_code FROM physical_class WHERE id = NEW.physical_class_id;
    IF v_code LIKE '%-99' THEN
        IF NEW.classification_note IS NULL OR btrim(NEW.classification_note) = '' THEN
            RAISE EXCEPTION
                'DCMS-CLS-SYS-007: 选用 % 其他类必须填写无法归类理由', v_code
                USING ERRCODE = '23514';
        END IF;
        IF NEW.status <> 'PENDING' AND NEW.approved_by IS NULL THEN
            RAISE EXCEPTION
                'DCMS-CLS-SYS-007: 选用 % 其他类必须经批准后方可生效', v_code
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_fallback_classification
    BEFORE INSERT OR UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_check_fallback_classification();

-- ---------------------------------------------------------------------
-- 4.3 NAM §3/§4/§9: 基本图号名称由系统按词典组合生成
--
-- NAM §9 规定"生成中文名称: 系统生成, 用户只可确认, 不能直接自由改写"。
-- 因此这里不是"校验用户输入的名称", 而是直接按
--     【0~2 个稳定限定词】+【1 个固化核心工程实体名称】
-- 生成 family_name_cn / family_name_en, 覆盖用户传入值。
--
-- 这样处理的效果: 受限词、自由文本、同义词、机型/位置/材料等根本无法
-- 进入基本图号名称 —— NAM-AT-001 与 NAM-AT-004 在结构上即已满足,
-- 而不是依赖正则匹配去拦截。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_compose_family_name() RETURNS trigger AS $$
DECLARE
    v_q1_cn text := ''; v_q1_en text := '';
    v_q2_cn text := ''; v_q2_en text := '';
    v_core_cn text;     v_core_en text;
BEGIN
    SELECT name_cn, name_en INTO v_core_cn, v_core_en
      FROM naming_core_term WHERE id = NEW.core_term_id;
    IF v_core_cn IS NULL THEN
        RAISE EXCEPTION
            'DCMS-INV-023: 未指定核心实体词或该词条不存在, 基本图号名称无法生成。'
            ' 核心工程实体名称必须取自 UG-EDS-NAM-001 §5 受控词典'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.qualifier_1_id IS NOT NULL THEN
        SELECT name_cn, name_en INTO v_q1_cn, v_q1_en
          FROM naming_qualifier WHERE id = NEW.qualifier_1_id;
    END IF;
    IF NEW.qualifier_2_id IS NOT NULL THEN
        SELECT name_cn, name_en INTO v_q2_cn, v_q2_en
          FROM naming_qualifier WHERE id = NEW.qualifier_2_id;
    END IF;

    -- 中文: 限定词与核心词直接连写 (P形 + 抱箍 -> P形抱箍)
    NEW.family_name_cn := v_q1_cn || v_q2_cn || v_core_cn;
    -- 英文: 空格分隔, 受控英文名称统一大写 (P-SHAPED CLAMP)
    NEW.family_name_en := upper(btrim(
        concat_ws(' ', nullif(v_q1_en, ''), nullif(v_q2_en, ''), v_core_en)));

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 命名在分类一致性校验之后执行(触发器同一时机按名称字母序), 故用 z 前缀
CREATE TRIGGER trg_z_family_compose_name
    BEFORE INSERT OR UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_compose_family_name();

-- ---------------------------------------------------------------------
-- 4.4 NAM §8 / CLS-SYS-005: 正式发号后分类与核心词锁定
--     ACTIVE/CLOSED 状态的设计族不得由普通更新改变一级/二级分类与核心词。
--     NAM-AT-006: 核心词由"抱箍"改为"支架"须触发设计族边界重新评估。
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION dcms_lock_family_identity() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('ACTIVE', 'CLOSED') THEN
        IF (NEW.primary_class_code, NEW.physical_class_id, NEW.core_term_id)
           IS DISTINCT FROM
           (OLD.primary_class_code, OLD.physical_class_id, OLD.core_term_id)
        THEN
            RAISE EXCEPTION
                'DCMS-CLS-SYS-005: 设计族 % 已正式生效, 一级/二级分类与核心实体词已锁定。'
                ' 分类更正须走授权更正流程(CLS-001 §13), 核心词变更须重新评估设计族边界(NAM-AT-006)',
                OLD.basic_drawing_number USING ERRCODE = '23514';
        END IF;
        IF (NEW.qualifier_1_id, NEW.qualifier_2_id)
           IS DISTINCT FROM (OLD.qualifier_1_id, OLD.qualifier_2_id)
        THEN
            RAISE EXCEPTION
                'DCMS-NAM-§10: 设计族 % 已正式生效, 稳定限定词变更会改变正式名称, 须走受控更正流程',
                OLD.basic_drawing_number USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_identity_lock
    BEFORE UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_lock_family_identity();

-- ---------------------------------------------------------------------
-- 5 登记新增规则, 供验收追溯
-- ---------------------------------------------------------------------
INSERT INTO invariant_registry (code, statement_cn, enforced_by, enforcement_ref, test_ref) VALUES
('CLS-SYS-002', '一级分类与二级分类固定映射; 核心实体词须属同一一级类别', 'DB_TRIGGER',
 'trg_family_class_consistency', 'test_cls_sys_002'),
('CLS-SYS-005', '正式发号后一级/二级分类与核心实体词锁定', 'DB_TRIGGER',
 'trg_family_identity_lock', 'test_cls_sys_005'),
('CLS-SYS-007', '选用 XX-99 其他类必须填写理由并经批准', 'DB_TRIGGER',
 'trg_family_fallback_classification', 'test_cls_sys_007'),
('NAM-§4', '基本图号名称由系统按受控词典组合生成, 用户不得自由输入', 'DB_TRIGGER',
 'trg_z_family_compose_name', 'test_nam_compose');

-- ---------------------------------------------------------------------
-- 6 装载自检
-- ---------------------------------------------------------------------
DO $$
DECLARE v_pc int; v_ct int; v_ql int; v_rt int; v_ph int;
BEGIN
    SELECT count(*) INTO v_pc FROM physical_class;
    SELECT count(*) INTO v_ct FROM naming_core_term;
    SELECT count(*) INTO v_ql FROM naming_qualifier;
    SELECT count(*) INTO v_rt FROM restricted_term;
    SELECT (SELECT count(*) FROM physical_class   WHERE definition LIKE '[占位]%')
         + (SELECT count(*) FROM naming_core_term WHERE definition LIKE '[占位]%')
         + (SELECT count(*) FROM naming_qualifier WHERE definition LIKE '[占位]%')
      INTO v_ph;

    IF v_pc <> 60 THEN RAISE EXCEPTION '二级分类应为 60 条, 实际 %', v_pc; END IF;
    IF v_ct <> 148 THEN RAISE EXCEPTION '核心实体词应为 148 条, 实际 %', v_ct; END IF;
    IF v_ql <> 60 THEN RAISE EXCEPTION '稳定限定词应为 60 条, 实际 %', v_ql; END IF;
    IF v_ph <> 0 THEN RAISE EXCEPTION '仍残留 % 条占位词条', v_ph; END IF;

    RAISE NOTICE '正式词典装载完成: 二级分类 % 条, 核心实体词 % 条, 稳定限定词 % 条, 受限词 % 条; 占位词条已清零',
        v_pc, v_ct, v_ql, v_rt;
    RAISE NOTICE '注意: 功能分类词典(F01~F16)仍为占位, 须以《功能分类规范》装载后替换';
END $$;
