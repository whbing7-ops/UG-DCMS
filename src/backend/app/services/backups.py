"""数据库与附件一致性备份、校验、定时任务及受控恢复。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings


def _root() -> Path:
    p = Path(get_settings().backup_root); p.mkdir(parents=True, exist_ok=True); return p


def _pg(name: str) -> str:
    s = get_settings()
    suffix = ".exe" if os.name == "nt" else ""
    return str(Path(s.pg_bin) / f"{name}{suffix}") if s.pg_bin else f"{name}{suffix}"


def _env() -> dict:
    s = get_settings(); e = os.environ.copy()
    if s.pg_password: e["PGPASSWORD"] = s.pg_password
    e["PGCONNECT_TIMEOUT"] = "15"
    return e


class BackupCommandError(RuntimeError):
    def __init__(self, message: str, log_name: str):
        super().__init__(message)
        self.log_name = log_name


def _diagnostic_log(operation: str, detail: str) -> str:
    """详细输出只保存在本机并供管理员下载，不进入公开状态接口。"""
    name = f"restore-log-{uuid.uuid4().hex}.log"
    password = get_settings().pg_password
    if password:
        detail = detail.replace(password, "[口令已隐藏]")
    (_root() / name).write_text(
        f"时间：{datetime.now(timezone.utc).isoformat()}\n操作：{operation}\n{detail}\n",
        encoding="utf-8",
    )
    return name


def _run_pg(cmd: list[str], operation: str) -> None:
    # 保留原始字节后解码，避免中文 Windows 的默认编码再次掩盖数据库错误。
    try:
        result = subprocess.run(cmd, env=_env(), capture_output=True, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stderr or b"") + b"\n" + (exc.stdout or b"")
        log = _diagnostic_log(operation, "执行超过30分钟，已终止子进程。\n" + raw.decode("utf-8", errors="replace"))
        raise BackupCommandError(f"{operation}超时，已停止执行。请下载恢复诊断日志查看详情。", log) from exc
    except OSError as exc:
        log = _diagnostic_log(operation, str(exc))
        raise BackupCommandError(f"无法启动{operation}工具，请检查数据库工具路径及访问权限。", log) from exc
    if result.returncode:
        raw = result.stderr + b"\n" + result.stdout
        try:
            detail = raw.decode("utf-8")
        except UnicodeDecodeError:
            detail = raw.decode("gb18030", errors="replace")
        log = _diagnostic_log(operation, f"退出码：{result.returncode}\n{detail}")
        lower = detail.lower()
        if "other objects depend on it" in lower:
            reason = "现有数据库存在阻止恢复的对象依赖，可能涉及不同版本的表结构"
        elif "permission denied" in lower or "must be owner" in lower:
            reason = "数据库账户没有执行本次操作所需的权限"
        elif "unsupported version" in lower:
            reason = "数据库工具不支持此备份文件的格式版本"
        elif "authentication failed" in lower:
            reason = "数据库身份验证失败"
        elif "connection refused" in lower or "could not connect" in lower:
            reason = "无法连接数据库服务"
        else:
            reason = "数据库工具执行失败"
        raise BackupCommandError(f"{operation}失败：{reason}（退出码 {result.returncode}）。请下载恢复诊断日志查看详情。", log)


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()


def _restore_status(state: str, progress: int, message: str, **extra) -> dict:
    data={"state":state,"progress":progress,"message":message,
          "updated_at":datetime.now(timezone.utc).isoformat(),**extra}
    path=_root()/"restore-status.json"; part=path.with_suffix(".part")
    part.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
    os.replace(part,path)
    return data


def restore_status() -> dict:
    path=_root()/"restore-status.json"
    if not path.exists(): return {"state":"IDLE","progress":0,"message":"当前没有恢复任务"}
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
        # 如果 API 仍能响应而“正在重启”超过一分钟，说明服务退出机制没有生效。
        # 将其转为明确失败，避免客户端永远停留在 2%。真正重启期间 API 不可达；
        # 新进程启动后会立刻把状态推进为 RUNNING。
        if data.get("state")=="RESTARTING":
            updated=datetime.fromisoformat(str(data.get("updated_at","")))
            if (datetime.now(timezone.utc)-updated).total_seconds()>60:
                return _restore_status("FAILED",2,
                    "应用服务未在60秒内重启。请确认 UGDCMS-App 服务的故障恢复设置后重试。",
                    **{k:v for k,v in data.items() if k not in {"state","progress","message","updated_at"}})
        return data
    except Exception: return {"state":"UNKNOWN","progress":0,"message":"恢复状态文件不可读"}


def create_backup(reason: str = "MANUAL") -> dict:
    s=get_settings(); stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final=_root()/f"UG-DCMS-BACKUP-{stamp}.zip"
    with tempfile.TemporaryDirectory(dir=_root()) as td:
        tmp=Path(td); dump=tmp/"database.dump"
        cmd=[_pg("pg_dump"),"-Fc","-h",s.pg_host,"-p",str(s.pg_port),"-U",s.pg_user,
             "-d",s.pg_database,"-f",str(dump)]
        _run_pg(cmd, "数据库备份")
        files=Path(s.storage_root)
        manifest={"format":"UG-DCMS-BACKUP","version":1,"created_at":stamp,
                  "reason":reason,"database_sha256":_sha(dump),"file_count":0}
        with zipfile.ZipFile(final.with_suffix(".part"),"w",zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            z.write(dump,"database.dump")
            if files.exists():
                for p in files.rglob("*"):
                    if p.is_file(): z.write(p,"files/"+p.relative_to(files).as_posix()); manifest["file_count"]+=1
            z.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        os.replace(final.with_suffix(".part"),final)
    prune(); return {**manifest,"filename":final.name,"size_bytes":final.stat().st_size}


def list_backups() -> list[dict]:
    out=[]
    for p in sorted(_root().glob("UG-DCMS-BACKUP-*.zip"),reverse=True):
        try:
            with zipfile.ZipFile(p) as z: m=json.loads(z.read("manifest.json"))
            out.append({**m,"filename":p.name,"size_bytes":p.stat().st_size})
        except Exception: out.append({"filename":p.name,"size_bytes":p.stat().st_size,"invalid":True})
    return out


def validate_backup(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        names=set(z.namelist())
        if not {"manifest.json","database.dump"}<=names: raise ValueError("不是完整的UG-DCMS备份集")
        for name in names:
            candidate = Path(name)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError("备份包包含不安全的文件路径")
        m=json.loads(z.read("manifest.json"))
        if m.get("format")!="UG-DCMS-BACKUP" or m.get("version")!=1: raise ValueError("备份格式或版本不兼容")
        digest=hashlib.sha256(z.read("database.dump")).hexdigest()
        if digest!=m.get("database_sha256"): raise ValueError("数据库备份摘要校验失败")
        return {**m,"filename":path.name,"valid":True}


def queue_restore(content: bytes, filename: str, confirmation: str) -> dict:
    if confirmation != "恢复UG-DCMS": raise ValueError("恢复确认文字不正确")
    target=_root()/"pending-restore.zip"; part=target.with_suffix(".part")
    part.write_bytes(content); info=validate_backup(part); os.replace(part,target)
    queued_at=datetime.now(timezone.utc).isoformat()
    (_root()/"pending-restore.json").write_text(json.dumps({"filename":filename,"queued_at":queued_at}),encoding="utf-8")
    _restore_status("QUEUED",0,"备份包已校验，等待执行",filename=filename,queued_at=queued_at)
    return info


def apply_pending_restore() -> None:
    root=_root(); pending=root/"pending-restore.zip"; marker=root/"pending-restore.json"
    if not pending.exists() or not marker.exists(): return
    # 失败任务只能由管理员明确重试，避免每次启动都重复覆盖数据库。
    if restore_status().get("state") == "FAILED": return
    meta={}
    try:
        meta=json.loads(marker.read_text(encoding="utf-8"))
        _restore_status("RUNNING",5,"正在校验备份包",**meta)
        validate_backup(pending)
        _restore_status("RUNNING",15,"正在创建恢复前安全备份",**meta)
        create_backup("PRE_RESTORE")
        s=get_settings()
        with tempfile.TemporaryDirectory(dir=root) as td:
            tmp=Path(td); _restore_status("RUNNING",30,"正在解压数据库和附件",**meta)
            with zipfile.ZipFile(pending) as z: z.extractall(tmp)
            _restore_status("RUNNING",45,"正在恢复数据库",**meta)
            cmd=[_pg("pg_restore"),"--clean","--if-exists","--no-owner","--single-transaction",
                 "-h",s.pg_host,"-p",str(s.pg_port),"-U",s.pg_user,"-d",s.pg_database,str(tmp/"database.dump")]
            _run_pg(cmd, "数据库恢复")
            _restore_status("RUNNING",80,"数据库恢复完成，正在恢复附件",**meta)
            staged=tmp/"files"; live=Path(s.storage_root); old=live.with_name(live.name+".pre-restore")
            if old.exists(): shutil.rmtree(old)
            if live.exists(): os.replace(live,old)
            try:
                if staged.exists(): shutil.copytree(staged,live)
                else: live.mkdir(parents=True,exist_ok=True)
            except Exception:
                if live.exists(): shutil.rmtree(live)
                if old.exists(): os.replace(old,live)
                raise
        # Windows 的 Path.rename 在目标存在时会报 WinError 183。每次恢复使用
        # 排队时间生成唯一归档名，并以 os.replace 完成原子移动；重复执行同一任务
        # 也不会因为上一次留下的固定文件名而失败。
        queued=str(meta.get("queued_at") or datetime.now(timezone.utc).isoformat())
        suffix="".join(c for c in queued if c.isdigit())[:20] or datetime.now().strftime("%Y%m%d%H%M%S")
        applied=root/f"applied-pending-restore-{suffix}.zip"
        applied.unlink(missing_ok=True)
        os.replace(pending,applied); marker.unlink(missing_ok=True)
        _restore_status("COMPLETED",100,"恢复完成，可以重新登录系统",
                        completed_at=datetime.now(timezone.utc).isoformat(),**meta)
    except Exception as exc:
        last=restore_status()
        log_name=getattr(exc,"log_name",None) or _diagnostic_log("系统恢复",traceback.format_exc())
        message=str(exc) if isinstance(exc, BackupCommandError) else "恢复未完成，请下载恢复诊断日志查看详情。"
        _restore_status("FAILED",min(99,int(last.get("progress",0))),message,
                        failed_stage=last.get("message","准备恢复"),diagnostic_log=log_name,**meta)


def schedule() -> dict:
    p=_root()/"schedule.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"enabled":False,"frequency":"DAILY","hour":2,"retention":14}


def save_schedule(data: dict) -> dict:
    if data["frequency"] not in {"DAILY","WEEKLY"} or not 0<=int(data["hour"])<=23: raise ValueError("备份频率或小时无效")
    (_root()/"schedule.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return data


def prune() -> None:
    keep=max(1,int(schedule().get("retention",14)))
    for p in sorted(_root().glob("UG-DCMS-BACKUP-*.zip"),reverse=True)[keep:]: p.unlink(missing_ok=True)


async def scheduler(stop: asyncio.Event) -> None:
    last=""
    while not stop.is_set():
        now=datetime.now(); cfg=schedule(); key=now.strftime("%Y-%m-%d-%H")
        weekly_ok=cfg.get("frequency")!="WEEKLY" or now.weekday()==int(cfg.get("weekday",6))
        if cfg.get("enabled") and now.hour==int(cfg.get("hour",2)) and weekly_ok and key!=last:
            # 一次备份失败不能终止调度器；同一小时不反复重试，避免故障时持续占用资源。
            try:
                await asyncio.to_thread(create_backup,"SCHEDULED")
            except Exception:
                pass
            finally:
                last=key
        try: await asyncio.wait_for(stop.wait(),timeout=60)
        except asyncio.TimeoutError: pass
