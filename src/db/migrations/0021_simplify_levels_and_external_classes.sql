-- User-controlled simplification: Part/Assembly and three technical classes.
-- Record the metadata conversion before changing it; identities stay stable.
INSERT INTO audit_log(username,action,object_type,object_id,old_value,new_value,reason)
SELECT 'SYSTEM','OBJECT_LEVEL_SIMPLIFY','BASIC_DRAWING_FAMILY',id,
       jsonb_build_object('object_level_code',object_level_code),
       '{"object_level_code":"ASSEMBLY"}'::jsonb,'rc2.35 对象层级统一为零件、组件'
FROM basic_drawing_family WHERE object_level_code IN ('MODULE','EQUIPMENT','END_ITEM');
INSERT INTO audit_log(username,action,object_type,object_id,old_value,new_value,reason)
SELECT 'SYSTEM','OBJECT_LEVEL_SIMPLIFY','PART_NUMBER',id,
       jsonb_build_object('object_level_code',object_level_code),
       '{"object_level_code":"ASSEMBLY"}'::jsonb,'rc2.35 对象层级统一为零件、组件'
FROM part_number WHERE object_level_code IN ('MODULE','EQUIPMENT','END_ITEM');
UPDATE basic_drawing_family SET object_level_code='ASSEMBLY'
WHERE object_level_code IN ('MODULE','EQUIPMENT','END_ITEM');
UPDATE part_number SET object_level_code='ASSEMBLY'
WHERE object_level_code IN ('MODULE','EQUIPMENT','END_ITEM');
UPDATE object_level SET status=CASE WHEN code IN ('PART','ASSEMBLY') THEN 'ACTIVE' ELSE 'DEPRECATED' END;
ALTER TABLE basic_drawing_family ADD CONSTRAINT ck_family_two_levels CHECK(object_level_code IN ('PART','ASSEMBLY'));
ALTER TABLE part_number ADD CONSTRAINT ck_part_two_levels CHECK(object_level_code IN ('PART','ASSEMBLY'));

INSERT INTO external_part_class(code,name_cn,definition,sort_order,status)
SELECT code,name_cn,definition,sort_order,'ACTIVE' FROM primary_class WHERE code IN ('T1','T2','T3')
ON CONFLICT(code) DO UPDATE SET name_cn=EXCLUDED.name_cn,definition=EXCLUDED.definition,status='ACTIVE';
-- Only unambiguous old categories are converted automatically. Mixed-purpose
-- legacy categories stay recorded but require confirmation before new admission.
CREATE TEMP TABLE external_class_map(old_code text,new_code text);
INSERT INTO external_class_map SELECT unnest(ARRAY['E01','E02','E03','E04','E05','E06','E22','E23','E24','E25','E26','E27','E28']),'T1';
INSERT INTO external_class_map SELECT 'E'||lpad(n::text,2,'0'),'T2' FROM generate_series(8,19) n;
INSERT INTO external_class_map VALUES('E07','T3');
INSERT INTO audit_log(username,action,object_type,object_id,old_value,new_value,reason)
SELECT 'SYSTEM','EXTERNAL_PART_CLASSIFY','EXTERNAL_PART',e.id,
       jsonb_build_object('external_class_code',e.external_class_code),
       jsonb_build_object('external_class_code',m.new_code),'rc2.35 外部件改用一级技术类别'
FROM external_part e JOIN external_class_map m ON m.old_code=e.external_class_code;
UPDATE external_part e SET external_class_code=m.new_code FROM external_class_map m WHERE m.old_code=e.external_class_code;
DROP TABLE external_class_map;
UPDATE external_part_class SET status='DEPRECATED' WHERE code NOT IN ('T1','T2','T3');

CREATE FUNCTION dcms_external_primary_class_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' THEN
        IF NEW.external_class_code=OLD.external_class_code THEN RETURN NEW; END IF;
    END IF;
    IF NEW.external_class_code NOT IN ('T1','T2','T3') THEN
        RAISE EXCEPTION 'DCMS-EXT-CLASS: 外部件只能选择 T1、T2、T3 一级技术类别' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_external_primary_class_only BEFORE INSERT OR UPDATE OF external_class_code
ON external_part FOR EACH ROW EXECUTE FUNCTION dcms_external_primary_class_only();
