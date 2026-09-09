-- =====================================================================
-- UG-DCMS 迁移 0009 — 装载正式功能分类词典, 替换 0007 的 F01~F16 占位
--
-- 装载依据: UG-EDS-FUN-001《设计对象功能分类规范》R00
--   一级功能域 F01~F16 按"设计对象所作用的对象"划分(力/运动/介质/热/
--   电能/电磁/信息/光/人), 不按产品线、机载系统或技术原理划分, 以求长期稳定。
--   FUN-SYS-001~010 中可在数据库层强制的条款在 §3 实现。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1 前置保护: 已有对象引用功能词典时不得直接替换
-- ---------------------------------------------------------------------
DO $$
DECLARE v_used integer; v_fam integer; v_loaded integer;
BEGIN
    SELECT count(*) INTO v_loaded FROM function_item WHERE definition NOT LIKE '[占位]%';
    IF v_loaded >= 98 THEN
        RAISE EXCEPTION 'DCMS-0009: 功能分类词典已装载(% 项), 本迁移无需重复执行', v_loaded
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_used FROM object_function;
    SELECT count(*) INTO v_fam FROM basic_drawing_family WHERE primary_function_id IS NOT NULL;
    IF v_used > 0 OR v_fam > 0 THEN
        RAISE EXCEPTION
            'DCMS-0009: 已有 % 条对象功能挂载、% 个设计族引用功能词典。'
            ' 占位词典替换只允许在尚无引用时执行; 已有数据须按 UG-EDS-FUN-001 §12 编写带映射的迁移。',
            v_used, v_fam USING ERRCODE = '23514';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 2 清除占位并装载正式词典
-- ---------------------------------------------------------------------
ALTER TABLE function_item   DISABLE TRIGGER trg_function_item_no_delete;
ALTER TABLE function_domain DISABLE TRIGGER trg_function_domain_no_delete;

DELETE FROM function_item;
DELETE FROM function_domain;

-- 2.1 一级功能域 — FUN-001 §7 (长期冻结, 16 个)
INSERT INTO function_domain (code, name_cn, name_en, definition, sort_order) VALUES
('F01', '结构承载', 'Structural Load Bearing', '作用对象: 力。对象的固有作用是承受、保持、传递或分散机械载荷。', 10),
('F02', '固定保持', 'Fixing and Retention', '作用对象: 位置关系。对象的固有作用是建立或保持其他对象之间的相对位置关系。', 20),
('F03', '运动传动', 'Motion and Transmission', '作用对象: 运动。对象的固有作用是允许、约束、导引、传递或产生相对运动。', 30),
('F04', '防护隔离', 'Protection and Isolation', '作用对象: 外部有害作用。对象的固有作用是使被保护对象或人员免受外部机械、环境或物理作用的影响。介质密封见 F05，电磁作用见 F11。', 40),
('F05', '密封阻隔', 'Sealing and Barrier', '作用对象: 介质。对象的固有作用是阻止流体、气体、颗粒或火焰通过特定界面。', 50),
('F06', '流体控制', 'Fluid Handling', '作用对象: 流体。对象的固有作用是输送、导引、调节、分配、分离或储存流体。', 60),
('F07', '热管理', 'Thermal Management', '作用对象: 热。对象的固有作用是传导、耗散、产生、阻隔或调节热量。', 70),
('F08', '电能分配', 'Electrical Power Distribution', '作用对象: 电能（传输）。对象的固有作用是传输、分配或提供电能接入。电能形式的改变见 F09，通断见 F10。', 80),
('F09', '电能转换', 'Electrical Power Conversion', '作用对象: 电能（形式）。对象的固有作用是改变电能的形式、参数或储存状态。', 90),
('F10', '电路通断', 'Circuit Interruption', '作用对象: 电路连通状态。对象的固有作用是建立或断开电路连通状态，包括为保护目的而断开。', 100),
('F11', '电磁防护', 'Electromagnetic Protection', '作用对象: 电磁作用。对象的固有作用是建立电位参考、阻断或衰减电磁干扰与放电能量。', 110),
('F12', '感知探测', 'Sensing and Detection', '作用对象: 物理量→信号。对象的固有作用是感知物理量、目标或状态并转换为可用信号。信号的后续处理见 F13。', 120),
('F13', '信息处理', 'Information Processing', '作用对象: 信号与数据（处理）。对象的固有作用是对信号或数据进行调理、变换、运算、控制或存储。', 130),
('F14', '信息传输', 'Information Transmission', '作用对象: 信号与数据（传输）。对象的固有作用是在节点之间传输、交换、分配信号或数据。', 140),
('F15', '光学', 'Optical', '作用对象: 光。对象的固有作用是产生、成形、反射、透射或调节光。以光传输信息的对象归 F14。', 150),
('F16', '人机界面', 'Human-Machine Interface', '作用对象: 人。对象的固有作用是向人员呈现信息或接受人员操作输入。', 160);

