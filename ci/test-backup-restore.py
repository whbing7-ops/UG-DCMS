"""恢复错误诊断回归；--postgres 在 CI 专用数据库验证真实备份/失败回滚/恢复。"""
import argparse
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


def postgres_roundtrip():
    """仅由隔离的 CI job 调用：使用它的临时数据库，绝不连接生产配置。"""
    import psycopg
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('真实数据库回归只能在隔离 CI 中运行')
    settings = backups.get_settings()
    with psycopg.connect(settings.dsn, autocommit=True) as conn:
        # 备份持有原始数据；新增不在备份内的引用表重现新版 schema 的依赖冲突。
        conn.execute('CREATE TABLE restore_probe (id integer PRIMARY KEY, label text NOT NULL)')
        conn.execute("INSERT INTO restore_probe VALUES (1, '备份中的值')")
        files = Path(settings.storage_root)
        files.mkdir(parents=True, exist_ok=True)
        payload = files / 'restore-probe.bin'
        payload.write_bytes(b'original attachment\x00\xff')
        backup = backups.create_backup('RESTORE_REGRESSION')
        content = (backups._root() / backup['filename']).read_bytes()
        conn.execute('CREATE TABLE restore_new_reference (id integer REFERENCES restore_probe(id))')
        conn.execute('INSERT INTO restore_new_reference VALUES (1)')
        conn.execute("UPDATE restore_probe SET label='恢复前的值'")
        payload.write_bytes(b'before failed restore')
        backups.queue_restore(content, backup['filename'], '恢复UG-DCMS')
        backups.apply_pending_restore()
        state = backups.restore_status()
        assert state['state'] == 'FAILED' and state['progress'] == 45, state
        log = (backups._root() / state['diagnostic_log']).read_text(encoding='utf-8')
        assert 'other objects depend on it' in log, log
        assert 'other objects depend on it' not in json.dumps(state)
        assert conn.execute('SELECT label FROM restore_probe').fetchone()[0] == '恢复前的值'
        assert payload.read_bytes() == b'before failed restore'
        print('PASS: real PostgreSQL dependency failure captured; transaction rolled back; attachments preserved')
        conn.execute('DROP TABLE restore_new_reference')
        backups._restore_status('RESTARTING', 2, '管理员明确重试')
        backups.apply_pending_restore()
        state = backups.restore_status()
        assert state['state'] == 'COMPLETED' and state['progress'] == 100, state
        assert conn.execute('SELECT label FROM restore_probe').fetchone()[0] == '备份中的值'
        assert payload.read_bytes() == b'original attachment\x00\xff'
        assert not (backups._root() / 'pending-restore.json').exists()
        conn.execute('DROP TABLE restore_probe')
        payload.unlink()
        print('PASS: real PostgreSQL and binary attachment restore completed after explicit retry')


if __name__ == '__main__':
    if '--postgres' in sys.argv:
        postgres_roundtrip()
    else:
        unittest.main()
