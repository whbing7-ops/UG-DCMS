"""Explicit administrator-only fixture import; no connection details accepted."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from typing import Literal
from .. import errors
from ..deps import Conn, CurrentUser, require, require_password_changed
from ..rbac import Perm
from ..txroute import TransactionalRoute
from ..services import ima_demo

router = APIRouter(tags=['模拟业务数据'], route_class=TransactionalRoute)

class ImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_code: Literal['SIM-IMA-V1']

@router.get('/simulation/ima')
def get_status(conn: Conn, user: CurrentUser):
    return ima_demo.status(conn)

@router.post('/simulation/ima')
def import_ima(payload: ImportRequest, conn: Conn, actor: dict=Depends(require(Perm.SYSTEM_SETTING)),
               changed: dict=Depends(require_password_changed)):
    try:
        return ima_demo.import_dataset(conn, actor)
    except (ValueError, LookupError, FileExistsError) as exc:
        raise errors.bad_request(str(exc))
    except OSError:
        raise errors.bad_request('模拟附件写入失败，本次导入已回滚；请检查存储空间和目录写入权限后重试')
