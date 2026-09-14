"""Opt-in IMA fixture, using real domain services in one atomic transaction.

All identities are new. No supplied IDs, SQL, paths or existing approvers are
accepted. Simulated actors exist only for this fixture, then become inactive.
The immutable import receipt makes retries and concurrent calls idempotent.
"""
from __future__ import annotations

import io
import json
import secrets
import zipfile
from html import escape
from uuid import uuid4

from .. import audit, storage
from ..db import execute, fetch_all, fetch_one, scalar
from ..security import hash_password
from . import applicability, baselines, bom, externals, families, files

CODE = 'SIM-IMA-V1'
MARK = '【模拟数据】'
NOTICE = MARK + '仅用于 UG-DCMS 业务演示，不可用于制造、试验符合性或装机；审批记录为模拟流程。'
# key, Chinese name, physical class, controlled core term, level
PARTS = [
    ('IMA', 'IMA综合模块化航电设备', 'T2-14', 'T2-005', 'ASSEMBLY'),
    ('CHASSIS', 'IMA机箱组件', 'T1-02', 'T1-013', 'ASSEMBLY'),
    ('CPU', 'IMA计算模块', 'T2-13', 'T2-003', 'ASSEMBLY'),
    ('POWER', 'IMA电源模块', 'T2-13', 'T2-003', 'ASSEMBLY'),
    ('IO', 'IMA接口模块', 'T2-13', 'T2-003', 'ASSEMBLY'),
    ('BACKPLANE', 'IMA背板组件', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('CPU_A', 'IMA基础计算板', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('CPU_B', 'IMA增强计算板', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('PWR_CCA', 'IMA电源板', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('IO_CCA', 'IMA接口板', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('EXT_IO', 'IMA扩展接口板', 'T2-02', 'T2-002', 'ASSEMBLY'),
    ('HARNESS', 'IMA电源线束', 'T3-02', 'T3-002', 'ASSEMBLY'),
    ('HOUSING', 'IMA壳体', 'T1-02', 'T1-013', 'PART'),
    ('COVER', 'IMA盖板', 'T1-01', 'T1-003', 'PART'),
    ('BRACKET', 'IMA安装支架', 'T1-01', 'T1-006', 'PART'),
    ('HEATSINK', 'IMA导热板', 'T1-01', 'T1-001', 'PART'),
    ('PCB_CPU', 'IMA计算印制板', 'T2-01', 'T2-001', 'PART'),
    ('PCB_PWR', 'IMA电源印制板', 'T2-01', 'T2-001', 'PART'),
    ('PCB_IO', 'IMA接口印制板', 'T2-01', 'T2-001', 'PART'),
]
EXTERNALS = [
    ('SCREW', '紧固螺钉', 'T1'), ('CONN', '连接器', 'T2'),
    ('SOC', '计算处理器', 'T2'), ('FPGA', '可编程逻辑器件', 'T2'),
    ('RAM', '存储器', 'T2'), ('DCDC', '电源转换器', 'T2'),
    ('RES', '电阻器', 'T2'), ('CAP', '电容器', 'T2'), ('WIRE', '导线组件', 'T3'),
]
# parent, item, child, quantity, rule (None = common to A and B)
LINES = [
    ('IMA','10','CHASSIS',1,None), ('IMA','20','CPU',2,None),
    ('IMA','30','POWER',2,None), ('IMA','40','IO',1,None),
    ('IMA','50','BACKPLANE',1,None), ('IMA','60','HARNESS',2,None),
    ('CHASSIS','10','HOUSING',1,None), ('CHASSIS','20','COVER',1,None),
    ('CHASSIS','30','BRACKET',4,None), ('CHASSIS','40','SCREW',16,None),
    ('CPU','10','CPU_A',1,'A'), ('CPU','10','CPU_B',1,'B'),
    ('CPU','20','HEATSINK',1,None), ('CPU','30','SCREW',4,None),
    ('POWER','10','PWR_CCA',1,None), ('POWER','20','HEATSINK',1,None),
    ('POWER','30','SCREW',4,None),
    ('IO','10','IO_CCA',1,None), ('IO','20','EXT_IO',1,'B'),
    ('IO','30','SCREW',4,None),
    ('BACKPLANE','10','PCB_IO',1,None), ('BACKPLANE','20','CONN',8,None),
    ('BACKPLANE','30','CAP',12,None),
    ('CPU_A','10','PCB_CPU',1,None), ('CPU_A','20','SOC',1,None),
    ('CPU_A','30','RAM',2,None), ('CPU_A','40','CAP',24,None),
    ('CPU_B','10','PCB_CPU',1,None), ('CPU_B','20','SOC',2,None),
    ('CPU_B','30','RAM',4,None), ('CPU_B','40','CAP',32,None),
    ('CPU_B','50','FPGA',1,None),
    ('PWR_CCA','10','PCB_PWR',1,None), ('PWR_CCA','20','DCDC',2,None),
    ('PWR_CCA','30','CAP',16,None), ('PWR_CCA','40','RES',12,None),
    ('IO_CCA','10','PCB_IO',1,None), ('IO_CCA','20','FPGA',1,None),
    ('IO_CCA','30','CONN',4,None), ('IO_CCA','40','RES',16,None),
    ('EXT_IO','10','PCB_IO',1,None), ('EXT_IO','20','CONN',6,None),
    ('EXT_IO','30','CAP',8,None),
    ('HARNESS','10','WIRE',6,None), ('HARNESS','20','CONN',2,None),
]
SOFTWARE = [
    ('PLATFORM','IMA平台软件','SOFTWARE',['CPU_A','CPU_B']),
    ('IO_LOGIC','IMA接口逻辑','FIRMWARE',['IO_CCA','EXT_IO']),
    ('POWER_MON','IMA电源监控软件','FIRMWARE',['PWR_CCA']),
]


def status(conn):
    receipt = fetch_one(conn, 'SELECT * FROM simulation_dataset WHERE dataset_code=%s', (CODE,))
    return {'dataset_code': CODE, 'notice': NOTICE, 'imported': receipt is not None,
            'receipt': receipt, 'planned': {'parts':len(PARTS), 'externals':len(EXTERNALS),
            'bom_lines':len(LINES), 'configurations':2, 'software':len(SOFTWARE)}}


def _mark_request(conn, req):
    execute(conn, 'UPDATE approval_request SET title=%s||title WHERE id=%s', (MARK, req['id']))


def _actor(conn, suffix, role, admin, batch):
    username = f'sim_ima_{suffix}_{batch}'
    row = fetch_one(conn, """INSERT INTO app_user(username,full_name,password_hash,created_by)
        VALUES(%s,%s,%s,%s) RETURNING id""", (username, MARK + 'IMA模拟' + suffix,
        hash_password(secrets.token_urlsafe(40)), admin['user_id']))
    execute(conn, 'INSERT INTO user_role(user_id,role_code) VALUES(%s,%s)', (row['id'],role))
    return {'user_id':str(row['id']), 'username':username, 'client_ip':admin.get('client_ip'),
            'session_id':admin['session_id']}


def _document(conn, key, title, kind, content, extension, mime, author, approver, keys, docs):
    number = f'{CODE}-{key}'
    files.create_file(conn, file_number=number, file_type_code=kind,
                      title_cn=MARK+title, title_en='SIMULATED '+key, actor=author)
    rev = files.create_revision(conn, number, NOTICE, author)
    # Unique filename per attempt: a killed process can leave unreferenced bytes,
    # but a retry cannot overwrite any previous attachment.
    filename = f'{number}-模拟-{uuid4().hex[:12]}.{extension}'
    storage_key = storage.make_key(number, rev['revision_number'], 'PRIMARY_NATIVE', filename)
    # Track only keys that this call can newly create, never existing files.
    if storage.exists(storage_key):
        raise ValueError('模拟附件路径冲突，未覆盖已有文件')
    keys.append(storage_key)
    attachment = files.upload_attachment(conn, str(rev['id']), role='PRIMARY_NATIVE', filename=filename,
        mime_type=mime, content=content, actor=author)
    _mark_request(conn, files.submit_revision(conn, str(rev['id']), approver['user_id'], author))
    files.release_revision(conn, str(rev['id']), NOTICE, approver)
    doc = {'file_number':number, 'revision_id':str(rev['id']),
           'target':number+' Rev.'+rev['revision_number'], 'attachment_id':str(attachment['id']),
           'filename':filename, 'sha256':attachment['sha256']}
    docs.append(doc)
    return doc


def import_dataset(conn, admin):
    # Non-blocking lock: double clicks get a clear response instead of consuming
    # all application connections while a large atomic import is in progress.
    if not scalar(conn, 'SELECT pg_try_advisory_xact_lock(811038)'):
        raise ValueError('IMA模拟数据正在导入，请稍后刷新查看结果')
    existing = status(conn)
    if existing['imported']:
        return existing
    keys = []
    try:
        # A savepoint includes deferred constraint validation, so physical files
        # are cleaned on domain, DB and commit validation errors alike.
        with conn.transaction():
            result = _populate(conn, admin, keys)
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            conn.execute('SET CONSTRAINTS ALL DEFERRED')
        return result
    except BaseException:
        for key in keys:
            storage.delete(key)
        raise


def _populate(conn, admin, keys):
    # Check all fixed namespaces before writing anything. Never reuse a
    # similarly named existing record, even if it looks like a demo record.
    for table, column in [('namespace','code'), ('design_file','file_number'),
            ('software_object','software_number'), ('configuration_context','context_code'),
            ('applicability_rule','rule_code'), ('external_part','external_part_number')]:
        if scalar(conn, f'SELECT EXISTS(SELECT 1 FROM {table} WHERE {column} LIKE %s)', (CODE+'%',)):
            raise ValueError(f'编号前缀 {CODE} 已有数据但无完整导入记录；为保护已有数据已停止，请更换环境或联系管理员核对')
    batch = uuid4().hex[:12]
    author = _actor(conn, '编制人', 'ENGINEER', admin, batch)
    approver = _actor(conn, '批准人', 'CONFIGURATION_MANAGER', admin, batch)
    actors = [author['user_id'], approver['user_id']]
    docs, parts, external, sw, snapshots, baselines_map = [], {}, {}, {}, {}, {}
    execute(conn, "INSERT INTO namespace(code,name_cn,name_en,kind) VALUES(%s,%s,%s,'OTHER')",
            (CODE, MARK+'IMA虚构供应来源', 'SIMULATED IMA'))
    for key, name, physical, term, level in PARTS:
        function_code = ('F04-01' if key in ('CHASSIS','HOUSING','COVER') else
            'F01-03' if key=='BRACKET' else 'F07-01' if key=='HEATSINK' else
            'F09-01' if key in ('POWER','PWR_CCA','PCB_PWR') else 'F08-01' if key=='HARNESS' else
            'F13-03' if key in ('IMA','CPU','CPU_A','CPU_B','PCB_CPU') else 'F14-02')
        function_id = scalar(conn, "SELECT id FROM function_item WHERE code=%s AND status='ACTIVE'", (function_code,))
        if not function_id:
            raise ValueError('模拟数据所需功能分类已停用：'+function_code)
        pc = scalar(conn, "SELECT id FROM physical_class WHERE code=%s AND status='ACTIVE'", (physical,))
        ct = scalar(conn, "SELECT id FROM naming_core_term WHERE code=%s AND status='ACTIVE'", (term,))
        if not pc or not ct:
            raise ValueError('模拟数据所需受控分类或核心词已停用：'+physical+' / '+term)
        fam = families.create_family(conn, primary_class_code=physical[:2], physical_class_id=str(pc),
            object_level_code=level, core_term_id=str(ct), qualifier_1_id=None, qualifier_2_id=None,
            primary_function_id=str(function_id), family_definition=MARK+name+'；'+NOTICE,
            allowed_variation=MARK+'仅演示范围内的尺寸或装配变化', excluded_variation=MARK+'真实产品设计',
            new_family_reason=NOTICE, classification_note=MARK+CODE, actor=author)
        fid = str(fam['id'])
        _mark_request(conn, families.submit_family(conn, fid, approver['user_id'], author))
        families.approve_family(conn, fid, NOTICE, approver)
        pn = families.create_dash(conn, fid, formal_name_cn=MARK+name, formal_name_en='SIMULATED IMA '+key,
            object_level_code=level, difference_summary=NOTICE, actor=author)
        code = pn['full_part_number']
        oid = str(scalar(conn, 'SELECT design_object_id FROM part_number WHERE id=%s', (pn['id'],)))
        parts[key] = {'part_number':code, 'id':str(pn['id']), 'object_id':oid, 'family_id':fid, 'name':MARK+name}
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="660" viewBox="0 0 1000 660">
<rect width="1000" height="660" fill="white"/><g font-family="sans-serif" fill="#172b4d">
<text x="35" y="50" font-size="26">{escape(MARK+name)}</text>
<text x="35" y="86" font-size="20">{code} / Rev.00 — SIMULATED DRAWING</text>
<rect x="190" y="170" width="620" height="260" fill="#eef4fa" stroke="#172b4d" stroke-width="3"/>
<circle cx="220" cy="200" r="10"/><circle cx="780" cy="200" r="10"/>
<circle cx="220" cy="400" r="10"/><circle cx="780" cy="400" r="10"/>
<text x="280" y="300" font-size="28">{escape(key)} · 示意外形</text>
<text x="35" y="520" font-size="20">几何、比例和接口均为虚构；无制造尺寸或材料依据。</text>
<text x="35" y="560" font-size="20" fill="#bb2222">模拟数据 — 不可用于制造、试验符合性或装机</text>
<text x="35" y="610" font-size="18">UG-DCMS / SIM-IMA-V1 / 模拟设计定义附件</text></g></svg>'''
        doc = _document(conn, key+'-DWG', name+'示意图', 'DWG', svg.encode(), 'svg',
                        'image/svg+xml', author, approver, keys, docs)
        parts[key]['definition'] = doc
        files.link_definition(conn, code, doc['file_number'], 'PRIMARY_DEFINITION', NOTICE, author)
    for key, name, cls in EXTERNALS:
        code = CODE+'::'+CODE+'-'+key
        externals.create_external(conn, namespace_code=CODE, external_part_number=CODE+'-'+key,
            name_cn=MARK+name, name_en='SIMULATED '+key, manufacturer_code=None, external_class_code=cls,
            project_code=CODE, project_applicability=MARK+'IMA构型A/B', project_evaluation_basis=NOTICE, actor=author)
        st = externals.add_technical_state(conn, code, supplier_revision='SIM-R00',
            supplier_document=MARK+CODE+'-供应规格-'+key, supplier_document_date=None, notes=NOTICE, actor=author)
        _mark_request(conn, externals.submit_technical_state(conn, str(st['id']), approver['user_id'], author))
        externals.accept_technical_state(conn, str(st['id']), NOTICE, approver)
        ep = externals.get_external(conn, code)
        control = ep['project_controls'][0]
        _mark_request(conn, externals.submit_project_control(conn, str(control['id']), approver['user_id'], author))
        externals.approve_project_control(conn, str(control['id']), NOTICE, approver)
        external[key] = {'object_code':code, 'state_target':code+' TS1', 'state_id':str(st['id'])}
    contexts = {}
    for config in ('A','B'):
        contexts[config] = applicability.create_context(conn, code=CODE+'-'+config,
            name=MARK+'IMA构型'+config, attributes={'simulation':CODE,'ima_variant':config},
            description=NOTICE+('基础计算配置' if config=='A' else '增强计算及扩展接口配置'), actor=author)
        applicability.create_rule(conn, code=CODE+'-ONLY-'+config, name=MARK+'仅IMA构型'+config,
            expression={'all':[{'field':'simulation','op':'eq','value':CODE},
                              {'field':'ima_variant','op':'eq','value':config}]}, description=NOTICE, actor=author)
    for parent,item,child,qty,rule in LINES:
        child_code = parts[child]['part_number'] if child in parts else external[child]['object_code']
        line = bom.add_line(conn, parts[parent]['object_id'], item_number=item, child_object_code=child_code,
            quantity=qty, unit_code='EA', reference_designator='SIM-'+parent+'-'+item,
            effectivity=MARK+('A/B共用' if not rule else '构型'+rule), notes=NOTICE, actor=author)
        if rule:
            applicability.assign_rule(conn, str(line['id']), CODE+'-ONLY-'+rule, author)
    for key,name,kind,hardware in SOFTWARE:
        number=CODE+'-SW-'+key
        externals.create_software(conn, software_number=number, name_cn=MARK+name,
                                  name_en='SIMULATED '+key, software_type=kind, actor=author)
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('模拟说明.txt',NOTICE+'\n此包仅含演示元数据，无可运行固件。')
            z.writestr('manifest.json',json.dumps({'simulation':True,'software':number,'version':'SIM-1.0.0',
                'hardware':[parts[h]['part_number'] for h in hardware],'hardware_version':'R00'},ensure_ascii=False))
        version=externals.add_version_package(conn, number, version='SIM-1.0.0', build=CODE,
            filename=number+'-模拟.zip', mime_type='application/zip', content=buffer.getvalue(), actor=author)
        package=externals.get_version_package(conn,str(version['id']))
        keys.append(package['package_storage_key'])
        for hw in hardware:
            externals.add_hardware_compatibility(conn,str(version['id']),parts[hw]['part_number'],'R00',NOTICE,author)
        _mark_request(conn, externals.submit_version(conn,str(version['id']),approver['user_id'],author))
        externals.release_version(conn,str(version['id']),NOTICE,approver)
        sw[key]={'software_number':number,'version_id':str(version['id']), 'target':number+' SIM-1.0.0',
                 'hardware':hardware,'sha256':version['hash_sha256']}
    for key in dict.fromkeys(line[0] for line in LINES):
        snapshots[key]=bom.create_snapshot(conn,parts[key]['object_id'],author)
    resolved={}
    for config,context in contexts.items():
        snap=applicability.create_resolved_snapshot(conn,parts['IMA']['object_id'],context=context['attributes'],
                                                   context_id=str(context['id']),actor=author)
        frozen_lines=fetch_all(conn,'SELECT * FROM resolved_bom_snapshot_line WHERE resolved_bom_snapshot_id=%s ORDER BY sort_path',(snap['id'],))
        payload={'notice':NOTICE,'snapshot':snap,'lines':frozen_lines}
        doc=_document(conn,'CONFIG-'+config,'IMA构型'+config+'冻结清单','BOMDOC',
            json.dumps(payload,ensure_ascii=False,indent=2,default=str).encode(),'json','application/json',author,approver,keys,docs)
        resolved[config]={'snapshot':snap,'document':doc,'attributes':context['attributes']}
    # Child baselines before parent; every assembly locks its own Master BOM.
    for key in reversed(parts):
        p=parts[key]
        bl=baselines.create_baseline(conn,p['part_number'],NOTICE,author,project_code=CODE,scope_note=NOTICE)
        bid=str(bl['id'])
        def add(kind,target,role=None):
            baselines.add_item(conn,bid,item_type=kind,target=target,item_role=role,notes=NOTICE,actor=author)
        add('FILE_REVISION',p['definition']['target'],'PRIMARY_DEFINITION')
        if key in snapshots:
            add('BOM_SNAPSHOT',snapshots[key]['snapshot_number'])
        for ext in dict.fromkeys(line[2] for line in LINES if line[0]==key and line[2] in external):
            add('EXTERNAL_TECHNICAL_STATE',external[ext]['state_target'])
        for software in sw.values():
            if key in software['hardware'] or key=='IMA':
                add('SOFTWARE_VERSION',software['target'])
        if key=='IMA':
            for ext in external.values():
                add('EXTERNAL_TECHNICAL_STATE',ext['state_target'])
            for item in resolved.values():
                add('FILE_REVISION',item['document']['target'],'SUPPORTING_DEFINITION')
            report={'notice':NOTICE,'parts':parts,'child_baselines':baselines_map,'master_snapshots':snapshots,
                    'configurations':resolved,'software':sw,'external_states':external}
            trace=_document(conn,'TRACE','IMA设计基线追溯清单','PSCD',
                json.dumps(report,ensure_ascii=False,indent=2,default=str).encode(),'json','application/json',author,approver,keys,docs)
            add('FILE_REVISION',trace['target'],'SUPPORTING_DEFINITION')
            rows=''.join('<tr><td>'+escape(p['part_number'])+'</td><td>'+escape(p['name'])+'</td></tr>' for p in parts.values())
            manual=f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>模拟IMA图纸手册</title>
<style>body{{font:16px sans-serif;max-width:1000px;margin:40px auto;line-height:1.7}}td,th{{border:1px solid #aaa;padding:8px}}table{{border-collapse:collapse}}aside{{color:#b22}}</style>
<h1>【模拟数据】IMA图纸手册 R00</h1><aside>{NOTICE}</aside>
<h2>构型定义</h2><p>A：每台设备两套基础计算模块；B：两套增强计算模块，并增加扩展接口板。机箱、电源、背板、线束及紧固件共用。</p>
<p>同一顶层P/N维护一套主BOM，计算模块项号10按规则互斥选择计算板。所有软件版本固定为SIM-1.0.0，适装硬件版本R00。</p>
<h2>图纸目录</h2><table><tr><th>件号</th><th>模拟名称</th></tr>{rows}</table>
<h2>冻结依据</h2><p>顶层设计基线包含两种构型的冻结清单、软件版本、供应商技术状态和下级基线追溯附件。所有图纸为虚构外形示意；本手册不证明任何真实产品性能或适航符合性。</p></html>'''
            manual_doc=_document(conn,'MANUAL','IMA图纸手册','PSCD',manual.encode(),'html','text/html',author,approver,keys,docs)
            add('FILE_REVISION',manual_doc['target'],'SUPPORTING_DEFINITION')
        _mark_request(conn,baselines.submit(conn,bid,approver['user_id'],author))
        released=baselines.release(conn,bid,NOTICE,approver)
        baselines_map[key]={'id':bid,'part_number':p['part_number'],**released}
    execute(conn,'UPDATE app_user SET is_active=false WHERE id=ANY(%s::uuid[])',(actors,))
    manifest={'notice':NOTICE,'parts':parts,'externals':external,'software':sw,'documents':docs,
              'baselines':baselines_map,'configurations':resolved,'actors':actors,
              'counts':{'parts':len(parts),'externals':len(external),'bom_lines':len(LINES),
                        'software':len(sw),'documents':len(docs),'baselines':len(baselines_map),'configurations':2},
              'top_part_number':parts['IMA']['part_number']}
    execute(conn,'INSERT INTO simulation_dataset(dataset_code,manifest,imported_by) VALUES(%s,%s::jsonb,%s)',
            (CODE,json.dumps(manifest,ensure_ascii=False,default=str),admin['user_id']))
    audit.write(conn,action='SIMULATION_DATASET_IMPORT',user_id=str(admin['user_id']),username=admin['username'],
                object_type='SIMULATION_DATASET',object_code=CODE,new_value=manifest['counts'],reason=NOTICE,
                session_id=str(admin.get('session_id')),client_ip=admin.get('client_ip'))
    return status(conn)
