"""Applicant-owned drafts for every implemented approval object.

Always lock the business object before its request. All submission, decision,
editing and withdrawal paths share that order, preventing approve/withdraw races.
"""
import hashlib
import json
from datetime import date
from uuid import uuid4
from .. import audit, storage
from ..db import fetch_one, fetch_all, execute

TYPES = {
    'BASIC_DRAWING_FAMILY': ('basic_drawing_family','PENDING','设计族'),
    'FILE_REVISION': ('file_revision','WORKING','文件版次'),
    'DESIGN_BASELINE': ('design_baseline','DRAFT','设计基线'),
    'EXTERNAL_TECHNICAL_STATE': ('external_technical_state','DRAFT','外部件技术状态'),
    'EXTERNAL_PROJECT_CONTROL': ('external_part_project_control','DRAFT','外部件项目准入'),
    'SOFTWARE_VERSION': ('software_version','DRAFT','软件版本'),
}
# field, label, required, max length; only these business fields may be changed.
FIELDS = {
 'BASIC_DRAWING_FAMILY': [('primary_class_code','一级类别',True,10),('physical_class_id','二级分类',True,36),
    ('object_level_code','对象层级',True,20),('core_term_id','核心实体词',True,36),
    ('qualifier_1_id','限定词一',False,36),('qualifier_2_id','限定词二',False,36),
    ('primary_function_id','主功能',True,36),('family_definition','设计族定义',True,1000),
    ('allowed_variation','允许变化',True,1000),('excluded_variation','排除变化',True,1000),
    ('new_family_reason','新建理由',True,500),('classification_note','分类说明',False,500)],
 'FILE_REVISION': [('change_summary','更改说明',True,2000),('revision_date','版次日期',False,10)],
 'DESIGN_BASELINE': [('reason','建立理由',True,2000),('project_code','项目编号',True,128),
    ('scope_note','适用范围',False,2000),('change_reference','更改依据',False,500)],
 'EXTERNAL_TECHNICAL_STATE': [('supplier_revision','供应商版本',True,128),('supplier_document','供应商文件',False,256),
    ('supplier_document_date','供应商文件日期',False,10),('notes','说明',False,2000)],
 'EXTERNAL_PROJECT_CONTROL': [('project_code','项目编号',True,128),('applicability','项目适用性',True,2000),
    ('evaluation_basis','评价依据',True,2000)],
 'SOFTWARE_VERSION': [('version','版本号',True,128),('build','构建号',False,128),('notes','说明',False,2000)],
}


def lock_object(conn, kind, oid):
    if kind not in TYPES: raise ValueError('不支持的审批对象类型')
    row=fetch_one(conn,f'SELECT * FROM {TYPES[kind][0]} WHERE id=%s FOR UPDATE',(oid,))
    if not row: raise LookupError('草稿对象不存在，可能已被删除')
    return row


def latest_request(conn, kind, oid):
    return fetch_one(conn,'SELECT * FROM approval_request WHERE object_type=%s AND object_id=%s ORDER BY requested_at DESC,id DESC LIMIT 1',(kind,oid))


def owner(row, req):
    return (req or {}).get('requester_id') or row.get('created_by')


def editable(kind,row):
    return (row['status']==TYPES[kind][1] or kind=='EXTERNAL_TECHNICAL_STATE' and row['status']=='REJECTED') and not row.get('approval_request_id')


def require_editable(conn,kind,oid,actor):
    row=lock_object(conn,kind,oid)
    req=latest_request(conn,kind,oid)
    if str(owner(row,req))!=str(actor['user_id']): raise PermissionError('只能编辑、删除或提交自己的草稿申请')
    if not editable(kind,row): raise ValueError('当前对象不可编辑；审核中请先撤回，已批准对象必须按变更流程建立新版')
    if kind=='EXTERNAL_TECHNICAL_STATE' and row['status']=='REJECTED':
        execute(conn,"UPDATE external_technical_state SET status='DRAFT' WHERE id=%s",(oid,))
        row['status']='DRAFT'
    return row


