-- 软件版本与可加载硬件的受控多对多关系。
CREATE TABLE IF NOT EXISTS software_hardware_compatibility (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    software_version_id uuid NOT NULL REFERENCES software_version(id) ON DELETE CASCADE,
    hardware_design_object_id uuid NOT NULL REFERENCES design_object(id),
    hardware_version text NOT NULL,
    applicability_note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    created_by uuid REFERENCES app_user(id),
    CONSTRAINT uq_software_hardware_version UNIQUE
      (software_version_id, hardware_design_object_id, hardware_version),
    CONSTRAINT ck_hardware_version_not_blank CHECK (btrim(hardware_version) <> '')
);

CREATE INDEX IF NOT EXISTS idx_sw_hw_hardware
  ON software_hardware_compatibility(hardware_design_object_id, hardware_version);

COMMENT ON TABLE software_hardware_compatibility IS
  '软件版本适装硬件清单；同一软件版本允许关联多个内部件或外部件硬件。';

CREATE OR REPLACE FUNCTION dcms_guard_software_hardware() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE version_status text; hardware_type text;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'DCMS-INV-031: 适装关系请在草稿阶段移除后重新登记';
  END IF;
  IF TG_OP = 'DELETE' THEN
    SELECT status INTO version_status FROM software_version
      WHERE id=OLD.software_version_id FOR UPDATE;
  ELSE
    SELECT status INTO version_status FROM software_version
      WHERE id=NEW.software_version_id FOR UPDATE;
    SELECT object_type INTO hardware_type FROM design_object WHERE id=NEW.hardware_design_object_id;
    IF hardware_type NOT IN ('INTERNAL_PART','EXTERNAL_PART') THEN
      RAISE EXCEPTION 'DCMS-INV-031: 适装目标必须为内部硬件件号或外部件号';
    END IF;
  END IF;
  IF version_status IS DISTINCT FROM 'DRAFT' THEN
    RAISE EXCEPTION 'DCMS-INV-031: 仅草稿软件版本允许维护适装关系';
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_software_hardware_guard ON software_hardware_compatibility;
CREATE TRIGGER trg_software_hardware_guard BEFORE INSERT OR UPDATE OR DELETE
  ON software_hardware_compatibility FOR EACH ROW EXECUTE FUNCTION dcms_guard_software_hardware();
