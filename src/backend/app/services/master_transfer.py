"""Master-data transfer: real business validation in rollback-only savepoints.

Preview checks every row, including conflicts between rows, without consuming
numbers. Commit locks the batch and rechecks in one atomic transaction.
"""
import csv
import hashlib
import io
import json
import uuid

import psycopg
from pydantic import ValidationError

from .. import audit
from ..db import execute, fetch_all, fetch_one, scalar
from . import families, externals, imports

FIELDS = {
    'families': [('primary_class_code','一级类别'),('physical_class_code','二级分类代码'),
        ('object_level_code','对象层级'),('core_term_code','核心词代码'),
        ('qualifier_1_code','限定词1代码'),('qualifier_2_code','限定词2代码'),
        ('primary_function_code','主功能代码'),('family_definition','族定义'),
        ('allowed_variation','允许变化'),('excluded_variation','排除变化'),
        ('new_family_reason','新建理由'),('classification_note','分类说明'),
        ('basic_drawing_number','基本图号'),('family_name_cn','名称'),('status','状态')],
    'parts': [('basic_drawing_number','基本图号'),('requested_dash','Dash号'),
        ('formal_name_cn','中文正式名称'),('formal_name_en','英文正式名称'),
        ('object_level_code','对象层级'),('difference_summary','差异说明'),
        ('full_part_number','完整件号'),('lifecycle_status','状态')],
    'externals': [('namespace_code','来源代码'),('external_part_number','外部件号'),
        ('name_cn','中文名称'),('name_en','英文名称'),('manufacturer_code','制造商代码'),
        ('external_class_code','外部件分类'),('project_code','项目编号'),
        ('project_applicability','项目适用范围'),('project_evaluation_basis','项目评价依据'),
        ('lifecycle_status','状态')],
}
TYPES = {'families':'FAMILY', 'parts':'PART_NUMBER', 'externals':'EXTERNAL_PART'}


def check_kind(kind):
    if kind not in FIELDS:
        raise ValueError('未知导入导出类型')


def csv_text(kind, rows):
    check_kind(kind)
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow([label for key,label in FIELDS[kind]])
    for row in rows:
        # CSV opened in Excel must not execute user-entered formulas.
        values = [str(row.get(key) or '') for key,label in FIELDS[kind]]
        writer.writerow(["'"+v if v.startswith(('=','+','-','@','\t','\r')) else v for v in values])
    return '\ufeff' + out.getvalue()


def export_rows(conn, kind, family_id=None):
    check_kind(kind)
    if kind == 'families':
        return fetch_all(conn, """SELECT f.*,pc.code AS physical_class_code,ct.code AS core_term_code,
          q1.code AS qualifier_1_code,q2.code AS qualifier_2_code,fn.code AS primary_function_code
          FROM basic_drawing_family f JOIN physical_class pc ON pc.id=f.physical_class_id
          JOIN naming_core_term ct ON ct.id=f.core_term_id
          LEFT JOIN naming_qualifier q1 ON q1.id=f.qualifier_1_id
          LEFT JOIN naming_qualifier q2 ON q2.id=f.qualifier_2_id
          JOIN function_item fn ON fn.id=f.primary_function_id ORDER BY f.created_at,f.id""")
    if kind == 'parts':
        return fetch_all(conn, """SELECT p.*,p.dash_number AS requested_dash,f.basic_drawing_number
          FROM part_number p JOIN basic_drawing_family f ON f.id=p.basic_drawing_family_id
          WHERE (%s::uuid IS NULL OR f.id=%s::uuid) ORDER BY p.full_part_number""", (family_id,family_id))
    return fetch_all(conn, """SELECT e.*,n.code AS namespace_code,m.code AS manufacturer_code
       FROM external_part e JOIN namespace n ON n.id=e.namespace_id
       LEFT JOIN manufacturer m ON m.id=e.manufacturer_id ORDER BY e.external_part_number,e.id""")


def _dictionary_id(conn, table, code):
    if not code:
        return None
    value = scalar(conn, f"SELECT id FROM {table} WHERE code=%s AND status='ACTIVE'", (code,))
    if value is None:
        raise ValueError(f'词典代码不存在或已停用：{code}')
    return str(value)


