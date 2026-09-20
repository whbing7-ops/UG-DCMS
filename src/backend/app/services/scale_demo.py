"""Trusted additive 10-project simulation. Domain services, atomicity and receipts."""
import io
import json
import logging
import zipfile
from collections import defaultdict
from html import escape
from uuid import uuid4
from .. import audit, storage
from ..db import execute, fetch_all, fetch_one, scalar
from . import applicability, baselines, bom, externals, families, files, ima_demo

CODE='SIM-SCALE-V1'
MARK=ima_demo.MARK
NOTICE=ima_demo.NOTICE
COUNTS=dict(families=1000,parts=10000,software=500,externals=5000,projects=10)
log=logging.getLogger(__name__)


def builtin_dataset():
    names=['IMA综合航电','客舱PSU','通信管理','电源分配','数据采集','显示控制','导航接口','座舱记录','环境监控','机载网络']
    projects=[dict(code=f'{CODE}-P{p:02d}',name=MARK+n+'测试项目',index=p) for p,n in enumerate(names,1)]
    fs,ps,es,ss,lines=[],[],[],[],[]
    def part(f,p):return f'F{f:04d}-D{p:02d}'
    mechanical=[('壳体','T1-02','T1-013','F04-01'),('盖板','T1-01','T1-003','F04-01'),
        ('安装支架','T1-01','T1-006','F01-03'),('导热板','T1-01','T1-001','F07-01'),('印制板','T2-01','T2-001','F14-02')]
    for f in range(1,1001):
        if f==1:name,pc,ct,fn,level='综合设备','T2-14','T2-005','F13-03','ASSEMBLY'
        elif f<=10:name,pc,ct,fn,level='功能模块','T2-13','T2-003','F13-03','ASSEMBLY'
        elif f<=110:name,pc,ct,fn,level='接口电路板','T2-02','T2-002','F14-02','ASSEMBLY'
        else:name,pc,ct,fn=mechanical[(f-111)%5];level='PART'
        # Numeric labels such as 0304 accidentally match controlled material terms.
        # Keep IDs numeric, but use Chinese ordinal digits in formal names.
        ordinal=f'{f:04d}'.translate(str.maketrans('0123456789','零一二三四五六七八九'))
        fs.append([f'F{f:04d}',name+ordinal,pc,ct,fn,level])
        ps.extend([[part(f,p),f'F{f:04d}',p] for p in range(1,11)])
    for p in range(1,11):
        for f in range(2,1001):lines.append([part(1 if f<=10 else 2+(f-11)//110,p),f'I{f:04d}',part(f,p),1])
        for n in range(1,501):
            e=(p-1)*500+n;key=f'E{e:05d}';cls=['T1','T2','T3'][(e-1)%3]
            es.append([key,{'T1':'标准紧固件','T2':'电子器件','T3':'电气连接件'}[cls]+f'{e:05d}',cls,p])
            lines.append([part(2+(n-1)%9,p),f'E{n:04d}',key,1+n%4])
        if p>1:lines.extend([[part(1,p),'COMMON-PART',part(1000,1),1],[part(1,p),'COMMON-EXT','E00001',2]])
        for n in range(1,51):
            s=(p-1)*50+n;hw=[part(10+n,p),part(60+n,p)]
            if s==1:hw.extend(part(1,j) for j in range(1,11))
            ss.append([f'S{s:04d}',f'功能软件{s:04d}',['FIRMWARE','CONFIG_DATA','LOADABLE'][(s-1)%3],p,hw])
    return dict(dataset_code=CODE,notice=NOTICE,counts=COUNTS,projects=projects,families=fs,parts=ps,externals=es,software=ss,bom_lines=lines)


def status(conn):
    receipt=fetch_one(conn,'SELECT * FROM simulation_dataset WHERE dataset_code=%s',(CODE,))
    return dict(dataset_code=CODE,notice=NOTICE,planned=COUNTS,imported=receipt is not None,receipt=receipt)


def import_dataset(conn,admin,dataset=None,progress=None):
    if not scalar(conn,'SELECT pg_try_advisory_xact_lock(811038)'):raise ValueError('模拟数据正在导入，请稍后刷新备份页查看结果')
    existing=status(conn)
    if existing['imported']:return existing
    keys=[]
    try:
        with conn.transaction():
            result=_populate(conn,admin,keys,dataset or builtin_dataset(),progress)
            conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
            conn.execute('SET CONSTRAINTS ALL DEFERRED')
        return result
    except BaseException:
        for key in keys:storage.delete(key)
        raise


def _populate(conn,admin,keys,data,progress=None):
    emit=progress or (lambda *args: None)
    for table,column in [('namespace','code'),('design_file','file_number'),('software_object','software_number'),
        ('configuration_context','context_code'),('external_part','external_part_number'),
        ('external_part_project_control','project_code'),('design_baseline','project_code'),('basic_drawing_family','classification_note')]:
        prefix=MARK+CODE if table=='basic_drawing_family' else CODE
        if scalar(conn,f'SELECT EXISTS(SELECT 1 FROM {table} WHERE {column} LIKE %s)',(prefix+'%',)):
            raise ValueError(f'{CODE} 前缀已存在但没有完整导入记录；已停止，未覆盖原记录')
    batch=uuid4().hex[:12]
    author=ima_demo._actor(conn,'编制人','ENGINEER',admin,batch,prefix='sim_scale',label='规模测试')
    approver=ima_demo._actor(conn,'批准人','CONFIGURATION_MANAGER',admin,batch,prefix='sim_scale',label='规模测试')
    approver['reviewer']=ima_demo._actor(conn,'审核人','ENGINEER',admin,batch,prefix='sim_scale',label='规模测试')
    execute(conn,"INSERT INTO namespace(code,name_cn,name_en,kind) VALUES(%s,%s,%s,'OTHER')",(CODE,MARK+'规模测试虚构供应来源','SIMULATED SCALE'))
    parts,ext,software,projects={},{},{},{}
    docs=[];by_family=defaultdict(list)
    for key,family,p in data['parts']:by_family[family].append((key,p))
    controlled={t:{r['code']:str(r['id']) for r in fetch_all(conn,f"SELECT id,code FROM {t} WHERE status='ACTIVE'")}
                for t in ['physical_class','naming_core_term','function_item']}
    emit('设计族、件号及设计资料',0,1000,0,20)
    for index,(key,name,physical,term,func,level) in enumerate(data['families'],1):
        try:pc,ct,fn=controlled['physical_class'][physical],controlled['naming_core_term'][term],controlled['function_item'][func]
        except KeyError as exc:raise ValueError('所需受控分类或核心词已停用：'+str(exc)) from exc
        f=families.create_family(conn,primary_class_code=physical[:2],physical_class_id=pc,object_level_code=level,
            core_term_id=ct,qualifier_1_id=None,qualifier_2_id=None,primary_function_id=fn,family_definition=MARK+name+'；'+NOTICE,
            allowed_variation=MARK+'十个项目变型，接口和尺寸以模拟图纸手册为准',excluded_variation=MARK+'真实设计',
            new_family_reason=NOTICE,classification_note=MARK+CODE+' '+key,actor=author)
        fid=str(f['id'])
        ima_demo._mark_request(conn,families.submit_family(conn,fid,approver['user_id'],author))
        families.approve_family(conn,fid,NOTICE,approver)
        for pk,p in by_family[key]:
            pn=families.create_dash(conn,fid,formal_name_cn=MARK+name+f' 项目{p:02d}变型',formal_name_en='SIMULATED '+pk,
                object_level_code=level,difference_summary=NOTICE,actor=author)
            oid=str(scalar(conn,'SELECT design_object_id FROM part_number WHERE id=%s',(pn['id'],)))
            parts[pk]=dict(part_number=pn['full_part_number'],id=str(pn['id']),object_id=oid,family=key,project=p,name=MARK+name)
        rows=''.join('<tr><td>'+escape(parts[pk]['part_number'])+'</td><td>'+str(p)+'</td></tr>' for pk,p in by_family[key])
        content=f'<!doctype html><meta charset="utf-8"><h1>{escape(MARK+name)} 模拟图纸手册 R00</h1><p>{NOTICE}</p><p>十个变型件号对应表，仅供模拟，无制造尺寸或性能依据。</p><table><tr><th>件号</th><th>项目</th></tr>{rows}</table>'
        doc=ima_demo._document(conn,key+'-MANUAL',name+'图纸手册','PSCD',content.encode(),'html','text/html',author,approver,keys,docs,prefix=CODE)
        for pk,_ in by_family[key]:
            parts[pk]['definition']=doc
            files.link_definition(conn,parts[pk]['part_number'],doc['file_number'],'PRIMARY_DEFINITION',NOTICE,author)
        emit('设计族、件号及设计资料',index,1000,0,20)
        if index%100==0:log.warning('%s families %s/1000',CODE,index)
    for project in data['projects']:
        p=project['index'];root=parts[f'F0001-D{p:02d}']
        c=applicability.create_context(conn,code=project['code'],name=project['name'],attributes={'simulation':CODE,'project':project['code']},description=NOTICE,actor=author)
        projects[p]={**project,'root_part_number':root['part_number'],'context_id':str(c['id'])}
    emit('外部件及项目准入',0,5000,20,20)
    for index,(key,name,cls,p) in enumerate(data['externals'],1):
        number=CODE+'-'+key;code=CODE+'::'+number
        externals.create_external(conn,namespace_code=CODE,external_part_number=number,name_cn=MARK+name,name_en='SIMULATED '+key,
            manufacturer_code=None,external_class_code=cls,project_code=None,project_applicability=None,project_evaluation_basis=None,actor=author)
        st=externals.add_technical_state(conn,code,supplier_revision='SIM-R00',supplier_document=MARK+number,supplier_document_date=None,notes=NOTICE,actor=author)
        ima_demo._mark_request(conn,externals.submit_technical_state(conn,str(st['id']),approver['user_id'],author))
        externals.accept_technical_state(conn,str(st['id']),NOTICE,approver)
        uses=list(projects) if key=='E00001' else [p]
        for project_id in uses:
            c=externals.add_project_control(conn,code,projects[project_id]['code'],NOTICE,NOTICE,author)
            ima_demo._mark_request(conn,externals.submit_project_control(conn,str(c['id']),approver['user_id'],author))
            externals.approve_project_control(conn,str(c['id']),NOTICE,approver)
        ext[key]=dict(object_code=code,target=code+' TS1',projects=uses)
        emit('外部件及项目准入',index,5000,20,20)
        if index%500==0:log.warning('%s externals %s/5000',CODE,index)
    emit('软件及硬件关联',0,500,40,5)
    for index,(key,name,kind,p,hardware) in enumerate(data['software'],1):
        number=CODE+'-'+key
        externals.create_software(conn,software_number=number,name_cn=MARK+name,name_en='SIMULATED '+key,software_type=kind,actor=author)
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('模拟说明.txt',NOTICE+'\n仅含元数据，无可运行固件。')
            z.writestr('manifest.json',json.dumps(dict(simulation=True,software=number,hardware=[parts[h]['part_number'] for h in hardware]),ensure_ascii=False))
        v=externals.add_version_package(conn,number,version='SIM-1.0.0',build=CODE,filename=number+'.zip',mime_type='application/zip',content=buf.getvalue(),actor=author)
        package=externals.get_version_package(conn,str(v['id']));keys.append(package['package_storage_key'])
        for h in hardware:externals.add_hardware_compatibility(conn,str(v['id']),parts[h]['part_number'],'R00',NOTICE,author)
        ima_demo._mark_request(conn,externals.submit_version(conn,str(v['id']),approver['user_id'],author))
        externals.release_version(conn,str(v['id']),NOTICE,approver)
        software[key]=dict(target=number+' SIM-1.0.0',projects=list(projects) if key=='S0001' else [p],hardware=hardware)
        emit('软件及硬件关联',index,500,40,5)
    children=defaultdict(list)
    emit('建立多层 BOM',0,len(data['bom_lines']),45,15)
    for index,(parent,item,child,qty) in enumerate(data['bom_lines'],1):
        code=parts[child]['part_number'] if child in parts else ext[child]['object_code']
        bom.add_line(conn,parts[parent]['object_id'],item_number=item,child_object_code=code,quantity=qty,unit_code='EA',
            reference_designator='SIM-'+item,effectivity=MARK+('项目共用' if item.startswith('COMMON') else '项目变型'),notes=NOTICE,actor=author)
        children[parent].append(child)
        emit('建立多层 BOM',index,len(data['bom_lines']),45,15)
    # Child baselines first; common leaf P/Ns also release before any parent.
    emit('审批及发布设计基线',0,len(parts),60,39)
    for index,key in enumerate(sorted(parts,key=lambda k:int(k[1:5]),reverse=True),1):
        p=parts[key];project=projects[p['project']]
        bl=baselines.create_baseline(conn,p['part_number'],NOTICE,author,project_code=project['code'],scope_note=NOTICE);bid=str(bl['id'])
        def add(kind,target,role=None):baselines.add_item(conn,bid,item_type=kind,target=target,item_role=role,notes=NOTICE,actor=author)
        add('FILE_REVISION',p['definition']['target'],'PRIMARY_DEFINITION')
        if key in children:
            snap=bom.create_snapshot(conn,p['object_id'],author);add('BOM_SNAPSHOT',snap['snapshot_number'])
            for child in children[key]:
                if child in ext:add('EXTERNAL_TECHNICAL_STATE',ext[child]['target'])
        for s in software.values():
            if key in s['hardware'] or (p['family']=='F0001' and p['project'] in s['projects']):add('SOFTWARE_VERSION',s['target'])
        if p['family']=='F0001':
            report=dict(notice=NOTICE,project=project,parts={k:v for k,v in parts.items() if v['project']==p['project'] or k=='F1000-D01'},
                externals={k:v for k,v in ext.items() if p['project'] in v['projects']},software={k:v for k,v in software.items() if p['project'] in v['projects']})
            doc=ima_demo._document(conn,f'P{p["project"]:02d}-CATALOG',project['name']+'冻结目录','BOMDOC',json.dumps(report,ensure_ascii=False,default=str).encode(),
                'json','application/json',author,approver,keys,docs,prefix=CODE)
            add('FILE_REVISION',doc['target'],'SUPPORTING_DEFINITION');project['catalog_file']=doc['file_number'];project['baseline_id']=bid
        ima_demo._mark_request(conn,baselines.submit(conn,bid,approver['user_id'],author))
        baselines.release(conn,bid,NOTICE,approver);p['baseline_id']=bid
        emit('审批及发布设计基线',index,len(parts),60,39)
        if index%1000==0:log.warning('%s released baselines %s/10000',CODE,index)
    actors=[author['user_id'],approver['reviewer']['user_id'],approver['user_id']]
    execute(conn,'UPDATE app_user SET is_active=false WHERE id=ANY(%s::uuid[])',(actors,))
    counts={**COUNTS,'bom_lines':len(data['bom_lines']),'documents':len(docs),'baselines':len(parts),
        'project_controls':sum(len(e['projects']) for e in ext.values()),'hardware_links':sum(len(s['hardware']) for s in software.values())}
    manifest=dict(notice=NOTICE,counts=counts,projects=list(projects.values()),actors=actors)
    execute(conn,'INSERT INTO simulation_dataset(dataset_code,manifest,imported_by) VALUES(%s,%s::jsonb,%s)',(CODE,json.dumps(manifest,ensure_ascii=False),admin['user_id']))
    audit.write(conn,action='SIMULATION_DATASET_IMPORT',user_id=str(admin['user_id']),username=admin['username'],object_type='SIMULATION_DATASET',
        object_code=CODE,new_value=counts,reason=NOTICE,session_id=str(admin.get('session_id')),client_ip=admin.get('client_ip'))
    return status(conn)
