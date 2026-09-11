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
ok(len(migs) == 20, f'expected 20 migrations, found {len(migs)}')
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
extras = (ROOT/'frontend'/'js'/'extras.js').read_text(encoding='utf-8-sig')
ui = (ROOT/'frontend'/'js'/'ui.js').read_text(encoding='utf-8-sig')
system_api = (ROOT/'backend'/'app'/'api'/'system.py').read_text(encoding='utf-8-sig')
backup_service = (ROOT/'backend'/'app'/'services'/'backups.py').read_text(encoding='utf-8-sig')
main_api = (ROOT/'backend'/'app'/'main.py').read_text(encoding='utf-8-sig')
index_html = (ROOT/'frontend'/'index.html').read_text(encoding='utf-8-sig')
purge_migration = (ROOT/'db'/'migrations'/'0017_purge_business_data_keep_accounts.sql').read_text(encoding='utf-8-sig')
software_migration = (ROOT/'db'/'migrations'/'0018_software_package_attachment.sql').read_text(encoding='utf-8-sig')
external_api = (ROOT/'backend'/'app'/'api'/'externals.py').read_text(encoding='utf-8-sig')
bom_api = (ROOT/'backend'/'app'/'api'/'bom.py').read_text(encoding='utf-8-sig')
views3 = (ROOT/'frontend'/'js'/'views3.js').read_text(encoding='utf-8-sig')
views5 = (ROOT/'frontend'/'js'/'views5.js').read_text(encoding='utf-8-sig')

checks = {
    'versioned runtime pointer': 'CURRENT-RUNTIME.txt' in ps and 'CURRENT-RUNTIME.txt' in start,
    'versioned release pointer': 'CURRENT-RELEASE.txt' in ps and 'CURRENT-RELEASE.txt' in start,
    'versioned runtime name': 'venv-1.0.0-rc2.34-' in ps,
    'versioned release name': 'app-1.0.0-rc2.34-' in ps,
    'stale app process cleanup': 'Stop-StaleAppProcesses' in ps,
    'application port owner check': 'Get-PortOwner $AppPort' in ps,
    'service failure log tail': 'UGDCMS-App.err.log' in ps and 'Get-Content $path -Tail 30' in ps,
    'Inno payload staging': 'DestDir: "{app}\\payload\\backend"' in iss,
    'no Inno in-place backend': 'DestDir: "{app}\\backend"' not in iss,
    'migration runs from new release': '-InstallDir $newRelease' in ps,
    'frontend root points at new release': 'DCMS_FRONTEND_ROOT=$newRelease\\frontend' in ps,
    'UTF8 psql client': "PGCLIENTENCODING='UTF8'" in ps and "PGCLIENTENCODING='UTF8'" in migrate,
    'migration stops on error': 'ON_ERROR_STOP=1' in migrate,
    'migration count gate': '数据库迁移完整性检查通过：20/20' in ps,
    'strict HTTP health': 'HTTP 健康检查通过' in ps,
    'health route matches backend': '/api/v1/health' in ps and '/api/v1/system/health' not in ps,
    'upgrade-safe env permissions': "'*S-1-5-32-544:(F)'" in ps and 'attrib.exe -R' in ps,
    'backup page table component imported': 'table, tablePanel, toast' in extras,
    'Chinese status presentation': "IN_REVIEW:'审核中'" in ui and 'statusText(v)' in ui,
    'two-step restore controls': '立即重启并执行恢复' in extras and '/system/restore/apply' in extras,
    'restore apply and cancel API': '@router.post("/system/restore/apply"' in system_api and '@router.delete("/system/restore"' in system_api,
    'restore numeric progress UI': 'restore-progress-line' in extras and "percent.textContent=value+'%'" in extras,
    'restore completion UI': "title.textContent='恢复完成'" in extras and '恢复完成（100%）' in extras,
    'restore status probe': '@router.get("/system/restore/status")' in system_api,
    'restore uses service supervisor': 'os._exit(75)' in system_api
        and 'UGDCMS-Restore-Restart' in system_api
        and 'Register-ScheduledTask' not in system_api,
    'service wrapper receives native exit code': 'exit $LASTEXITCODE' in start,
    'restore archive is collision-safe': 'applied-pending-restore-{suffix}.zip' in backup_service
        and 'os.replace(pending,applied)' in backup_service,
    'frontend upgrade invalidates cache': 'FreshStaticFiles' in main_api
        and 'no-store, max-age=0' in main_api and '?v=rc2.34' in index_html,
    'business purge preserves accounts': 'TRUNCATE TABLE' in purge_migration
        and 'app_user' not in purge_migration.split('TRUNCATE TABLE', 1)[1].split('RESTART IDENTITY', 1)[0]
        and '账户保护校验失败' in purge_migration,
    'software package upload and automatic digest': 'package_storage_key' in software_migration
        and '/versions/package' in external_api and 'zipfile.is_zipfile' in external_api
        and '软件内容压缩包' in views5,
    'BOM child uses controlled candidate picker': '/bom-candidates/' in bom_api
        and "'INTERNAL_PART','EXTERNAL_PART'" in bom_api and 'selectedChild' in views3,
    'validation prompts are Chinese': 'RequestValidationError' in main_api
        and 'validation_error_handler' in main_api
        and 'String should have at least' not in (ROOT/'frontend'/'js'/'api.js').read_text(encoding='utf-8-sig'),
    'upgrade pointer rollback': '已恢复上一版本 Release/Runtime 指针' in ps,
    'legacy start script rollback': '$previousStartNativeContent' in ps,
    'runtime import checks app': 'verify-runtime.py' in ps and 'RUNTIME-VERIFIED.txt' in ps,
    'psql args are not automatic variable': ('param([string[]]$PsqlArgs' in migrate and '@PsqlArgs' in migrate
                                               and migrate.count('Invoke-Psql -PsqlArgs') == 2),
    'Windows wheel target cp312': '--python-version 3.12' in build and '--platform win_amd64' in build and '--abi cp312' in build,
    'offline pip bootstrap': "'-m','ensurepip','--upgrade'" in ps,
    'wheel SHA256 manifest': 'SHA256SUMS.txt' in ps and 'SHA256SUMS.txt' in build and 'Get-FileHash' in ps,
    'permanent log': "Join-Path $InstallDir 'logs'" in ps and 'LAST-ERROR.txt' in ps,
    'missing ExitCode compatibility': 'Windows 未提供退出码，继续执行后续结果校验' in ps,
}
for name, cond in checks.items(): ok(cond, name)

# Ordering invariants
try:
    mig_done = ps.index("Write-Step '数据库迁移完整性检查通过：20/20（综合演示业务数据已清理，仅保留账户与基础配置）'")
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
print(f' [PASS] migrations UTF-8/order: {len(migs)}/20')
