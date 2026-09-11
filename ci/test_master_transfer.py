"""Public API regression for rc2.34 master data rules; disposable CI only."""
import csv
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor


def run(engineer, approver, cm, admin, target):
    stamp=str(time.time_ns())
    classes=engineer.get('/families/class-options')
    assert [c['code'] for c in classes]==['T'+str(i) for i in range(1,10)]
    assert all(c['available']==(c['code'] in ('T1','T2','T3')) for c in classes)
    assert all(c['reason'] for c in classes if not c['available'])
    pcs=engineer.get('/dictionary/physical-class')
    terms=engineer.get('/dictionary/core-term')
    fn=engineer.get('/dictionary/function-item')[0]
    level=engineer.get('/dictionary/object-level')[0]['code']

    def csv_bytes(rows):
        f=io.StringIO(); w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        return f.getvalue().encode('utf-8-sig')
    def preview(kind,rows):
        return engineer.upload('/master-data/'+kind+'/preview','batch.csv',csv_bytes(rows))
    def commit(batch,client=engineer,expected=(200,)):
        return client.call('POST','/master-data/batches/'+batch['batch_id']+'/commit',expected=expected)
    def row(cls):
        return {'primary_class_code':cls,
          'physical_class_code':next(p['code'] for p in pcs if p['primary_class_code']==cls),
          'core_term_code':next(t['code'] for t in terms if t['primary_class_code']==cls),
          'object_level_code':level,'primary_function_code':fn['code'],
          'family_definition':'batch-'+stamp+'-'+cls,'allowed_variation':'尺寸',
          'excluded_variation':'原理','new_family_reason':'批量导入回归'}
    before=engineer.get('/numbers/basic-drawing')['next_by_class']
    count=len(engineer.get('/families'))
    b=preview('families',[row('T1'),row('T2')])
    assert b['committable'],b
    assert len(engineer.get('/families'))==count
    assert engineer.get('/numbers/basic-drawing')['next_by_class']==before
    commit(b,admin,expected=(400,))
    assert commit(b)['created']==2
    commit(b,expected=(400,))
    made=[]
    for cls in ('T1','T2'):
        fam=next(f for f in engineer.get('/families') if engineer.get('/families/'+f['id'])['family_definition']=='batch-'+stamp+'-'+cls)
        assert fam['basic_drawing_number'].startswith('PENDING-')
        engineer.post('/families/'+fam['id']+'/submit',{'approver_user_id':target['id']})
        active=approver.post('/families/'+fam['id']+'/approve?comments=batch')
        assert active['basic_drawing_number']==f"UG{cls[1]}{before[cls]:05d}"
        made.append(active)
    bad=preview('families',[row('T1'),dict(row('T1'),primary_class_code='T4')])
    assert bad['error_rows']==1
    commit(bad,expected=(400,))
    print('PASS batch/family-preview-rollback-approval-per-class-numbering-blocked-category',flush=True)

    basic=made[0]['basic_drawing_number']
    part={'basic_drawing_number':basic,'formal_name_cn':'批量支架甲',
          'formal_name_en':'BATCH BRACKET A','object_level_code':level,'difference_summary':'尺寸区别'}
    b=preview('parts',[part,dict(part,formal_name_cn='批量支架乙')])
    assert b['committable'],b
    assert engineer.get('/families/'+made[0]['id']+'/numbers')['next_available']==1
    assert commit(b)['created']==2
    numbers=engineer.get('/families/'+made[0]['id']+'/dashes')
    assert [p['full_part_number'] for p in numbers]==[basic+'-001',basic+'-002']
    assert preview('parts',[dict(part,requested_dash='001')])['error_rows']==1
    exported=list(csv.DictReader(io.StringIO(engineer.download('/master-data/parts/export?family_id='+made[0]['id']).decode('utf-8-sig'))))
    assert len(exported)==2 and exported[0]['完整件号']==basic+'-001'
    # XLSX uses the same validation path, not just CSV parsing.
    import openpyxl
    wb=openpyxl.Workbook();ws=wb.active;ws.append(list(part));ws.append(list(part.values()))
    out=io.BytesIO();wb.save(out)
    xb=engineer.upload('/master-data/parts/preview','batch.xlsx',out.getvalue())
    assert xb['committable'],xb
    # Occupy a specifically previewed Dash before commit: entire batch must roll back.
    stale=preview('parts',[dict(part,requested_dash='010'),dict(part,requested_dash='011')])
    engineer.post('/families/'+made[0]['id']+'/dashes',{
      'formal_name_cn':'已占用支架','formal_name_en':'OCCUPIED BRACKET','object_level_code':level,
      'difference_summary':'并发占号','requested_dash':11})
    commit(stale,expected=(400,))
    assert not any(n['dash_number']==10 for n in engineer.get('/families/'+made[0]['id']+'/dashes'))
    print('PASS batch/dash-csv-xlsx-export-duplicate-and-stale-batch-atomic-rollback',flush=True)

    ns=engineer.get('/dictionary/namespace')
    extcls=engineer.get('/external-part-classes')[0]['code']
    ext={'namespace_code':ns[0]['code'],'external_part_number':'BATCH-'+stamp,
         'name_cn':'批量外部件','external_class_code':extcls}
    duplicate=dict(ext,name_cn='不同的名称',namespace_code=ns[-1]['code'])
    b=preview('externals',[ext,duplicate]);assert b['error_rows']==1,b
    b=preview('externals',[ext]);assert b['committable'];assert commit(b)['created']==1
    for d in (duplicate,dict(duplicate,external_part_number=' '+ext['external_part_number'].lower()+' ')):
        r=engineer.call('POST','/external-parts',d,expected=(400,409))
        assert '已存在' in r['error']['message'],r
    racing=dict(ext,external_part_number='RACE-'+stamp)
    with ThreadPoolExecutor(2) as executor:
        results=list(executor.map(lambda n:engineer.call('POST','/external-parts',dict(racing,name_cn='并发件'+str(n)),expected=(201,400,409)),range(2)))
    assert sum('object_code' in r for r in results)==1,results
    for kind in ('families','externals'):
        assert len(list(csv.DictReader(io.StringIO(engineer.download('/master-data/'+kind+'/export').decode('utf-8-sig')))))>0
        assert engineer.download('/master-data/'+kind+'/template').startswith(b'\xef\xbb\xbf')
    print('PASS external/global-duplicate-different-name-case-space-and-concurrent-create',flush=True)