-- 2.2 二级功能 — FUN-001 §8 / 附录A (98 项)
INSERT INTO function_item (code, domain_code, name_cn, name_en, definition, sort_order) VALUES
('F01-01', 'F01', '承载', 'LOAD CARRYING', '承受并保持作用于自身的载荷', 10),
('F01-02', 'F01', '传力', 'LOAD TRANSFER', '将载荷传递至相邻结构或接口', 20),
('F01-03', 'F01', '支撑', 'SUPPORT', '为其他对象提供支承基面或支承点', 30),
('F01-04', 'F01', '加强', 'REINFORCEMENT', '局部提高结构强度、刚度或稳定性', 40),
('F01-05', 'F01', '载荷分散', 'LOAD DISTRIBUTION', '将集中载荷分散到较大区域', 50),
('F01-06', 'F01', '承压', 'PRESSURE CONTAINMENT', '承受内外压差引起的载荷', 60),
('F02-01', 'F02', '夹持', 'CLAMPING', '以环抱、压紧方式握持被固定对象', 10),
('F02-02', 'F02', '紧固', 'FASTENING', '以螺纹、铆接等方式实现可拆或不可拆连接', 20),
('F02-03', 'F02', '定位', 'LOCATING', '确定对象之间的相对位置或方位', 30),
('F02-04', 'F02', '锁止', 'LOCKING', '防止已建立的连接或位置发生松动、脱开', 40),
('F02-05', 'F02', '轴向保持', 'AXIAL RETENTION', '限制对象沿轴向的位移', 50),
('F02-06', 'F02', '间隔', 'SPACING', '保持对象之间的确定间距', 60),
('F02-07', 'F02', '悬挂', 'SUSPENDING', '将对象悬置于支承结构之下或之间', 70),
('F03-01', 'F03', '导引', 'GUIDING', '约束运动方向并引导运动过程', 10),
('F03-02', 'F03', '铰接', 'ARTICULATION', '形成可转动的连接关系', 20),
('F03-03', 'F03', '传动', 'POWER TRANSMISSION', '传递运动、扭矩或直线力', 30),
('F03-04', 'F03', '限位', 'MOTION LIMITING', '限定运动行程或极限位置', 40),
('F03-05', 'F03', '驱动', 'ACTUATION', '产生位移、转动或作用力', 50),
('F03-06', 'F03', '减摩', 'FRICTION REDUCTION', '降低相对运动界面的摩擦与磨损', 60),
('F03-07', 'F03', '位置调节', 'POSITION ADJUSTMENT', '提供可调整并保持的位置、长度或角度', 70),
('F04-01', 'F04', '包覆防护', 'COVERING', '包覆被保护对象形成防护界面', 10),
('F04-02', 'F04', '遮蔽', 'SHROUDING', '遮挡视线、光线、气流或飞溅物', 20),
('F04-03', 'F04', '减振隔振', 'VIBRATION ISOLATION', '衰减或隔断振动的传递', 30),
('F04-04', 'F04', '缓冲吸能', 'SHOCK ABSORPTION', '吸收冲击能量降低峰值载荷', 40),
('F04-05', 'F04', '降噪隔声', 'NOISE ATTENUATION', '衰减或隔断声能的传播', 50),
('F04-06', 'F04', '防异物', 'FOREIGN OBJECT PROTECTION', '阻止异物进入受保护区域', 60),
('F04-07', 'F04', '人身防护', 'PERSONNEL PROTECTION', '防止人员触电、夹伤、烫伤或割伤', 70),
('F05-01', 'F05', '静密封', 'STATIC SEALING', '在无相对运动界面上阻止介质泄漏', 10),
('F05-02', 'F05', '动密封', 'DYNAMIC SEALING', '在有相对运动界面上阻止介质泄漏', 20),
('F05-03', 'F05', '防水防潮', 'MOISTURE BARRIER', '阻止水或湿气侵入', 30),
('F05-04', 'F05', '防尘', 'DUST BARRIER', '阻止粉尘或颗粒物侵入', 40),
('F05-05', 'F05', '阻火', 'FIRE BARRIER', '阻止火焰或高温烟气蔓延', 50),
('F05-06', 'F05', '压力隔断', 'PRESSURE BARRIER', '隔断具有压差的两个空间', 60),
('F06-01', 'F06', '流体输送', 'FLUID TRANSPORT', '将流体从一处输送至另一处', 10),
('F06-02', 'F06', '流体导引', 'FLOW GUIDING', '引导流体的流动方向或路径', 20),
('F06-03', 'F06', '流量调节', 'FLOW REGULATION', '调节或限制流体的流量与压力', 30),
('F06-04', 'F06', '流体分配', 'FLUID DISTRIBUTION', '将流体分配至多个支路', 40),
('F06-05', 'F06', '过滤分离', 'FILTRATION', '从流体中分离颗粒或其他相', 50),
('F06-06', 'F06', '流体储存', 'FLUID STORAGE', '容纳并保持一定量的流体', 60),
('F07-01', 'F07', '导热', 'HEAT CONDUCTION', '在对象之间传导热量', 10),
('F07-02', 'F07', '散热', 'HEAT DISSIPATION', '将热量排放至环境', 20),
('F07-03', 'F07', '加热', 'HEATING', '产生并输入热量', 30),
('F07-04', 'F07', '隔热', 'THERMAL INSULATION', '阻隔热量传递', 40),
('F07-05', 'F07', '温度调节', 'TEMPERATURE REGULATION', '将温度维持在设定范围', 50),
('F08-01', 'F08', '电能传输', 'POWER TRANSMISSION', '在两点之间传输电能', 10),
('F08-02', 'F08', '电能分配', 'POWER DISTRIBUTION', '将电能分配至多个负载支路', 20),
('F08-03', 'F08', '电能接入', 'POWER INTERFACE', '为外部设备提供电能接入界面', 30),
('F08-04', 'F08', '汇流', 'POWER BUSING', '汇集多路电能形成公共节点', 40),
('F09-01', 'F09', '电压变换', 'VOLTAGE CONVERSION', '改变电压等级', 10),
('F09-02', 'F09', '交直流变换', 'AC-DC CONVERSION', '在交流与直流之间变换', 20),
('F09-03', 'F09', '电能调节', 'POWER CONDITIONING', '稳压、稳流或改善电能质量', 30),
('F09-04', 'F09', '电能储存', 'ENERGY STORAGE', '储存电能并按需释放', 40),
('F09-05', 'F09', '电能产生', 'POWER GENERATION', '将其他形式能量转换为电能', 50),
('F09-06', 'F09', '电热转换', 'ELECTRO-THERMAL CONVERSION', '将电能转换为热能', 60),
('F09-07', 'F09', '电机械转换', 'ELECTRO-MECHANICAL CONVERSION', '在电能与机械能之间转换', 70),
('F10-01', 'F10', '人工通断', 'MANUAL SWITCHING', '由人工操作接通或断开电路', 10),
('F10-02', 'F10', '电控通断', 'ELECTRICAL SWITCHING', '由电信号控制接通或断开电路', 20),
('F10-03', 'F10', '过流断开', 'OVERCURRENT INTERRUPTION', '电流超限时断开电路', 30),
('F10-04', 'F10', '过压断开', 'OVERVOLTAGE INTERRUPTION', '电压超限时断开或旁路', 40),
('F10-05', 'F10', '短路断开', 'SHORT-CIRCUIT INTERRUPTION', '短路故障时快速断开电路', 50),
('F10-06', 'F10', '隔离断开', 'ISOLATION', '为维护或安全提供可见断开点', 60),
('F11-01', 'F11', '电气搭接', 'ELECTRICAL BONDING', '在结构件之间建立低阻抗电气通路', 10),
('F11-02', 'F11', '接地', 'GROUNDING', '建立电位参考并提供故障电流通路', 20),
('F11-03', 'F11', '电磁屏蔽', 'EMI SHIELDING', '以导电界面阻断电磁场耦合', 30),
('F11-04', 'F11', '电磁滤波', 'EMI FILTERING', '衰减导线上传导的电磁干扰', 40),
('F11-05', 'F11', '雷电防护', 'LIGHTNING PROTECTION', '承受或旁路雷击能量', 50),
('F11-06', 'F11', '静电防护', 'ESD PROTECTION', '泄放或限制静电放电能量', 60),
('F12-01', 'F12', '物理量感知', 'PHYSICAL QUANTITY SENSING', '感知温度、压力、位置、速度、加速度、流量等物理量', 10),
('F12-02', 'F12', '目标探测', 'TARGET DETECTION', '探测特定目标、事件或辐射的存在', 20),
('F12-03', 'F12', '图像采集', 'IMAGE ACQUISITION', '采集可见光或其他波段图像', 30),
('F12-04', 'F12', '声音采集', 'AUDIO ACQUISITION', '将声波转换为电信号', 40),
('F12-05', 'F12', '状态监测', 'CONDITION MONITORING', '持续监测对象运行状态并输出结果', 50),
('F13-01', 'F13', '信号调理', 'SIGNAL CONDITIONING', '放大、滤波、隔离或匹配信号', 10),
('F13-02', 'F13', '信号变换', 'SIGNAL CONVERSION', '模数、数模、编码或解码变换', 20),
('F13-03', 'F13', '数据处理', 'DATA PROCESSING', '对数据进行运算、分析或格式化', 30),
('F13-04', 'F13', '逻辑控制', 'LOGIC CONTROL', '按逻辑规则产生控制输出', 40),
('F13-05', 'F13', '运动控制', 'MOTION CONTROL', '对位置、速度或力进行闭环控制', 50),
('F13-06', 'F13', '数据存储', 'DATA STORAGE', '保存数据供后续读取', 60),
('F13-07', 'F13', '计时同步', 'TIMING AND SYNCHRONIZATION', '提供时基或实现同步', 70),
('F14-01', 'F14', '信号传输', 'SIGNAL TRANSMISSION', '以导体传输模拟或离散信号', 10),
('F14-02', 'F14', '数据传输', 'DATA TRANSMISSION', '按通信协议传输数字数据', 20),
('F14-03', 'F14', '网络交换', 'NETWORK SWITCHING', '在网络节点之间转发数据', 30),
('F14-04', 'F14', '射频收发', 'RF TRANSMIT AND RECEIVE', '以电磁波辐射或接收信息', 40),
('F14-05', 'F14', '光信号传输', 'OPTICAL SIGNAL TRANSMISSION', '以光波导传输信息', 50),
('F14-06', 'F14', '协议转换', 'PROTOCOL CONVERSION', '在不同通信协议之间转换', 60),
('F14-07', 'F14', '信号分配', 'SIGNAL DISTRIBUTION', '分路、合路或耦合信号通道', 70),
('F15-01', 'F15', '照明', 'ILLUMINATION', '为观察或作业提供光照', 10),
('F15-02', 'F15', '光信号发射', 'OPTICAL SIGNALLING', '以光输出表达位置、状态或告警', 20),
('F15-03', 'F15', '光束成形', 'BEAM FORMING', '以光学面控制光强分布与投射角', 30),
('F15-04', 'F15', '反射成像', 'REFLECTION AND IMAGING', '以反射或折射形成观察像', 40),
('F15-05', 'F15', '滤光', 'LIGHT FILTERING', '选择性透过或阻挡特定波长', 50),
('F15-06', 'F15', '调光', 'LIGHT DIMMING', '调节光输出强度', 60),
('F16-01', 'F16', '信息显示', 'INFORMATION DISPLAY', '以图形或字符向人员呈现信息', 10),
('F16-02', 'F16', '状态指示', 'STATUS INDICATION', '以离散方式指示状态', 20),
('F16-03', 'F16', '告警提示', 'WARNING AND ALERTING', '以视觉或听觉提示异常或注意事项', 30),
('F16-04', 'F16', '人工操作', 'MANUAL OPERATION', '为人员提供操作输入界面', 40),
('F16-05', 'F16', '声音输出', 'AUDIO OUTPUT', '向人员输出语音或提示音', 50),
('F16-06', 'F16', '标识信息', 'MARKING AND IDENTIFICATION', '承载铭牌、标牌等标识信息', 60);

