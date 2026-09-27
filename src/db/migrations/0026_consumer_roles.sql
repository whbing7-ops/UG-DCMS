-- 生产、采购角色: 只读, 只见已发布内容(权限在应用层 rbac.py 中定义)。
-- 可重复执行; 已存在同名角色时保持不变。
INSERT INTO role (code, name_cn, name_en, description, sort_order) VALUES
('PRODUCTION',  '生产', 'Production',
 '只读; 查看并下载已发布的图纸与资料, 不可查看草稿与审核中内容', 70),
('PROCUREMENT', '采购', 'Procurement',
 '只读; 查看已发布资料、BOM、外部件, 不含原生设计文件与草稿', 80)
ON CONFLICT (code) DO NOTHING;
