"""数据库与附件一致性备份、校验、定时任务及受控恢复。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
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
    return e


def _sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024), b""): h.update(b)
    return h.hexdigest()


def create_backup(reason: str = "MANUAL") -> dict:
    s=get_settings(); stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final=_root()/f"UG-DCMS-BACKUP-{stamp}.zip"
    with tempfile.TemporaryDirectory(dir=_root()) as td:
        tmp=Path(td); dump=tmp/"database.dump"
        cmd=[_pg("pg_dump"),"-Fc","-h",s.pg_host,"-p",str(s.pg_port),"-U",s.pg_user,
             "-d",s.pg_database,"-f",str(dump)]
        subprocess.run(cmd,env=_env(),check=True,capture_output=True,text=True)
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
    (_root()/"pending-restore.json").write_text(json.dumps({"filename":filename,"queued_at":datetime.now(timezone.utc).isoformat()}),encoding="utf-8")
    return info


def apply_pending_restore() -> None:
    root=_root(); pending=root/"pending-restore.zip"; marker=root/"pending-restore.json"
    if not pending.exists() or not marker.exists(): return
    validate_backup(pending); create_backup("PRE_RESTORE")
    s=get_settings()
    with tempfile.TemporaryDirectory(dir=root) as td:
        tmp=Path(td)
        with zipfile.ZipFile(pending) as z: z.extractall(tmp)
        cmd=[_pg("pg_restore"),"--clean","--if-exists","--no-owner","--single-transaction",
             "-h",s.pg_host,"-p",str(s.pg_port),"-U",s.pg_user,"-d",s.pg_database,str(tmp/"database.dump")]
        subprocess.run(cmd,env=_env(),check=True,capture_output=True,text=True)
        staged=tmp/"files"; live=Path(s.storage_root); old=live.with_name(live.name+".pre-restore")
        if old.exists(): shutil.rmtree(old)
        if live.exists(): os.replace(live,old)
        if staged.exists(): shutil.copytree(staged,live)
        else: live.mkdir(parents=True,exist_ok=True)
    pending.rename(root/("applied-"+pending.name)); marker.unlink(missing_ok=True)


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
