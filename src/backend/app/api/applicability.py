"""构型上下文、Applicability 与 Resolved BOM API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import errors
from ..db import fetch_one
from ..deps import Conn, CurrentUser, require
from ..rbac import Perm
from ..txroute import TransactionalRoute
from ..services import applicability as svc

router = APIRouter(tags=["构型适用性"], route_class=TransactionalRoute)

class ContextCreate(BaseModel):
    context_code: str = Field(min_length=1,max_length=64)
    name_cn: str = Field(min_length=1,max_length=128)
    description: str | None = Field(default=None,max_length=500)
    attributes: dict

class RuleCreate(BaseModel):
    rule_code: str = Field(min_length=1,max_length=64)
    name_cn: str = Field(min_length=1,max_length=128)
    description: str | None = Field(default=None,max_length=500)
    expression: dict

class AssignRule(BaseModel):
    rule_code: str | None = None

class ResolveRequest(BaseModel):
    context_code: str | None = None
    attributes: dict | None = None
    max_depth: int = Field(default=10,ge=1,le=60)

def _object_id(conn, code: str) -> str:
    r=fetch_one(conn,"SELECT id FROM design_object WHERE object_code=%s",(code,))
    if not r: raise errors.not_found(f"设计对象不存在: {code}")
    return str(r["id"])

def _context(conn, req: ResolveRequest):
    if req.context_code:
        r=fetch_one(conn,"SELECT id,attributes FROM configuration_context WHERE context_code=%s AND status='ACTIVE'",(req.context_code,))
        if not r: raise errors.not_found(f"构型上下文不存在: {req.context_code}")
        attrs=dict(r["attributes"] or {})
        if req.attributes: attrs.update(req.attributes)
        return str(r["id"]), attrs
    return None, dict(req.attributes or {})

@router.get("/configuration/contexts")
def contexts(conn: Conn,user: CurrentUser): return svc.list_contexts(conn)

@router.post("/configuration/contexts",status_code=201)
def create_context(p: ContextCreate,conn: Conn,actor: dict=Depends(require(Perm.DRAFT_WRITE))):
    try: return svc.create_context(conn,code=p.context_code,name=p.name_cn,attributes=p.attributes,description=p.description,actor=actor)
    except ValueError as e: raise errors.bad_request(str(e))

@router.get("/applicability/rules")
def rules(conn: Conn,user: CurrentUser): return svc.list_rules(conn)

@router.post("/applicability/rules",status_code=201)
def create_rule(p: RuleCreate,conn: Conn,actor: dict=Depends(require(Perm.DRAFT_WRITE))):
    try: return svc.create_rule(conn,code=p.rule_code,name=p.name_cn,expression=p.expression,description=p.description,actor=actor)
    except ValueError as e: raise errors.bad_request(str(e))

@router.put("/bom/lines/{line_id}/applicability")
def bind(line_id: str,p: AssignRule,conn: Conn,actor: dict=Depends(require(Perm.DRAFT_WRITE))):
    try: return svc.assign_rule(conn,line_id,p.rule_code,actor)
    except LookupError as e: raise errors.not_found(str(e))

@router.post("/bom/{object_code}/resolve")
def resolve(object_code: str,p: ResolveRequest,conn: Conn,user: CurrentUser):
    _,attrs=_context(conn,p)
    try: return svc.resolve_with_validation(conn,_object_id(conn,object_code),attrs,p.max_depth)
    except ValueError as e: raise errors.bad_request(str(e))

@router.post("/bom/{object_code}/resolved-snapshot",status_code=201)
def resolved_snapshot(object_code: str,p: ResolveRequest,conn: Conn,actor: dict=Depends(require(Perm.BASELINE_RELEASE))):
    cid,attrs=_context(conn,p)
    try: return svc.create_resolved_snapshot(conn,_object_id(conn,object_code),context=attrs,context_id=cid,actor=actor,max_depth=p.max_depth)
    except ValueError as e: raise errors.bad_request(str(e))

@router.get("/resolved-bom/{snapshot_number}")
def get_resolved(snapshot_number: str,conn: Conn,user: CurrentUser):
    h=fetch_one(conn,"SELECT * FROM resolved_bom_snapshot WHERE resolved_snapshot_number=%s",(snapshot_number,))
    if not h: raise errors.not_found("Resolved BOM 快照不存在")
    from ..db import fetch_all
    lines=fetch_all(conn,"SELECT level,item_number,child_object_code,child_display_name,quantity,extended_quantity,unit_code,reference_designator,applicability_rule_code,path FROM resolved_bom_snapshot_line WHERE resolved_bom_snapshot_id=%s ORDER BY sort_path",(h["id"],))
    return {"header":h,"lines":lines}
