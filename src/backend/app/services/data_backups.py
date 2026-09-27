"""Portable, additive logical simulation backups; never executes SQL or restores accounts.

Version 1 accepts only the two shipped immutable datasets. Numbered identities,
snapshot references and simulated approvals are recreated in the target system
using domain services. No real source users, credentials or data are exported.
"""
import hashlib
import io
import json
import zipfile

from . import ima_demo, scale_demo

FORMAT = 'UG-DCMS-DATA-BACKUP'
MAX_BYTES = 16 * 1024 * 1024
CONFIRMATION = '导入模拟数据'


def _payload(dataset_code=ima_demo.CODE):
    module = {ima_demo.CODE: ima_demo, scale_demo.CODE: scale_demo}[dataset_code]
    return json.dumps(module.builtin_dataset(), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def create_package():
    payload = _payload()
    manifest = {'format': FORMAT, 'version': 1, 'dataset_code': ima_demo.CODE,
                'mode': 'ADDITIVE', 'minimum_release': 'rc2.39',
                'notice': ima_demo.NOTICE, 'payload_sha256': hashlib.sha256(payload).hexdigest()}
    guide = '''【模拟数据】IMA 可移植业务数据备份包
安装 rc2.39 或兼容版本后，以管理员登录 → 系统 → 备份恢复 → 导入数据备份包。
选择本 ZIP，输入“导入模拟数据”，点击“校验并导入数据”。无需解压或重启服务。
此包为逻辑数据备份，包含内部件、外部件、多层 BOM、两种构型数据、软件关联和模拟图纸模板。
导入时按目标系统编号规则分配件号，生成含目标件号的模拟附件、软件元数据包、冻结清单与设计基线；
模拟编制和批准流程由独立模拟身份重建，完成后身份停用。不包含任何真实账户、密码或数据库转储。
共生成 19 个内部件、9 个外部件、45 条 BOM、3 套软件、23 份设计附件、82 个模拟审批、19 条发布基线。
已有数据和附件保持不变；重复导入返回同一批次；前缀冲突时停止，不覆盖已有记录。
不要将此包用于“恢复系统”的整库替换入口。此包不执行 SQL，不是 PostgreSQL 全量灾备文件。
完成后在件号、BOM、软件、设计资料清单和基线等正常业务模块查看。所有内容仅供业务演示。
'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in [('manifest.json', json.dumps(manifest, ensure_ascii=False).encode()),
                            ('dataset.json', payload), ('导入说明.txt', guide.encode())]:
            info = zipfile.ZipInfo(name, date_time=(2026, 9, 14, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return buffer.getvalue()


def validate_package(content):
    if len(content) > MAX_BYTES:
        raise ValueError('数据备份包超过 16 MB 上限')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if (len(infos) != 3 or {i.filename for i in infos} !=
                    {'manifest.json', 'dataset.json', '导入说明.txt'} or
                    sum(i.file_size for i in infos) > MAX_BYTES or any(i.flag_bits & 1 for i in infos)):
                raise ValueError('数据备份包文件清单或大小不符合要求')
            manifest = json.loads(archive.read('manifest.json'))
            payload = archive.read('dataset.json')
            digest = hashlib.sha256(payload).hexdigest()
            if (manifest.get('format') != FORMAT or manifest.get('version') != 1 or
                    manifest.get('dataset_code') not in (ima_demo.CODE, scale_demo.CODE) or manifest.get('mode') != 'ADDITIVE'):
                raise ValueError('不是受支持的增量数据备份包，请使用配套 IMA 或十项目规模数据包')
            # Check against trusted package content as well as its own manifest:
            # arbitrary objects, IDs, scripts and modified SVG cannot be imported.
            if digest != manifest.get('payload_sha256') or digest != hashlib.sha256(_payload(manifest['dataset_code'])).hexdigest():
                raise ValueError('数据备份包校验失败或内容已修改，请重新下载配套备份包')
            return json.loads(payload)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取数据备份包，请选择配套 ZIP 文件') from exc


def import_package(conn, actor, content, confirmation):
    if confirmation != CONFIRMATION:
        raise ValueError('请输入确认文字：'+CONFIRMATION)
    dataset = validate_package(content)
    module = scale_demo if dataset['dataset_code'] == scale_demo.CODE else ima_demo
    return module.import_dataset(conn, actor, dataset)


def create_scale_package():
    payload = _payload(scale_demo.CODE)
    manifest = dict(format=FORMAT, version=1, dataset_code=scale_demo.CODE,
        mode='ADDITIVE', minimum_release='rc2.43', notice=scale_demo.NOTICE,
        counts=scale_demo.COUNTS, payload_sha256=hashlib.sha256(payload).hexdigest())
    guide = """【模拟数据】十项目规模业务数据备份包 v1
先安装或升级至 rc2.43，再以管理员登录 → 系统 → 备份恢复 → 导入数据备份包。
选择本 ZIP（不要解压上传），输入确认文字“导入模拟数据”，点击“校验并导入数据”。
数据量较大，可能需要数分钟；请保持服务运行。若页面中断，可重新进入备份页查看导入记录。

新增1000个设计族，每族10个Dash，共10000个内部件号；500个软件对象；5000个外部件号；10个模拟项目。
每项目使用1000个内部件号、500个外部件号和50个软件对象，另有跨项目共用件、外部件和软件。
包含三层BOM、10000个发布设计基线、1010份模拟资料和500个不可执行的软件元数据包。
项目以构型上下文、项目基线、外部件项目准入和冻结目录体现；不增加独立项目菜单。
基本图号按目标系统现有分类规则分配，不含T，各项目根件号和基线入口在导入完成面板列出。

名称、定义、附件和审批统一标注模拟。审批由两名独立模拟身份执行，完成后两身份停用。
已有数据、账户、附件保留；重复导入不会再次新增；前缀冲突则停止。本包不含真实凭证，不执行任意SQL。
这是增量逻辑数据备份，不是整库灾备包，请勿选择“恢复系统”的整库替换入口。
所有内容仅用于测试，不可作为制造、试验符合性或装机依据。
"""
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in [('manifest.json',json.dumps(manifest,ensure_ascii=False).encode()),
                           ('dataset.json',payload),('导入说明.txt',guide.encode('utf-8-sig'))]:
            info=zipfile.ZipInfo(name,date_time=(2026,9,18,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,value)
    return buffer.getvalue()