def location(conn,kind,row):
    if kind=='BASIC_DRAWING_FAMILY': return row['family_name_cn'], '#/family/'+str(row['id']), '#/families'
    if kind=='FILE_REVISION':
        p=fetch_one(conn,'SELECT file_number FROM design_file WHERE id=%s',(row['design_file_id'],))
        return p['file_number']+' Rev.'+row['revision_number'], '#/revision/'+str(row['id']), '#/file/'+p['file_number']
    if kind=='DESIGN_BASELINE':
        p=fetch_one(conn,'SELECT full_part_number FROM part_number WHERE id=%s',(row['part_number_id'],))
        return p['full_part_number']+' '+row['baseline_code'], '#/baseline/'+str(row['id']), '#/baselines/'+p['full_part_number']
    if kind=='SOFTWARE_VERSION':
        p=fetch_one(conn,'SELECT software_number FROM software_object WHERE id=%s',(row['software_object_id'],))
        return p['software_number']+' '+row['version'], '#/software/'+p['software_number']+'?version_id='+str(row['id']), '#/software/'+p['software_number']
    p=fetch_one(conn,'SELECT d.object_code FROM external_part e JOIN design_object d ON d.id=e.design_object_id WHERE e.id=%s',(row['external_part_id'],))
    return p['object_code']+' '+str(row.get('project_code') or 'TS'+str(row['state_sequence'])), '#/external/'+p['object_code'], '#/external/'+p['object_code']


def describe(conn,kind,oid,actor):
    row=lock_object(conn,kind,oid)
    req=latest_request(conn,kind,oid)
    code,url,parent=location(conn,kind,row)
    own=str(owner(row,req))==str(actor['user_id'])
    return dict(object_type=kind,id=str(row['id']),label=TYPES[kind][2],object_code=code,
        object_url=url,parent_url=parent,values=row,can_edit=own and editable(kind,row),
        can_withdraw=own and bool(req and req['status']=='PENDING'),request=req,
        fields=[dict(name=n,label=l,required=r,max_length=m,type='date' if n.endswith('_date') else 'text') for n,l,r,m in FIELDS[kind]])


def list_mine(conn,actor):
    rows=[]
    for kind,(table,state,label) in TYPES.items():
        for row in fetch_all(conn,f'''SELECT t.* FROM {table} t
            LEFT JOIN LATERAL (SELECT requester_id FROM approval_request a
                WHERE a.object_type=%s AND a.object_id=t.id ORDER BY requested_at DESC,id DESC LIMIT 1) a ON true
            WHERE (t.status=%s OR (%s='EXTERNAL_TECHNICAL_STATE' AND t.status='REJECTED')) AND t.approval_request_id IS NULL AND COALESCE(a.requester_id,t.created_by)=%s
            ORDER BY t.created_at DESC LIMIT 200''',(kind,state,kind,actor['user_id'])):
            code,url,parent=location(conn,kind,row)
            rows.append(dict(object_type=kind,id=str(row['id']),label=label,object_code=code,object_url=url,created_at=row['created_at']))
    return sorted(rows,key=lambda r:r['created_at'],reverse=True)


def write_audit(conn,kind,oid,actor,action,old,new=None):
    audit.write(conn,action=action,user_id=str(actor['user_id']),username=actor['username'],
        object_type=kind,object_id=oid,old_value=json.loads(json.dumps(old,default=str)),
        new_value=json.loads(json.dumps(new,default=str)) if new is not None else None,
        session_id=str(actor.get('session_id')),client_ip=actor.get('client_ip'))


