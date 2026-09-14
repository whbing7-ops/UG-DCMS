-- Only register the feature. Installation never imports or removes business data.
CREATE TABLE IF NOT EXISTS simulation_dataset (
    dataset_code text PRIMARY KEY,
    manifest jsonb NOT NULL CHECK (jsonb_typeof(manifest)='object'),
    imported_by uuid NOT NULL REFERENCES app_user(id),
    imported_at timestamptz NOT NULL DEFAULT now()
);
DROP TRIGGER IF EXISTS trg_simulation_dataset_immutable ON simulation_dataset;
CREATE TRIGGER trg_simulation_dataset_immutable BEFORE UPDATE OR DELETE
    ON simulation_dataset FOR EACH ROW EXECUTE FUNCTION dcms_block_write();
