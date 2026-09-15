-- rc2.34: UG-DGS-000 Rev03 §4.2; existing identities remain unchanged.
UPDATE primary_class SET name_cn='机械、结构及非电气件' WHERE code='T1';
UPDATE primary_class SET name_cn='电气、电子部件与设备' WHERE code='T2';
UPDATE primary_class SET name_cn='以导线或电缆为主体的互连组件' WHERE code='T3';
INSERT INTO primary_class(code,name_cn,name_en,definition,sort_order)
SELECT 'T'||n,'预留类别','Reserved','预留类别，尚未批准启用',n
FROM generate_series(4,8) n ON CONFLICT(code) DO NOTHING;
INSERT INTO primary_class(code,name_cn,name_en,definition,sort_order)
VALUES('T9','历史与特殊','Historical and special','仅用于历史登记及经批准的特殊编号',9)
ON CONFLICT(code) DO NOTHING;

-- New external P/Ns must be unique across names and namespaces. A registry
-- preserves legacy duplicates without deleting/merging their referenced objects.
-- The unique key serializes concurrent claims; preview transactions roll it back.
CREATE TABLE external_part_number_registry (
    normalized_number text PRIMARY KEY,
    first_object_id uuid NOT NULL
);
INSERT INTO external_part_number_registry
SELECT DISTINCT ON (upper(btrim(external_part_number)))
       upper(btrim(external_part_number)),id
FROM external_part ORDER BY upper(btrim(external_part_number)),created_at,id;

CREATE FUNCTION dcms_guard_external_number() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.external_part_number := btrim(NEW.external_part_number);
    IF NEW.external_part_number = '' THEN
        RAISE EXCEPTION 'DCMS-EXT-NUMBER: 外部件号不能为空' USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' THEN
        IF upper(NEW.external_part_number)=upper(btrim(OLD.external_part_number)) THEN
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO external_part_number_registry VALUES(upper(NEW.external_part_number),NEW.id)
    ON CONFLICT DO NOTHING;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DCMS-EXT-DUPLICATE: 外部件号 % 已存在，即使名称或来源不同也不能重复创建', NEW.external_part_number
        USING ERRCODE='23505';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_external_number_unique BEFORE INSERT OR UPDATE OF external_part_number
ON external_part FOR EACH ROW EXECUTE FUNCTION dcms_guard_external_number();

ALTER TABLE import_batch DROP CONSTRAINT import_batch_import_type_check;
ALTER TABLE import_batch ADD CONSTRAINT import_batch_import_type_check
CHECK(import_type IN ('BOM','PART_NUMBER','EXTERNAL_PART','CROSS_REFERENCE','ATTRIBUTE','FAMILY'));