ALTER TABLE function_item   ENABLE TRIGGER trg_function_item_no_delete;
ALTER TABLE function_domain ENABLE TRIGGER trg_function_domain_no_delete;

-- ---------------------------------------------------------------------
-- 3 UG-EDS-FUN-001 引入的强制规则
-- ---------------------------------------------------------------------

-- FUN-SYS-004: 二级功能必须归属其一级功能域, 不得跨域组合
-- (代码前缀与 domain_code 必须一致, 防止装载或后续维护时错挂)
ALTER TABLE function_item
    ADD CONSTRAINT ck_function_item_domain_prefix
    CHECK (code LIKE domain_code || '-%');

-- FUN-SYS-003: 辅助功能不得与主功能重复
-- (uq_object_function 已保证同一功能不重复挂载, 此处补充语义说明)
COMMENT ON TABLE object_function IS
    'UG-EDS-FUN-001 §5.3: 每个设计对象有且仅有一个主功能(uq_object_function_primary 强制), '
    '辅助功能 0~N 个且不得与主功能重复(uq_object_function 强制)';

-- FUN-SYS-005: 新发基本图号时主功能必填
-- 设计族生效(ACTIVE)前必须指定主功能; PENDING 阶段允许暂缺
ALTER TABLE basic_drawing_family
    ADD CONSTRAINT ck_family_primary_function
    CHECK (status = 'PENDING' OR primary_function_id IS NOT NULL);

