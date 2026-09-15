"""Applicant draft editing, deletion and resubmission."""
from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from .. import errors
from ..db import fetch_one
from ..deps import Conn, CurrentUser, require, require_password_changed
from ..rbac import Perm
from ..txroute import TransactionalRoute
from ..services import drafts, families, files, baselines, externals

router=APIRouter(tags=['申请人草稿'],route_class=TransactionalRoute)

class EditRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    values: dict[str,str|None]

class SubmitRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    approver_user_id: str=Field(min_length=36,max_length=36)


def run(fn,*args):
    try: return fn(*args)
    except PermissionError as exc: raise errors.forbidden(str(exc))
    except LookupError as exc: raise errors.not_found(str(exc))
    except ValueError as exc: raise errors.bad_request(str(exc))


@router.get('/drafts/mine')
def mine(conn:Conn,actor:CurrentUser):
    return drafts.list_mine(conn,actor)

@router.get('/drafts/{kind}/{oid}')
def detail(kind:str,oid:str,conn:Conn,actor:CurrentUser):
    return run(drafts.describe,conn,kind,oid,actor)

@router.patch('/drafts/{kind}/{oid}')
def edit(kind:str,oid:str,payload:EditRequest,conn:Conn,
         actor:dict=Depends(require(Perm.DRAFT_WRITE)),changed:dict=Depends(require_password_changed)):
    return run(drafts.update,conn,kind,oid,payload.values,actor)

@router.delete('/drafts/{kind}/{oid}')
def delete(kind:str,oid:str,conn:Conn,actor:dict=Depends(require(Perm.DRAFT_WRITE)),
           changed:dict=Depends(require_password_changed)):
    return run(drafts.delete,conn,kind,oid,actor)

@router.post('/drafts/{kind}/{oid}/submit')
def submit(kind:str,oid:str,payload:SubmitRequest,conn:Conn,actor:dict=Depends(require(Perm.SUBMIT)),
           changed:dict=Depends(require_password_changed)):
    functions={'BASIC_DRAWING_FAMILY':families.submit_family,'FILE_REVISION':files.submit_revision,
      'DESIGN_BASELINE':baselines.submit,'EXTERNAL_TECHNICAL_STATE':externals.submit_technical_state,
      'EXTERNAL_PROJECT_CONTROL':externals.submit_project_control,'SOFTWARE_VERSION':externals.submit_version}
    if kind not in functions: raise errors.bad_request('不支持的审批对象类型')
    return run(functions[kind],conn,oid,payload.approver_user_id,actor)

@router.post('/drafts/SOFTWARE_VERSION/{oid}/package')
def package(oid:str,conn:Conn,file:UploadFile=File(...),actor:dict=Depends(require(Perm.DRAFT_WRITE)),
            changed:dict=Depends(require_password_changed)):
    content=file.file.read(100*1024*1024+1)
    if len(content)>100*1024*1024: raise errors.bad_request('软件包超过100MB上限')
    return run(drafts.replace_package,conn,oid,file.filename or 'software.zip',file.content_type or 'application/octet-stream',content,actor)
