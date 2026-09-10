"""文件存储 — SRS §13 / DD §9 / AC-DATA-05。

DD §9 明确要求"系统只用 StorageKey 抽象寻址, 不保存人工路径"。这条约束的意义在于:
一旦库里存的是 `\\\\fileserver\\设计部\\2026\\图纸\\xxx.pdf` 这样的路径, 任何一次
共享目录改名、盘符调整或服务器迁移都会让全部历史记录同时失效, 而且没人能判断
究竟哪些文件真的丢了、哪些只是路径变了。

StorageKey 由系统按内容与身份生成, 与物理布局解耦; 物理根目录换位置只需改一个配置。

写入采用"先写临时文件 → 校验摘要 → 原子改名"三步。中途失败留下的是临时文件而不是
一个内容不完整、却已被登记进数据库的正式附件 —— 后者会让 AT-007 的完整性巡检
报出无法解释的 MISMATCH。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import get_settings

_SAFE = re.compile(r"[^0-9A-Za-z._\-]")


@dataclass
class StoredFile:
    storage_key: str
    sha256: str
    size_bytes: int


def _root() -> Path:
    p = Path(get_settings().storage_root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve(storage_key: str) -> Path:
    """把 StorageKey 解析为绝对路径, 并确保不会跳出存储根。

    storage_key 最终来源于上传请求, 若不做限制, 形如 `../../etc/passwd` 的键
    可以让写入落到存储根之外。
    """
    root = _root().resolve()
    target = (root / storage_key).resolve()
    if not str(target).startswith(str(root) + os.sep):
        raise ValueError(f"非法 StorageKey: {storage_key}")
    return target


def make_key(file_number: str, revision_number: str, role: str, filename: str) -> str:
    """生成 StorageKey。

    结构 files/<文件号>/<版次>/<用途>/<安全文件名>, 人可读只是副产品 ——
    真正的作用是同一版次同一用途下天然唯一, 配合 uq_revision_attachment_key
    使"同一版次重复上传同一角色附件"在数据库层就被拦下。
    """
    safe_num = _SAFE.sub("_", file_number)
    safe_rev = _SAFE.sub("_", revision_number)
    safe_name = _SAFE.sub("_", filename)[-120:] or "file"
    return f"files/{safe_num}/R{safe_rev}/{role}/{safe_name}"


def make_software_key(software_number: str, version_id: str, filename: str) -> str:
    safe_num = _SAFE.sub("_", software_number)
    safe_id = _SAFE.sub("_", version_id)
    safe_name = _SAFE.sub("_", filename)[-120:] or "software-package.zip"
    return f"software/{safe_num}/{safe_id}/{safe_name}"


def save(storage_key: str, content: bytes) -> StoredFile:
    """原子写入并返回摘要。目标已存在时拒绝覆盖 — INV-007。"""
    target = _resolve(storage_key)
    if target.exists():
        raise FileExistsError(
            f"StorageKey 已存在, 不得覆盖已有文件内容 (INV-007): {storage_key}")
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256(content).hexdigest()
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # 落盘后重新读出核对, 确认写入过程没有出错再登记为正式文件
        if hashlib.sha256(Path(tmp).read_bytes()).hexdigest() != digest:
            raise IOError("写入后校验不一致, 已放弃该文件")
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return StoredFile(storage_key=storage_key, sha256=digest, size_bytes=len(content))


def read(storage_key: str) -> bytes:
    return _resolve(storage_key).read_bytes()


def exists(storage_key: str) -> bool:
    return _resolve(storage_key).exists()


def delete(storage_key: str) -> None:
    _resolve(storage_key).unlink(missing_ok=True)


def compute_sha256(storage_key: str) -> str | None:
    """按块读取计算摘要。图纸与三维模型可能很大, 不整体读入内存。"""
    path = _resolve(storage_key)
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def size_of(storage_key: str) -> int | None:
    path = _resolve(storage_key)
    return path.stat().st_size if path.exists() else None
