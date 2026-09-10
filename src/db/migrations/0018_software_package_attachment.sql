-- 软件版本交付包：实际文件、自动摘要与版本技术状态绑定。
ALTER TABLE software_version ADD COLUMN IF NOT EXISTS package_filename text;
ALTER TABLE software_version ADD COLUMN IF NOT EXISTS package_storage_key text;
ALTER TABLE software_version ADD COLUMN IF NOT EXISTS package_mime_type text;
ALTER TABLE software_version ADD COLUMN IF NOT EXISTS package_size_bytes bigint;

CREATE UNIQUE INDEX IF NOT EXISTS uq_software_version_package_storage_key
    ON software_version(package_storage_key) WHERE package_storage_key IS NOT NULL;

ALTER TABLE software_version DROP CONSTRAINT IF EXISTS ck_software_package_complete;
ALTER TABLE software_version ADD CONSTRAINT ck_software_package_complete CHECK (
    (package_filename IS NULL AND package_storage_key IS NULL AND
     package_mime_type IS NULL AND package_size_bytes IS NULL)
    OR
    (package_filename IS NOT NULL AND package_storage_key IS NOT NULL AND
     package_mime_type IS NOT NULL AND package_size_bytes >= 0 AND hash_sha256 IS NOT NULL)
);

COMMENT ON COLUMN software_version.package_storage_key IS
    '软件版本交付包的受控存储键；摘要使用 software_version.hash_sha256。';
