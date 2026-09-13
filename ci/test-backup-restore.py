"""恢复错误诊断回归；--postgres 在 CI 专用数据库验证真实备份/失败回滚/恢复。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/backend'))
from app.config import Settings
from app.services import backups


class RestoreDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = Settings(backup_root=str(self.root / 'backups'),
                                 storage_root=str(self.root / 'files'),
                                 pg_password='private-test-password')
        self.override = patch.object(backups, 'get_settings', return_value=self.settings)
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.tmp.cleanup()

    def test_real_process_stderr_saved_redacted_and_not_exposed_in_error(self):
        script = "import sys; sys.stderr.write('pg_restore: ERROR: other objects depend on it private-test-password'); sys.exit(1)"
        with self.assertRaises(backups.BackupCommandError) as error:
            backups._run_pg([sys.executable, '-c', script], '数据库恢复')
        detail = (backups._root() / error.exception.log_name).read_text(encoding='utf-8')
        self.assertIn('other objects depend on it', detail)
        self.assertNotIn('private-test-password', detail)
        self.assertIn('对象依赖', str(error.exception))
        self.assertNotIn('pg_restore: ERROR', str(error.exception))

    def test_non_utf8_windows_stderr_is_preserved(self):
        result = subprocess.CompletedProcess([], 1, b'', '数据库访问被拒绝'.encode('gb18030'))
        with patch.object(backups.subprocess, 'run', return_value=result):
            with self.assertRaises(backups.BackupCommandError) as error:
                backups._run_pg(['pg_restore'], '数据库恢复')
        self.assertIn('数据库访问被拒绝', (backups._root() / error.exception.log_name).read_text(encoding='utf-8'))

    def test_timeout_has_diagnostic_and_finite_wait(self):
        with patch.object(backups.subprocess, 'run', side_effect=subprocess.TimeoutExpired('pg_restore', 1800, stderr=b'waiting for lock')) as run:
            with self.assertRaises(backups.BackupCommandError) as error:
                backups._run_pg(['pg_restore'], '数据库恢复')
        self.assertEqual(run.call_args.kwargs['timeout'], 1800)
        self.assertIn('超时', str(error.exception))
        self.assertIn('waiting for lock', (backups._root() / error.exception.log_name).read_text(encoding='utf-8'))

    def test_failed_startup_does_not_retry_without_admin(self):
        root = backups._root()
        (root / 'pending-restore.zip').write_bytes(b'pending')
        (root / 'pending-restore.json').write_text('{}')
        backups._restore_status('FAILED', 45, '数据库恢复失败')
        with patch.object(backups, 'validate_backup') as validate:
            backups.apply_pending_restore()
        validate.assert_not_called()

    def test_corrupt_marker_does_not_crash_app_startup(self):
        root = backups._root()
        (root / 'pending-restore.zip').write_bytes(b'pending')
        (root / 'pending-restore.json').write_text('{broken')
        backups.apply_pending_restore()
        state = backups.restore_status()
        self.assertEqual(state['state'], 'FAILED')
        self.assertLess(state['progress'], 100)
        self.assertTrue((root / state['diagnostic_log']).is_file())

    def test_attachment_copy_failure_never_changes_database_or_live_files(self):
        live = Path(self.settings.storage_root)
        live.mkdir()
        (live / 'original').write_bytes(b'preserved')
        unpacked = self.root / 'unpacked'
        (unpacked / 'files').mkdir(parents=True)
        with patch.object(backups.shutil, 'copytree', side_effect=OSError('disk full')):
            with patch.object(backups, '_restore_database') as restore:
                with self.assertRaises(OSError):
                    backups._restore_payload(unpacked)
                restore.assert_not_called()
        self.assertEqual((live / 'original').read_bytes(), b'preserved')

    def test_attachment_switch_failure_preserves_original_files(self):
        live = Path(self.settings.storage_root)
        live.mkdir()
        (live / 'original').write_bytes(b'preserved')
        unpacked = self.root / 'unpacked'
        (unpacked / 'files').mkdir(parents=True)
        replace = os.replace

        def fail_switch(source, target):
            if '.restore-' in Path(source).name and Path(target) == live:
                raise PermissionError('file in use')
            return replace(source, target)

        with patch.object(backups.os, 'replace', side_effect=fail_switch):
            with patch.object(backups, '_restore_database') as restore:
                with self.assertRaises(PermissionError):
                    backups._restore_payload(unpacked)
                restore.assert_not_called()
        self.assertEqual((live / 'original').read_bytes(), b'preserved')


def postgres_roundtrip():
    """仅由隔离的 CI job 调用：使用它的临时数据库，绝不连接生产配置。"""
    import psycopg
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('真实数据库回归只能在隔离 CI 中运行')
    settings = backups.get_settings()
    with psycopg.connect(settings.dsn, autocommit=True) as conn:
        # 构造包含业务记录的旧版备份，再在目标库新增真实的 0019 外键。
        migrations = backups._migration_files()
        conn.execute('DROP TABLE software_hardware_compatibility')
        conn.execute('DROP TRIGGER trg_external_number_unique ON external_part')
        conn.execute('DROP FUNCTION dcms_guard_external_number()')
        conn.execute('DROP TABLE external_part_number_registry')
        conn.execute('DROP TRIGGER trg_external_primary_class_only ON external_part')
        conn.execute('DROP FUNCTION dcms_external_primary_class_only()')
        conn.execute('ALTER TABLE basic_drawing_family DROP CONSTRAINT ck_family_two_levels')
        conn.execute('ALTER TABLE part_number DROP CONSTRAINT ck_part_two_levels')
        conn.execute("DELETE FROM schema_migration WHERE version >= '0017'")
        for cls in ('E07','E08','E20'):
            oid=conn.execute("INSERT INTO design_object(object_type,object_code,display_name) VALUES('EXTERNAL_PART',%s,'旧分类恢复测试') RETURNING id",('RESTORE-CLASS-'+cls,)).fetchone()[0]
            conn.execute("""INSERT INTO external_part(design_object_id,namespace_id,external_part_number,name_cn,external_class_code)
              VALUES(%s,(SELECT id FROM namespace ORDER BY code LIMIT 1),%s,'旧分类恢复测试',%s)""",(oid,'RESTORE-CLASS-'+cls,cls))
        old_family=conn.execute("""INSERT INTO basic_drawing_family
            (basic_drawing_number,primary_class_code,physical_class_id,object_level_code,
             family_name_cn,family_name_en,core_term_id,family_definition,allowed_variation,excluded_variation)
            VALUES('PENDING-RESTORE-LEVEL','T1',
              (SELECT id FROM physical_class WHERE primary_class_code='T1' AND status='ACTIVE' ORDER BY code LIMIT 1),
              'MODULE','临时','TEMP',
              (SELECT id FROM naming_core_term WHERE primary_class_code='T1' AND status='ACTIVE' ORDER BY code LIMIT 1),
              '旧模块层级','尺寸','原理') RETURNING id""").fetchone()[0]
        part = conn.execute("INSERT INTO design_object(object_type,object_code,display_name) VALUES ('INTERNAL_PART','RESTORE-HW-PROBE','备份中的硬件') RETURNING id").fetchone()[0]
        legacy_part=conn.execute("""INSERT INTO part_number(design_object_id,basic_drawing_family_id,
          dash_number,full_part_number,formal_name_cn,formal_name_en,object_level_code,lifecycle_status)
          VALUES(%s,%s,1,'RESTORE-HW-PROBE','旧模块件号','LEGACY MODULE','MODULE','DRAFT') RETURNING id""",
          (part,old_family)).fetchone()[0]
        migration21=next(m for m in migrations if m.name.startswith('0021_')).read_text(encoding='utf-8-sig')
        original21=migration21.replace('SET CONSTRAINTS trg_part_current_baseline_check IMMEDIATE;', '').replace('SET CONSTRAINTS trg_part_current_baseline_check DEFERRED;', '')
        try:
            with conn.transaction():
                conn.execute(original21)
        except psycopg.errors.ObjectInUse as error:
            assert 'pending trigger events' in str(error) and 'part_number' in str(error), str(error)
        else:
            raise AssertionError('rc2.35 populated-part migration failure was not reproduced')
        assert conn.execute('SELECT object_level_code FROM part_number WHERE id=%s',(legacy_part,)).fetchone()[0]=='MODULE'
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='OBJECT_LEVEL_SIMPLIFY' AND object_id=%s",(str(legacy_part),)).fetchone()[0]==0
        print('PASS: exact rc2.35 pending trigger events reproduced with existing MODULE part; transaction rolled back')
        sw = conn.execute("INSERT INTO design_object(object_type,object_code,display_name) VALUES ('SOFTWARE','RESTORE-SW-PROBE','备份中的软件') RETURNING id").fetchone()[0]
        obj = conn.execute("INSERT INTO software_object(design_object_id,software_number,name_cn,software_type) VALUES (%s,'RESTORE-SW-PROBE','恢复测试软件','FIRMWARE') RETURNING id", (sw,)).fetchone()[0]
        version = conn.execute("INSERT INTO software_version(software_object_id,version) VALUES (%s,'R00') RETURNING id", (obj,)).fetchone()[0]
        accounts = conn.execute('SELECT id,username,password_hash FROM app_user ORDER BY id').fetchall()
        files = Path(settings.storage_root)
        files.mkdir(parents=True, exist_ok=True)
        payload = files / 'restore-probe.bin'
        payload.write_bytes(b'original attachment\x00\xff')
        backup = backups.create_backup('RESTORE_REGRESSION')
        content = (backups._root() / backup['filename']).read_bytes()
        conn.execute(next(m for m in migrations if m.name.startswith('0019_')).read_text(encoding='utf-8-sig'))
        conn.execute("INSERT INTO software_hardware_compatibility(software_version_id,hardware_design_object_id,hardware_version) VALUES (%s,%s,'R00')", (version, part))
        conn.execute("UPDATE design_object SET display_name='恢复前的硬件' WHERE id=%s", (part,))
        payload.write_bytes(b'before restore')
        backups.queue_restore(content, backup['filename'], '恢复UG-DCMS')

        # rc2.32 的原始命令必须确实出现用户日志中的同名约束冲突。
        with tempfile.TemporaryDirectory() as td:
            dump = Path(td) / 'database.dump'
            import zipfile
            with zipfile.ZipFile(backups._root() / 'pending-restore.zip') as z:
                dump.write_bytes(z.read('database.dump'))
            with unittest.TestCase().assertRaises(backups.BackupCommandError) as error:
                backups._run_pg([backups._pg('pg_restore'), '--clean', '--if-exists', '--no-owner', '--single-transaction',
                    '-h', settings.pg_host, '-p', str(settings.pg_port), '-U', settings.pg_user,
                    '-d', settings.pg_database, str(dump)], '旧版恢复重现')
            detail = (backups._root() / error.exception.log_name).read_text(encoding='utf-8')
            assert 'software_version_pkey' in detail and 'other objects depend on it' in detail, detail
        print('PASS: exact rc2.32 software_version_pkey dependency failure reproduced')

        # 在清理 schema、导入备份后故意使迁移出错，必须连表结构和附件一起回滚。
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / '9999_injected_failure.sql'
            bad.write_text('SELECT 1/0;', encoding='utf-8')
            with patch.object(backups, '_migration_files', return_value=migrations + [bad]):
                backups.apply_pending_restore()
        state = backups.restore_status()
        assert state['state'] == 'FAILED' and state['progress'] == 45, state
        detail = (backups._root() / state['diagnostic_log']).read_text(encoding='utf-8')
        assert 'division by zero' in detail, detail
        assert 'division by zero' not in json.dumps(state)
        assert conn.execute('SELECT count(*) FROM software_hardware_compatibility').fetchone()[0] == 1
        assert conn.execute('SELECT display_name FROM design_object WHERE id=%s', (part,)).fetchone()[0] == '恢复前的硬件'
        assert payload.read_bytes() == b'before restore'
        with patch.object(backups, '_restore_payload') as restore:
            backups.apply_pending_restore()
            restore.assert_not_called()
        print('PASS: real migration failure rolls back schema, business data and attachments; no startup retry')

        # 明确重试：保留新增外键，不做人工删除，旧备份应能恢复并自动升级。
        backups._restore_status('RESTARTING', 2, '管理员明确重试')
        backups.apply_pending_restore()
        state = backups.restore_status()
        if state['state'] != 'COMPLETED':
            print((backups._root() / state['diagnostic_log']).read_text(encoding='utf-8'))
        assert state['state'] == 'COMPLETED' and state['progress'] == 100, state
        assert conn.execute('SELECT display_name FROM design_object WHERE id=%s', (part,)).fetchone()[0] == '备份中的硬件'
        assert conn.execute('SELECT count(*) FROM software_version WHERE id=%s', (version,)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM software_hardware_compatibility').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM schema_migration').fetchone()[0] == len(migrations)
        assert conn.execute('SELECT object_level_code FROM basic_drawing_family WHERE id=%s',(old_family,)).fetchone()[0]=='ASSEMBLY'
        assert dict(conn.execute("SELECT external_part_number,external_class_code FROM external_part WHERE external_part_number LIKE 'RESTORE-CLASS-%'").fetchall())=={'RESTORE-CLASS-E07':'T3','RESTORE-CLASS-E08':'T2','RESTORE-CLASS-E20':'E20'}
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='OBJECT_LEVEL_SIMPLIFY' AND object_id=%s",(str(old_family),)).fetchone()[0]==1
        assert conn.execute('SELECT object_level_code FROM part_number WHERE id=%s',(legacy_part,)).fetchone()[0]=='ASSEMBLY'
        assert conn.execute("SELECT count(*) FROM audit_log WHERE action='OBJECT_LEVEL_SIMPLIFY' AND object_id=%s",(str(legacy_part),)).fetchone()[0]==1
        assert conn.execute("SELECT tgdeferrable AND tginitdeferred FROM pg_trigger WHERE tgname='trg_part_current_baseline_check'").fetchone()[0]
        print('PASS: legacy hierarchy and external classes upgrade with audit; ambiguous category retained for confirmation')
        assert conn.execute('SELECT id,username,password_hash FROM app_user ORDER BY id').fetchall() == accounts
        assert payload.read_bytes() == b'original attachment\x00\xff'
        assert not (backups._root() / 'pending-restore.json').exists()
        assert (backups._root() / state['safety_backup']).is_file()
        print('PASS: legacy backup restored with current schema, accounts, software and attachments; purge skipped')

        # 相同版本往返并验证恢复后的兼容关系约束仍可用。
        conn.execute("INSERT INTO software_hardware_compatibility(software_version_id,hardware_design_object_id,hardware_version) VALUES (%s,%s,'R00')", (version, part))
        current = backups.create_backup('CURRENT_ROUNDTRIP')
        backups.queue_restore((backups._root() / current['filename']).read_bytes(), current['filename'], '恢复UG-DCMS')
        backups.apply_pending_restore()
        assert backups.restore_status()['state'] == 'COMPLETED', backups.restore_status()
        assert conn.execute('SELECT count(*) FROM software_hardware_compatibility').fetchone()[0] == 1
        assert payload.read_bytes() == b'original attachment\x00\xff'
        conn.execute('DELETE FROM software_hardware_compatibility WHERE software_version_id=%s', (version,))
        conn.execute('DELETE FROM software_version WHERE id=%s', (version,))
        conn.execute('DELETE FROM software_object WHERE id=%s', (obj,))
        conn.execute('DELETE FROM design_object WHERE id IN (%s,%s)', (part, sw))
        payload.unlink()
        print('PASS: current-version roundtrip preserves hardware/software compatibility')



if __name__ == '__main__':
    if '--postgres' in sys.argv:
        postgres_roundtrip()
    else:
        unittest.main()
