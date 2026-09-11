"""Actual rc2.34 browser paths, including the formerly broken PENDING detail."""
import csv
import io
import re
import time
from playwright.sync_api import expect


def run(page, admin, call, base, output):
    stamp=str(time.time_ns())
    page.goto(base+'/#/family-new')
    selector=page.get_by_label('一级技术类别',exact=True)
    expect(selector.locator('option')).to_have_count(9)
    assert '机械、结构及非电气件' in selector.locator('option').first.inner_text()
    assert selector.locator('option:disabled').count()==6
    assert all('不可选' in t for t in selector.locator('option:disabled').all_text_contents())
    page.get_by_role('button',name='检索相似设计族',exact=True).click()
    create=page.get_by_role('button',name='建立设计族',exact=True)
    expect(create).to_be_enabled()
    selector.select_option('T2');expect(create).to_be_disabled()
    selector.select_option('T1')
    page.get_by_role('button',name='检索相似设计族',exact=True).click()
    page.get_by_label('族定义',exact=True).fill('UI family '+stamp)
    page.get_by_label('允许的变化范围',exact=True).fill('尺寸')
    page.get_by_label('排除的变化范围',exact=True).fill('原理')
    page.get_by_label('新建理由',exact=True).fill('UI approval regression')
    create.click()
    submit=page.get_by_role('button',name='提交审批',exact=True)
    expect(submit).to_be_visible()
    family_id=page.url.split('/family/')[1]
    submit.click()
    admin_id=call(admin,'GET','/auth/me')['id']
    page.get_by_role('dialog').get_by_label('审批人').select_option(admin_id)
    page.get_by_role('dialog').get_by_role('button',name='确认提交',exact=True).click()
    expect(page.get_by_role('dialog')).not_to_be_visible()
    admin.goto(base+'/#/family/'+family_id)
    admin.get_by_role('button',name='批准并分配图号',exact=True).click()
    expect(admin.locator('.tb-code')).to_have_text(re.compile(r'^UG1\d{5}$'))
    basic=admin.locator('.tb-code').inner_text()
    page.goto(base+'/#/family/'+family_id)
    page.reload()  # A second user's approval requires a fresh fetch of the same hash URL.
    expect(page.get_by_role('link',name='新增 Dash 件号',exact=True)).to_be_visible()
    page.screenshot(path=str(output/'family-approved-rc234.png'),full_page=True)
    print('PASS UI/family-category-invalidation-create-submit-admin-approve-number',flush=True)

    # Download and populate the actual template, preview, commit and download export.
    with page.expect_download() as download:
        page.get_by_role('button',name='下载Dash 件号导入模板',exact=True).click()
    template=download.value.path().read_bytes().decode('utf-8-sig')
    fields=next(csv.reader(io.StringIO(template)))
    stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
    level=call(page,'GET','/dictionary/object-level')[0]['code']
    writer.writerow({'基本图号':basic,'中文正式名称':'浏览器支架','英文正式名称':'UI BRACKET',
                     '对象层级':level,'差异说明':'尺寸区别'})
    page.get_by_role('button',name='批量导入Dash 件号',exact=True).click()
    dialog=page.get_by_role('dialog')
    dialog.get_by_label('导入文件').set_input_files({'name':'parts.csv','mimeType':'text/csv','buffer':stream.getvalue().encode('utf-8-sig')})
    dialog.get_by_role('button',name='校验并预览',exact=True).click()
    expect(dialog.get_by_role('button',name='确认整批导入',exact=True)).to_be_enabled()
    expect(dialog).to_contain_text('通过 1 行，错误 0 行')
    dialog.get_by_role('button',name='确认整批导入',exact=True).click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_role('link',name=basic+'-001',exact=True)).to_be_visible()
    with page.expect_download() as download:
        page.get_by_role('button',name='导出本族件号',exact=True).click()
    assert basic+'-001' in download.value.path().read_text(encoding='utf-8-sig')
    print('PASS UI/batch-download-template-upload-preview-commit-export',flush=True)

    ns=call(page,'GET','/dictionary/namespace')[0]['code']
    cls=call(page,'GET','/external-part-classes')[0]['code']
    for n in range(12):
        call(page,'POST','/external-parts',{'namespace_code':ns,'external_part_number':'UI-PICK-'+stamp+'-'+str(n),
          'name_cn':'下拉列表模块'+str(n),'external_class_code':cls},status=201)
    page.goto(base+'/#/bom/'+basic+'-001')
    search=page.get_by_role('textbox',name='子件号搜索',exact=True)
    search.fill('UI-PICK-'+stamp)
    expect(page.locator('.part-picker-option')).to_have_count(12)
    search.scroll_into_view_if_needed()
    assert page.locator('.part-picker-results').evaluate("""e => {
      const r=e.getBoundingClientRect(),p=e.closest('.panel').getBoundingClientRect();
      const y=Math.min(r.bottom-12,innerHeight-10),x=r.left+20;
      return r.height>=350 && y>p.bottom && e.contains(document.elementFromPoint(x,y));
    }"""), 'dropdown is clipped by its panel'
    page.screenshot(path=str(output/'bom-picker-rc234.png'))
    page.locator('.part-picker-results').evaluate('e=>e.scrollTop=e.scrollHeight')
    page.locator('.part-picker-option').last.click()
    expect(page.locator('.part-picker-results')).not_to_be_visible()
    expect(page.locator('.part-picker-selected')).to_contain_text('已选择')
    print('PASS UI/BOM-dropdown-twelve-results-unclipped-scroll-and-select',flush=True)