-- FUN-001 §5.2 固有作用原则 / §12: 设计族生效后功能分类锁定,
-- 不得因项目、机型、安装位置变化而更改
CREATE OR REPLACE FUNCTION dcms_lock_family_function() RETURNS trigger AS $$
BEGIN
    IF OLD.status IN ('ACTIVE', 'CLOSED')
       AND NEW.primary_function_id IS DISTINCT FROM OLD.primary_function_id THEN
        RAISE EXCEPTION
            'DCMS-FUN-§5.2: 设计族 % 已生效, 主功能已锁定。'
            ' 功能是固有作用, 不得因项目/机型/安装位置变化而修改;'
            ' 确属原判断错误须走 FUN-SYS-009 更正流程',
            OLD.basic_drawing_number USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_family_function_lock
    BEFORE UPDATE ON basic_drawing_family
    FOR EACH ROW EXECUTE FUNCTION dcms_lock_family_function();

-- FUN-001 §5.4: 功能与二级物理分类解耦 —
-- 二级功能不得限定适用的一级技术类别, 同一功能可由 T1/T2/T3/SW 对象实现
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'function_item'
                  AND column_name IN ('primary_class_code', 'physical_class_id')) THEN
        RAISE EXCEPTION
            'DCMS-FUN-§5.4: function_item 不得带一级/二级物理分类字段, 否则功能与形态耦合';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- 4 登记新增规则
