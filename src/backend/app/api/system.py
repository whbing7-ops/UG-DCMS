"""健康检查与系统自证端点。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..db import fetch_all, fetch_one, scalar
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from .. import audit, errors
from ..services import backups

router = APIRouter(tags=["系统"], route_class=TransactionalRoute)


class BackupScheduleRequest(BaseModel):
    enabled: bool
    frequency: str = Field(pattern="^(DAILY|WEEKLY)$")
    hour: int = Field(ge=0, le=23)
    weekday: int = Field(default=6, ge=0, le=6)
    retention: int = Field(default=14, ge=1, le=365)


@router.get("/health")
def health(conn: Conn):
    """存活探针。不需要认证, 供 Docker healthcheck 与反向代理使用。"""
    ok = scalar(conn, "SELECT 1") == 1
    return {"status": "ok" if ok else "degraded"}


@router.get("/system/invariants")
def invariants(conn: Conn, user: CurrentUser):
    """列出系统不变量及其强制手段 — 支撑 IVV §8 的可追溯性要求。

    这个端点存在的意义是: 验收时不必翻源码, 直接问系统"你都强制了哪些规则、
    分别靠什么强制"。
    """
    return fetch_all(conn, """
        SELECT code, statement_cn, enforced_by, enforcement_ref, test_ref
          FROM invariant_registry ORDER BY code
    """)


@router.get("/system/migrations")
def migrations(conn: Conn, user: CurrentUser):
    """已应用的数据库迁移版本 — IVV §8 要求 Migration 版本可追溯。"""
    exists = scalar(conn, """
        SELECT EXISTS (SELECT 1 FROM information_schema.tables
                        WHERE table_name = 'schema_migration')
    """)
    if not exists:
        return []
    return fetch_all(conn, """
        SELECT version, filename, sha256, applied_at, applied_by, duration_ms
          FROM schema_migration ORDER BY version
    """)


@router.get("/system/dictionary-status")
def dictionary_status(conn: Conn, user: CurrentUser):
    """字典装载自检: 条目数与占位残留。上线前应确认占位数为 0。"""
    return fetch_one(conn, """
        SELECT (SELECT count(*) FROM physical_class)    AS physical_class,
               (SELECT count(*) FROM naming_core_term)  AS core_term,
               (SELECT count(*) FROM naming_qualifier)  AS qualifier,
               (SELECT count(*) FROM function_domain)   AS function_domain,
               (SELECT count(*) FROM function_item)     AS function_item,
               (SELECT count(*) FROM restricted_term)   AS restricted_term,
               (SELECT count(*) FROM physical_class   WHERE definition LIKE '[占位]%%')
             + (SELECT count(*) FROM naming_core_term WHERE definition LIKE '[占位]%%')
             + (SELECT count(*) FROM naming_qualifier WHERE definition LIKE '[占位]%%')
             + (SELECT count(*) FROM function_item    WHERE definition LIKE '[占位]%%')
                                                            AS placeholder_remaining
    """)


@router.get("/system/backups")
def backup_list(conn: Conn, actor: dict = Depends(require(Perm.SYSTEM_SETTING))):
    marker=Path(backups._root())/"pending-restore.json"
    pending=json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else None
    return {"schedule": backups.schedule(), "backups": backups.list_backups(),
            "restore_pending": pending, "restore_status":backups.restore_status()}


@router.get("/system/restore/status")
def restore_status():
    """恢复期间数据库和登录会话可能暂不可用，因此状态探针不依赖数据库或认证。"""
    return backups.restore_status()


@router.post("/system/backups", status_code=201)
def backup_now(conn: Conn, actor: dict = Depends(require(Perm.SYSTEM_SETTING))):
    try: result=backups.create_backup("MANUAL")
    except Exception as e: raise errors.bad_request(f"备份失败: {e}")
    audit.write(conn,action="SYSTEM_BACKUP_CREATE",user_id=str(actor["user_id"]),
      username=actor["username"],object_type="SYSTEM_BACKUP",object_code=result["filename"],
      new_value=result,session_id=str(actor.get("session_id")),client_ip=actor.get("client_ip"))
    return result


@router.get("/system/backups/{filename}/download")
def backup_download(filename: str, conn: Conn,
                    actor: dict = Depends(require(Perm.SYSTEM_SETTING))):
    safe=Path(filename).name; path=backups._root()/safe
    if safe!=filename or not path.is_file(): raise errors.not_found("备份不存在")
    return FileResponse(path, media_type="application/zip", filename=safe)


@router.put("/system/backup-schedule")
def backup_schedule(payload: BackupScheduleRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.SYSTEM_SETTING))):
    data=backups.save_schedule(payload.model_dump())
    audit.write(conn,action="BACKUP_SCHEDULE_UPDATE",user_id=str(actor["user_id"]),
      username=actor["username"],object_type="SYSTEM_SETTING",object_code="backup_schedule",
      new_value=data,session_id=str(actor.get("session_id")),client_ip=actor.get("client_ip"))
    return data


@router.post("/system/restore", status_code=202)
async def restore(conn: Conn, file: UploadFile=File(...), confirmation: str=Form(...),
                  actor: dict=Depends(require(Perm.SYSTEM_SETTING))):
    content=await file.read()
    if len(content)>5*1024*1024*1024: raise errors.bad_request("备份文件超过5GB上限")
    try: info=backups.queue_restore(content,file.filename or "backup.zip",confirmation)
    except Exception as e: raise errors.bad_request(f"恢复包校验失败: {e}")
    audit.write(conn,action="SYSTEM_RESTORE_QUEUED",user_id=str(actor["user_id"]),
      username=actor["username"],object_type="SYSTEM_BACKUP",object_code=file.filename,
      new_value=info,reason="管理员确认恢复",session_id=str(actor.get("session_id")),client_ip=actor.get("client_ip"))
    return {"message":"恢复任务已校验并排队；重启UG-DCMS服务后自动执行，执行前会先生成恢复前备份",**info}


@router.delete("/system/restore")
def cancel_restore(conn: Conn, actor: dict=Depends(require(Perm.SYSTEM_SETTING))):
    root=Path(backups._root()); removed=False
    for name in ("pending-restore.zip","pending-restore.json"):
        p=root/name
        if p.exists(): p.unlink(); removed=True
    audit.write(conn,action="SYSTEM_RESTORE_CANCEL",user_id=str(actor["user_id"]),
      username=actor["username"],object_type="SYSTEM_BACKUP",object_code="pending-restore",
      new_value={"removed":removed},session_id=str(actor.get("session_id")),client_ip=actor.get("client_ip"))
    backups._restore_status("CANCELLED",0,"待执行的恢复任务已取消")
    return {"message":"待执行的恢复任务已取消"}


@router.post("/system/restore/apply", status_code=202)
def apply_restore(conn: Conn, actor: dict=Depends(require(Perm.SYSTEM_SETTING))):
    root=Path(backups._root())
    if not (root/"pending-restore.zip").exists() or not (root/"pending-restore.json").exists():
        raise errors.bad_request("没有等待执行的恢复任务")
    if os.name != "nt": raise errors.bad_request("立即执行恢复仅适用于Windows服务部署")
    audit.write(conn,action="SYSTEM_RESTORE_APPLY",user_id=str(actor["user_id"]),
      username=actor["username"],object_type="SYSTEM_BACKUP",object_code="pending-restore",
      reason="管理员确认立即重启并恢复",session_id=str(actor.get("session_id")),client_ip=actor.get("client_ip"))
    marker=json.loads((root/"pending-restore.json").read_text(encoding="utf-8"))
    backups._restore_status("RESTARTING",2,"正在重启应用服务，准备执行恢复",**marker)
    command="Start-Sleep -Seconds 3; Restart-Service -Name 'UGDCMS-App' -Force"
    flags=getattr(subprocess,"DETACHED_PROCESS",0) | getattr(subprocess,"CREATE_NEW_PROCESS_GROUP",0)
    subprocess.Popen(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-Command",command],
                     creationflags=flags,close_fds=True)
    return {"message":"服务将在3秒后重启并执行恢复，请约1分钟后刷新页面"}
