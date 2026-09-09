from __future__ import annotations
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []

def ok(cond: bool, msg: str):
    if not cond:
        errors.append(msg)

# UTF-8 readability and migration order
migs = sorted((ROOT / 'db' / 'migrations').glob('*.sql'))
ok(len(migs) == 15, f'expected 15 migrations, found {len(migs)}')
for i, f in enumerate(migs, 1):
    ok(f.name.startswith(f'{i:04d}_'), f'migration order broken: {f.name}')
    try:
        f.read_text(encoding='utf-8-sig')
    except Exception as e:
        errors.append(f'{f.name} is not UTF-8 readable: {e}')

ps = (ROOT/'windows'/'install-oneclick.ps1').read_text(encoding='utf-8-sig')
start = (ROOT/'windows'/'start-native.ps1').read_text(encoding='utf-8-sig')
migrate = (ROOT/'windows'/'migrate-native.ps1').read_text(encoding='utf-8-sig')
iss = (ROOT/'installer'/'UG-DCMS-Setup.iss').read_text(encoding='utf-8-sig')
build = (ROOT/'installer'/'Build-Setup.ps1').read_text(encoding='utf-8-sig')

checks = {
    'versioned runtime pointer': 'CURRENT-RUNTIME.txt' in ps and 'CURRENT-RUNTIME.txt' in start,
    'versioned release pointer': 'CURRENT-RELEASE.txt' in ps and 'CURRENT-RELEASE.txt' in start,
    'versioned runtime name': 'venv-1.0.0-rc2.12-' in ps,
    'versioned release name': 'app-1.0.0-rc2.12-' in ps,
    'Inno payload staging': 'DestDir: "{app}\\payload\\backend"' in iss,
    'no Inno in-place backend': 'DestDir: "{app}\\backend"' not in iss,
    'migration runs from new release': '-InstallDir $newRelease' in ps,
    'frontend root points at new release': 'DCMS_FRONTEND_ROOT=$newRelease\\frontend' in ps,
    'UTF8 psql client': "PGCLIENTENCODING='UTF8'" in ps and "PGCLIENTENCODING='UTF8'" in migrate,
    'migration stops on error': 'ON_ERROR_STOP=1' in migrate,
    'migration count gate': '数据库迁移完整性检查通过：15/15' in ps,
    'strict HTTP health': 'HTTP 健康检查通过' in ps,
    'upgrade pointer rollback': '已恢复上一版本 Release/Runtime 指针' in ps,
    'legacy start script rollback': '$previousStartNativeContent' in ps,
    'runtime import checks app': 'import app.main' in ps,
    'Windows wheel target cp312': '--python-version 3.12' in build and '--platform win_amd64' in build and '--abi cp312' in build,
    'offline pip bootstrap': "'-m','ensurepip','--upgrade'" in ps,
    'wheel SHA256 manifest': 'SHA256SUMS.txt' in ps and 'SHA256SUMS.txt' in build and 'Get-FileHash' in ps,
    'permanent log': "Join-Path $InstallDir 'logs'" in ps and 'LAST-ERROR.txt' in ps,
    'missing ExitCode compatibility': 'Windows 未提供退出码，继续执行后续结果校验' in ps,
}
for name, cond in checks.items(): ok(cond, name)

# Ordering invariants
try:
    mig_done = ps.index("Write-Step '数据库迁移完整性检查通过：15/15（含 50,000 条综合业务数据）'")
    switch_runtime = ps.index('Move-Item -Path $tmpRuntimeFile')
    switch_release = ps.index('Move-Item -Path $tmpReleaseFile')
    service = ps.index("Write-Step '注册 UG-DCMS 应用 Windows 服务...'")
    http_ok = ps.index("Write-Step 'HTTP 健康检查通过'")
    cleanup_release = ps.index('清理旧应用 Release')
    cleanup_legacy = ps.index("$legacyVenv = Join-Path $InstallDir 'venv'")
    ok(mig_done < switch_runtime <= switch_release < service < http_ok < cleanup_release, 'transaction ordering release/runtime/service/health')
    ok(http_ok < cleanup_legacy, 'legacy venv cleanup only after health')
except ValueError as e:
    errors.append(f'ordering marker missing: {e}')

if errors:
    print('INSTALLER SOURCE AUDIT: FAIL')
    for e in errors: print(' -', e)
    sys.exit(1)
print('INSTALLER SOURCE AUDIT: PASS')
for name in checks: print(' [PASS]', name)
print(f' [PASS] migrations UTF-8/order: {len(migs)}/15')
