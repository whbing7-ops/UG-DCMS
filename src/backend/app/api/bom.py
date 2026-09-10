"""BOM、Where-Used、导入端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import errors
from ..db import fetch_one, fetch_all
from ..txroute import TransactionalRoute
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..services import bom as bom_svc, imports as imp_svc, applicability as app_svc

router = APIRouter(tags=["BOM 与导入"], route_class=TransactionalRoute)


class LineCreateRequest(BaseModel):
    item_number: str = Field(min_length=1, max_length=32)
    child_object_code: str = Field(min_length=1, max_length=64)
    quantity: float = Field(gt=0)
    unit_code: str | None = None
    reference_designator: str | None = Field(default=None, max_length=256)
    effectivity: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=500)
    applicability_rule_code: str | None = Field(default=None, max_length=64)


class LineUpdateRequest(BaseModel):
    item_number: str | None = Field(default=None, max_length=32)
    quantity: float | None = Field(default=None, gt=0)
    unit_code: str | None = None
    reference_designator: str | None = Field(default=None, max_length=256)
    effectivity: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=500)
    applicability_rule_code: str | None = Field(default=None, max_length=64)


def _object_id(conn, object_code: str) -> str:
    row = fetch_one(conn, "SELECT id FROM design_object WHERE object_code = %s", (object_code,))
    if row is None:
        raise errors.not_found(f"设计对象不存在: {object_code}")
    return str(row["id"])


# ---------------- 工作 BOM ----------------
@router.get("/bom-candidates/{parent_object_code}")
def bom_candidates(parent_object_code: str, conn: Conn, user: CurrentUser,
                   q: str | None = Query(None, max_length=128), limit: int = Query(30, ge=1, le=100)):
    pattern=f"%{(q or '').strip()}%"
    return fetch_all(conn,"""SELECT d.object_code,d.display_name,d.object_kind,d.lifecycle_status,
      ep.external_part_number,ep.namespace_code
      FROM design_object d
      LEFT JOIN external_part ep ON ep.design_object_id=d.id
      WHERE d.object_code<>%s AND d.object_kind IN ('PART_NUMBER','EXTERNAL_PART')
        AND (%s='' OR d.object_code ILIKE %s OR d.display_name ILIKE %s
             OR ep.external_part_number ILIKE %s)
      ORDER BY CASE
        WHEN lower(d.object_code)=lower(%s) OR lower(COALESCE(ep.external_part_number,''))=lower(%s) THEN 0
        WHEN d.object_code ILIKE %s OR ep.external_part_number ILIKE %s THEN 1
        ELSE 2 END,d.object_code
      LIMIT %s""",(parent_object_code,(q or '').strip(),pattern,pattern,pattern,
                    (q or '').strip(),(q or '').strip(),f"{(q or '').strip()}%",
                    f"{(q or '').strip()}%",limit))


@router.get("/bom/{object_code}")
def get_bom(object_code: str, conn: Conn, user: CurrentUser):
    oid = _object_id(conn, object_code)
    header = fetch_one(conn, """
        SELECT id, status, working_sequence, last_validated_at, last_validation_result
          FROM bom_header WHERE parent_design_object_id = %s AND status = 'WORKING'
    """, (oid,))
    return {
        "parent_object_code": object_code,
        "header": header,
        "lines": bom_svc.list_lines(conn, str(header["id"])) if header else [],
    }


@router.post("/bom/{object_code}/lines", status_code=201)
def add_line(object_code: str, payload: LineCreateRequest, conn: Conn,
             actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    oid = _object_id(conn, object_code)
    try:
        row = bom_svc.add_line(
            conn, oid, item_number=payload.item_number,
            child_object_code=payload.child_object_code, quantity=payload.quantity,
            unit_code=payload.unit_code,
            reference_designator=payload.reference_designator,
            effectivity=payload.effectivity, notes=payload.notes, actor=actor)
        if payload.applicability_rule_code:
            app_svc.assign_rule(conn, str(row["id"]), payload.applicability_rule_code, actor)
        return row
    except LookupError as e:
        raise errors.not_found(str(e))


@router.patch("/bom/lines/{line_id}")
def update_line(line_id: str, payload: LineUpdateRequest, conn: Conn,
                actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        row = bom_svc.update_line(
            conn, line_id, quantity=payload.quantity, item_number=payload.item_number,
            unit_code=payload.unit_code,
            reference_designator=payload.reference_designator,
            effectivity=payload.effectivity, notes=payload.notes, actor=actor)
        if "applicability_rule_code" in payload.model_fields_set:
            app_svc.assign_rule(conn, line_id, payload.applicability_rule_code, actor)
        return row
    except LookupError as e:
        raise errors.not_found(str(e))


@router.delete("/bom/lines/{line_id}")
def delete_line(line_id: str, conn: Conn,
                reason: str | None = Query(None, max_length=256),
                actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        bom_svc.delete_line(conn, line_id, actor, reason=reason)
    except LookupError as e:
        raise errors.not_found(str(e))
    return {"message": "BOM 行已删除"}


# ---------------- 展开与汇总 ----------------
@router.get("/bom/{object_code}/expand")
def expand(object_code: str, conn: Conn, user: CurrentUser,
           max_depth: int = Query(10, ge=1, le=60)):
    return bom_svc.expand(conn, _object_id(conn, object_code), max_depth)


@router.get("/bom/{object_code}/summary")
def summary(object_code: str, conn: Conn, user: CurrentUser,
            max_depth: int = Query(10, ge=1, le=60)):
    """汇总用量: 同一子项在多层多处出现时合并累计。"""
    return bom_svc.summarize(conn, _object_id(conn, object_code), max_depth)


@router.get("/bom/{object_code}/validate")
def validate(object_code: str, conn: Conn, user: CurrentUser):
    return bom_svc.validate(conn, _object_id(conn, object_code))


# ---------------- Where-Used ----------------
@router.get("/where-used/{object_code}")
def where_used(object_code: str, conn: Conn, user: CurrentUser,
               max_depth: int = Query(10, ge=1, le=60),
               include_snapshots: bool = True):
    """反查装机关系 — AC-WU-01。工作 BOM 与已冻结快照分开返回。"""
    return bom_svc.where_used(conn, _object_id(conn, object_code),
                              max_depth, include_snapshots)


# ---------------- 快照 ----------------
@router.post("/bom/{object_code}/snapshot", status_code=201)
def create_snapshot(object_code: str, conn: Conn,
                    actor: dict = Depends(require(Perm.BASELINE_RELEASE))):
    """冻结当前工作 BOM 为不可变快照 — INV-015。"""
    try:
        return bom_svc.create_snapshot(conn, _object_id(conn, object_code), actor)
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.get("/bom/snapshots/compare")
def compare(conn: Conn, user: CurrentUser,
            a: str = Query(..., description="快照编号"),
            b: str = Query(..., description="快照编号")):
    headers = [fetch_one(conn, "SELECT id,parent_design_object_id FROM bom_snapshot WHERE snapshot_number=%s", (n,)) for n in (a,b)]
    if any(h is None for h in headers):
        raise errors.not_found("BOM 快照不存在，请从快照列表中选择")
    if headers[0]['parent_design_object_id'] != headers[1]['parent_design_object_id']:
        raise errors.bad_request("只能比较同一父件号的 BOM 快照")
    def lines(header):
        rows = fetch_all(conn, """SELECT item_number,child_object_code,quantity,unit_code,
            reference_designator,effectivity,notes FROM bom_snapshot_line
            WHERE bom_snapshot_id=%s ORDER BY sort_order,item_number""", (header['id'],))
        return {(r['item_number'],r['child_object_code']):r for r in rows}
    old,new = map(lines,headers)
    added=[new[k] for k in sorted(new.keys()-old.keys())]
    removed=[old[k] for k in sorted(old.keys()-new.keys())]
    changed=[{'item_number':k[0],'child_object_code':k[1],'from':old[k],'to':new[k]}
             for k in sorted(old.keys() & new.keys()) if old[k] != new[k]]
    return {'from':a,'to':b,'added':added,'removed':removed,'changed':changed,'identical':not(added or removed or changed)}


@router.get('/bom/{object_code}/snapshots')
def snapshots(object_code: str, conn: Conn, user: CurrentUser):
    oid = _object_id(conn, object_code)
    return {
        'bom': fetch_all(conn, 'SELECT snapshot_number,line_count,created_at FROM bom_snapshot WHERE parent_design_object_id=%s ORDER BY created_at DESC', (oid,)),
        'resolved': fetch_all(conn, 'SELECT resolved_snapshot_number,line_count,context_attributes,created_at FROM resolved_bom_snapshot WHERE parent_design_object_id=%s ORDER BY created_at DESC', (oid,)),
    }


# ---------------- 导入 ----------------
@router.get("/import/bom/template", response_class=PlainTextResponse)
def bom_template(user: CurrentUser):
    return PlainTextResponse(
        imp_svc.bom_template_csv(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bom_template.csv"'})


@router.post("/import/bom/preview")
async def preview_import(conn: Conn,
                         parent_object_code: str = Form(...),
                         file: UploadFile = File(...),
                         actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    """上传并预览 — AC-IMP-01。不产生任何业务数据, 逐行给出行号与原因。"""
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise errors.bad_request("文件超过 20MB 上限")
    try:
        return imp_svc.preview_bom(conn, parent_object_code, content,
                                   file.filename or "upload.csv", actor)
    except LookupError as e:
        raise errors.not_found(str(e))


@router.get("/import/batches/{batch_id}")
def get_batch(batch_id: str, conn: Conn, user: CurrentUser):
    return imp_svc._batch_result(conn, batch_id)


@router.post("/import/batches/{batch_id}/commit")
def commit_import(batch_id: str, conn: Conn,
                  replace_existing: bool = Query(False),
                  actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    """提交导入。存在错误行时拒绝——不做部分导入。"""
    try:
        return imp_svc.commit_bom(conn, batch_id, actor, replace_existing)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))


@router.post("/import/batches/{batch_id}/abort")
def abort_import(batch_id: str, conn: Conn,
                 reason: str = Query(..., min_length=1, max_length=256),
                 actor: dict = Depends(require(Perm.DRAFT_WRITE))):
    try:
        imp_svc.abort_batch(conn, batch_id, reason, actor)
    except LookupError as e:
        raise errors.not_found(str(e))
    except ValueError as e:
        raise errors.bad_request(str(e))
    return {"message": "批次已放弃"}
