"""设计文件、版次、附件、完整性巡检端点。"""
from __future__ import annotations
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import errors, storage
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import files as file_svc

router = APIRouter(tags=["设计文件"], route_class=TransactionalRoute)

MAX_ATTACHMENT_BYTES = 200 * 1024 * 1024


class FileCreateRequest(BaseModel):
    file_number: str = Field(min_length=1, max_length=64)
    file_type_code: str
    title_cn: str = Field(min_length=1, max_length=256)
    title_en: str | None = Field(default=None, max_length=256)


class RevisionCreateRequest(BaseModel):
    change_summary: str = Field(min_length=1, max_length=1000)


class SubmitRequest(BaseModel):
    approver_user_id: str


class DefinitionLinkRequest(BaseModel):
    object_code: str
    file_number: str
    relation_type: str = Field(pattern="^(PRIMARY_DEFINITION|SUPPORTING_DEFINITION|"
                                       "INTERFACE_DEFINITION|QUALIFICATION_EVIDENCE)$")
    applicability_note: str | None = Field(default=None, max_length=500)


# ---------------- 文件 ----------------
@router.get("/files")
def list_files(conn: Conn, user: CurrentUser,
               file_type_code: str | None = None, q: str | None = None):
    return file_svc.list_files(conn, file_type_code, q)


@router.post("/files", status_code=201)
def create_file(payload: FileCreateRequest, conn: Conn,
                actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    return file_svc.create_file(
        conn, file_number=payload.file_number, file_type_code=payload.file_type_code,
        title_cn=payload.title_cn, title_en=payload.title_en, actor=actor)


@router.get("/files/{file_number}")
def get_file(file_number: str, conn: Conn, user: CurrentUser):
    df = file_svc.get_file(conn, file_number)
    if df is None:
        raise errors.not_found(f"设计文件不存在: {file_number}")
    df["revisions"] = file_svc.list_revisions(conn, str(df["id"]))
    return df


# ---------------- 版次 ----------------
@router.post("/files/{file_number}/revisions", status_code=201)
def create_revision(file_number: str, payload: RevisionCreateRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        return file_svc.create_revision(conn, file_number, payload.change_summary, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: str, conn: Conn, user: CurrentUser):
    rev = file_svc.get_revision(conn, revision_id)
    if rev is None:
        raise errors.not_found("版次不存在")
    rev["attachments"] = file_svc.attachments(conn, revision_id)
    return rev


@router.post("/revisions/{revision_id}/submit")
def submit_revision(revision_id: str, payload: SubmitRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.SUBMIT))):
    try:
        return file_svc.submit_revision(conn, revision_id, payload.approver_user_id, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/revisions/{revision_id}/release")
def release_revision(revision_id: str, conn: Conn,
                     comments: str = Query("同意发布", max_length=500),
                     actor: dict = Depends(require(Perm.APPROVE))):
    """发布版次。按 INV-014，本操作不改变任何 P/N 的当前技术状态。"""
    try:
        return file_svc.release_revision(conn, revision_id, comments, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/revisions/{revision_id}/cancel")
def cancel_revision(revision_id: str, conn: Conn,
                    reason: str = Query(..., min_length=1, max_length=256),
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        file_svc.cancel_revision(conn, revision_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "版次已取消"}


# ---------------- 附件 ----------------
@router.post("/revisions/{revision_id}/attachments", status_code=201)
async def upload_attachment(revision_id: str, conn: Conn,
                            role: str = Form(...),
                            file: UploadFile = File(...),
                            actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    valid = {"PRIMARY_NATIVE", "RELEASED_PDF", "DERIVED_STEP", "DERIVED_DXF", "REFERENCE"}
    if role not in valid:
        raise errors.bad_request(f"附件用途须为: {', '.join(sorted(valid))}")
    content = await file.read()
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise errors.bad_request("附件超过 200MB 上限")
    try:
        return file_svc.upload_attachment(
            conn, revision_id, role=role, filename=file.filename or "upload.bin",
            mime_type=file.content_type or "application/octet-stream",
            content=content, actor=actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    except FileExistsError as e:
        raise errors.conflict(str(e), rule="INV-007")


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: str, conn: Conn, user: CurrentUser):
    from ..db import fetch_one
    row = fetch_one(conn, """
        SELECT filename, storage_key, mime_type FROM revision_attachment WHERE id=%s
    """, (attachment_id,))
    if row is None:
        raise errors.not_found("附件不存在")
    if not storage.exists(row["storage_key"]):
        raise errors.not_found("附件物理文件缺失, 请运行完整性巡检")
    return Response(content=storage.read(row["storage_key"]),
                    media_type=row["mime_type"],
                    headers={"Content-Disposition":
                             "attachment; filename*=UTF-8''" + quote(row["filename"], safe='')})


@router.delete("/attachments/{attachment_id}")
def delete_attachment(attachment_id: str, conn: Conn,
                      reason: str | None = Query(None, max_length=256),
                      actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        file_svc.delete_attachment(conn, attachment_id, actor, reason=reason)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "附件已删除"}


# ---------------- 完整性巡检 ----------------
@router.post("/integrity/check")
def check_integrity(conn: Conn, revision_id: str | None = Query(None),
                    actor: dict = Depends(require(Perm.READ_AUDIT))):
    """重算摘要并与登记值比对 — AC-DATA-05 / AT-007。"""
    return file_svc.check_integrity(conn, revision_id=revision_id, actor=actor)


@router.get("/integrity/issues")
def integrity_issues(conn: Conn, user: CurrentUser):
    return file_svc.open_integrity_issues(conn)


# ---------------- 设计定义关系 ----------------
@router.post("/definitions", status_code=201)
def link_definition(payload: DefinitionLinkRequest, conn: Conn,
                    actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        return file_svc.link_definition(
            conn, payload.object_code, payload.file_number,
            payload.relation_type, payload.applicability_note, actor)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.get("/definitions/{object_code}")
def definitions_of(object_code: str, conn: Conn, user: CurrentUser):
    return file_svc.definitions_of(conn, object_code)
