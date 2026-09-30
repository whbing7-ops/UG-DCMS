"""把 pandoc 生成的蓝色主题标题改黑。

必须在 Word 最后一次保存之后跑: Word 保存时会按 themeColor 重新解析色值,
把之前改黑的又刷回蓝色。所以流程是 建 → Word存(更新目录) → 本脚本 → Word只导PDF。
"""
import re
import shutil
import sys
import zipfile

F = sys.argv[1]
PAT = re.compile(r'<w:color w:val="(?:0F4761|156082|595959|272727)"[^>]*/>')
z = zipfile.ZipFile(F)
names = z.namelist()
parts = {k: z.read(k) for k in names}
z.close()
n = 0
for k in ('word/styles.xml', 'word/document.xml'):
    txt, c = PAT.subn('<w:color w:val="000000"/>', parts[k].decode('utf-8'))
    n += c
    parts[k] = txt.encode('utf-8')
tmp = F + '.tmp'
zo = zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED)
for k in names:
    zo.writestr(k, parts[k])
zo.close()
shutil.move(tmp, F)
print('改黑 %d 处' % n)