-- ---------------------------------------------------------------------
INSERT INTO invariant_registry (code, statement_cn, enforced_by, enforcement_ref, test_ref) VALUES
('FUN-SYS-002', '每个设计对象有且仅有一个主功能', 'DB_CONSTRAINT',
 'uq_object_function_primary (与 INV-021 同一约束)', 'test_fun_sys_002'),
('FUN-SYS-004', '二级功能必须归属其一级功能域, 不得跨域组合', 'DB_CONSTRAINT',
 'ck_function_item_domain_prefix + FK function_item.domain_code', 'test_fun_sys_004'),
('FUN-SYS-005', '设计族生效前主功能必填', 'DB_CONSTRAINT',
 'ck_family_primary_function', 'test_fun_sys_005'),
('FUN-§5.2', '设计族生效后主功能锁定, 不得因项目/机型/位置变化而修改', 'DB_TRIGGER',
 'trg_family_function_lock', 'test_fun_lock'),
('FUN-§5.4', '功能分类与物理分类解耦, 功能词典不含形态维度', 'DB_STRUCTURE',
 'function_item 无 primary_class_code / physical_class_id 字段', 'test_fun_decoupled');

-- ---------------------------------------------------------------------
-- 5 装载自检
-- ---------------------------------------------------------------------
DO $$
DECLARE v_d int; v_i int; v_ph int; v_bad int;
BEGIN
    SELECT count(*) INTO v_d FROM function_domain;
    SELECT count(*) INTO v_i FROM function_item;
    SELECT count(*) INTO v_ph FROM function_item WHERE definition LIKE '[占位]%';
    SELECT count(*) INTO v_bad FROM function_item WHERE code NOT LIKE domain_code || '-%';

    IF v_d <> 16 THEN RAISE EXCEPTION '一级功能域应为 16 个, 实际 %', v_d; END IF;
    IF v_i <> 98 THEN RAISE EXCEPTION '二级功能应为 98 项, 实际 %', v_i; END IF;
    IF v_ph <> 0 THEN RAISE EXCEPTION '仍残留 % 条占位功能条目', v_ph; END IF;
    IF v_bad <> 0 THEN RAISE EXCEPTION '存在 % 条跨域挂载的二级功能', v_bad; END IF;

    RAISE NOTICE '功能分类词典装载完成: 一级功能域 % 个, 二级功能 % 项; 占位条目已清零', v_d, v_i;
    RAISE NOTICE '至此三份 EDS 支持规范(CLS/NAM/FUN)的受控词典已全部装载, 无占位残留';
END $$;