def create_row(conn, kind, data, actor):
    # Reuse the interactive API's field limits and the same business services.
    from ..api.families import FamilyCreateRequest, DashCreateRequest
    from ..api.externals import ExternalCreateRequest
    d = dict(data)
    if kind == 'families':
        if d.get('basic_drawing_number'):
            raise ValueError('新建设计族的基本图号必须留空，由审批发号；导出文件中的已有设计族不可重复新建')
        for source,target,table in [('physical_class_code','physical_class_id','physical_class'),
                ('core_term_code','core_term_id','naming_core_term'),
                ('qualifier_1_code','qualifier_1_id','naming_qualifier'),
                ('qualifier_2_code','qualifier_2_id','naming_qualifier'),
                ('primary_function_code','primary_function_id','function_item')]:
            d[target] = _dictionary_id(conn, table, d.pop(source,None))
        return families.create_family(conn, **FamilyCreateRequest.model_validate(d).model_dump(), actor=actor)
    if kind == 'parts':
        family_id = scalar(conn, 'SELECT id FROM basic_drawing_family WHERE basic_drawing_number=%s',
                           (d.get('basic_drawing_number'),))
        if not family_id:
            raise ValueError('基本图号不存在，请先建立设计族并批准发号')
        return families.create_dash(conn, str(family_id), **DashCreateRequest.model_validate(d).model_dump(), actor=actor)
    return externals.create_external(conn, **ExternalCreateRequest.model_validate(d).model_dump(), actor=actor)


def _message(exc):
    if isinstance(exc, ValidationError):
        from ..errors import FIELD_CN, _validation_message
        return '；'.join(FIELD_CN.get(e['loc'][-1],str(e['loc'][-1]))+'：'+_validation_message(e) for e in exc.errors())
    if isinstance(exc, psycopg.Error):
        return exc.diag.message_primary or '数据不符合约束'
    return str(exc)


def preview(conn, kind, content, filename, actor):
    check_kind(kind)
    header, rows = imports.parse_table(content,filename)
    if not rows or len(rows)>2000:
        raise ValueError('每批应包含 1–2000 行数据')
    aliases = {alias:key for key,label in FIELDS[kind] for alias in (key,label)}
    keys = [aliases.get(h.strip()) for h in header]
    if len([k for k in keys if k]) != len(set(k for k in keys if k)):
        raise ValueError('存在重复表头，请使用导入模板')
    results=[]
    # Outer rollback retains valid rows during validation, catching intra-file
    # duplicates and sequential Dash allocation. No identities or audit survive.
    with conn.transaction(force_rollback=True):
        for index,row in enumerate(rows,2):
            data = {key: str(row[i]).strip() if i<len(row) and row[i] is not None else None
                    for i,key in enumerate(keys) if key}
            data = {k:(v or None) for k,v in data.items()}
            try:
                with conn.transaction():
                    create_row(conn,kind,data,actor)
                result,messages='OK',[]
            except (ValueError,LookupError,psycopg.Error) as exc:
                result,messages='ERROR',[_message(exc)]
            results.append((index,data,result,messages))
    batch = fetch_one(conn, """INSERT INTO import_batch
        (batch_number,import_type,source_filename,source_sha256,target_context,created_by,total_rows,ok_rows,error_rows)
        VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s) RETURNING id""",
        ('MASTER-'+uuid.uuid4().hex, TYPES[kind], filename, hashlib.sha256(content).hexdigest(),
         json.dumps({'kind':kind}),actor['user_id'],len(results),
         sum(r[2]=='OK' for r in results),sum(r[2]=='ERROR' for r in results)))
    for index,data,result,messages in results:
        execute(conn, """INSERT INTO import_batch_row(import_batch_id,row_number,raw_data,result,messages)
            VALUES(%s,%s,%s::jsonb,%s,%s::jsonb)""",
            (batch['id'],index,json.dumps(data,ensure_ascii=False),result,json.dumps(messages,ensure_ascii=False)))
    return imports._batch_result(conn,str(batch['id']))


def commit(conn, batch_id, actor):
    b=fetch_one(conn,'SELECT * FROM import_batch WHERE id=%s FOR UPDATE',(batch_id,))
    if b is None:
        raise LookupError('导入批次不存在')
    if str(b['created_by']) != str(actor['user_id']):
        raise ValueError('只能提交本人预览的批次')
    if b['status']!='PREVIEW' or b['error_rows'] or not b['total_rows']:
        raise ValueError('批次已提交或包含错误，请修正后重新预览')
    kind=(b['target_context'] or {}).get('kind')
    check_kind(kind)
    rows=fetch_all(conn,'SELECT * FROM import_batch_row WHERE import_batch_id=%s ORDER BY row_number',(batch_id,))
    for row in rows:
        try:
            obj=create_row(conn,kind,row['raw_data'],actor)
        except (ValueError,LookupError,psycopg.Error) as exc:
            raise ValueError(f"第 {row['row_number']} 行：{_message(exc)}；整批未导入，请重新预览") from exc
        execute(conn,'UPDATE import_batch_row SET created_object_type=%s,created_object_id=%s WHERE id=%s',
                (TYPES[kind],obj['id'],row['id']))
    execute(conn,"UPDATE import_batch SET status='COMMITTED',committed_at=now(),committed_by=%s WHERE id=%s",(actor['user_id'],batch_id))
    audit.write(conn,action='IMPORT_COMMIT',user_id=str(actor['user_id']),username=actor['username'],
                object_type='IMPORT_BATCH',object_id=batch_id,new_value={'kind':kind,'created':len(rows)})
    return {'batch_number':b['batch_number'],'created':len(rows)}
