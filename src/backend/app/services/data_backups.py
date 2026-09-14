"""Portable, additive logical IMA backup; never executes SQL or restores accounts.

Version 1 accepts only the shipped immutable dataset. Numbered identities,
snapshot references and simulated approvals are recreated in the target system
using domain services. No real source users, credentials or data are exported.
"""
import hashlib
import io
import json
import zipfile

from . import ima_demo

FORMAT = 'UG-DCMS-DATA-BACKUP'
MAX_BYTES = 2 * 1024 * 1024
CONFIRMATION = '导入模拟数据'


def _payload():
    return json.dumps(ima_demo.builtin_dataset(), ensure_ascii=False, sort_keys=True,
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
        raise ValueError('数据备份包超过 2 MB 上限')
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
                    manifest.get('dataset_code') != ima_demo.CODE or manifest.get('mode') != 'ADDITIVE'):
                raise ValueError('不是受支持的增量数据备份包，请使用 rc2.39 配套 IMA 数据备份')
            # Check against trusted package content as well as its own manifest:
            # arbitrary objects, IDs, scripts and modified SVG cannot be imported.
            if digest != manifest.get('payload_sha256') or digest != hashlib.sha256(_payload()).hexdigest():
                raise ValueError('数据备份包校验失败或内容已修改，请重新下载配套备份包')
            return json.loads(payload)
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('无法读取数据备份包，请选择配套 ZIP 文件') from exc


def import_package(conn, actor, content, confirmation):
    if confirmation != CONFIRMATION:
        raise ValueError('请输入确认文字：'+CONFIRMATION)
    dataset = validate_package(content)
    return ima_demo.import_dataset(conn, actor, dataset)