def update(conn,kind,oid,values,actor):
    row=require_editable(conn,kind,oid,actor)
    fields={n:(l,r,m) for n,l,r,m in FIELDS[kind]}
    if not values or set(values)-fields.keys(): raise ValueError('包含不允许修改的字段或未提供修改内容')
    clean={}
    for key,value in values.items():
        label,required,limit=fields[key]
        if value is not None and not isinstance(value,str): raise ValueError(label+'格式不正确')
        value=value.strip() if value is not None else None
        if required and not value: raise ValueError(label+'不能为空')
        if value and len(value)>limit: raise ValueError(label+'超出长度限制')
        if key.endswith('_date') and value: date.fromisoformat(value)
        clean[key]=value or ('' if key in ('build','scope_note') else None)
    merged={**row,**clean}
    if kind=='BASIC_DRAWING_FAMILY':
        from . import families
        if merged['primary_class_code'] not in ('T1','T2','T3'): raise ValueError('请选择有效一级类别')
        if merged['object_level_code'] not in ('PART','ASSEMBLY'): raise ValueError('对象层级只能是零件或组件')
        for key,table in [('physical_class_id','physical_class'),('core_term_id','naming_core_term'),
                          ('qualifier_1_id','naming_qualifier'),('qualifier_2_id','naming_qualifier'),('primary_function_id','function_item')]:
            if merged.get(key) and not fetch_one(conn,f"SELECT id FROM {table} WHERE id=%s AND status='ACTIVE'",(merged[key],)):
                raise ValueError(fields[key][0]+'不存在或已停用')
        similar=families.similar_search(conn,merged['primary_class_code'],merged['physical_class_id'],merged['core_term_id'])
        clean['similar_check_result']=json.dumps({'candidates':[str(x['id']) for x in similar if str(x['id'])!=oid]})
        execute(conn,'UPDATE basic_drawing_family SET similar_check_at=now() WHERE id=%s',(oid,))
    if kind=='EXTERNAL_TECHNICAL_STATE':
        clean['hash_sha256']=hashlib.sha256(f"{merged['supplier_revision']}|{merged.get('supplier_document') or ''}|{merged.get('supplier_document_date') or ''}".encode()).hexdigest()
    execute(conn,f"UPDATE {TYPES[kind][0]} SET "+','.join(k+'=%s'+('::jsonb' if k=='similar_check_result' else '') for k in clean)+' WHERE id=%s',tuple(clean.values())+(oid,))
    write_audit(conn,kind,oid,actor,'DRAFT_EDIT',row,clean)
    return describe(conn,kind,oid,actor)


def delete(conn,kind,oid,actor):
    row=require_editable(conn,kind,oid,actor)
    code,url,parent=location(conn,kind,row)
    # Only owned draft children are removed. Referenced business records keep
    # their FK protection; old approval rounds and audited file bytes remain.
    if kind=='SOFTWARE_VERSION': execute(conn,'DELETE FROM software_hardware_compatibility WHERE software_version_id=%s',(oid,))
    if kind=='FILE_REVISION': execute(conn,'DELETE FROM revision_attachment WHERE file_revision_id=%s',(oid,))
    if kind=='DESIGN_BASELINE': execute(conn,'DELETE FROM baseline_item WHERE design_baseline_id=%s',(oid,))
    execute(conn,f'DELETE FROM {TYPES[kind][0]} WHERE id=%s',(oid,))
    execute(conn,"UPDATE approval_request SET payload=payload || jsonb_build_object('draft_deleted_at',now()) WHERE object_type=%s AND object_id=%s",(kind,oid))
    write_audit(conn,kind,oid,actor,'DRAFT_DELETE',row)
    return {'deleted':True,'parent_url':parent}


def replace_package(conn,oid,filename,mime,content,actor):
    row=require_editable(conn,'SOFTWARE_VERSION',oid,actor)
    if not content: raise ValueError('软件包不能为空')
    so=fetch_one(conn,'SELECT software_number FROM software_object WHERE id=%s',(row['software_object_id'],))
    key=storage.make_software_key(so['software_number'],str(uuid4()),filename)
    saved=storage.save(key,content)
    try:
        execute(conn,'''UPDATE software_version SET package_filename=%s,package_storage_key=%s,
            package_mime_type=%s,package_size_bytes=%s,hash_sha256=%s WHERE id=%s''',
            (filename,saved.storage_key,mime,saved.size_bytes,saved.sha256,oid))
        write_audit(conn,'SOFTWARE_VERSION',oid,actor,'DRAFT_PACKAGE_REPLACE',row,{'sha256':saved.sha256,'filename':filename})
        conn.execute('SET CONSTRAINTS ALL IMMEDIATE')
    except BaseException:
        storage.delete(saved.storage_key)
        raise
    return describe(conn,'SOFTWARE_VERSION',oid,actor)
