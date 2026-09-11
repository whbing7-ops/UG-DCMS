"""Batch creation and full CSV export for families, Dash numbers and externals."""
from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import Response

from .. import errors
from ..deps import Conn, CurrentUser, require, require_password_changed
from ..rbac import Perm
from ..txroute import TransactionalRoute
from ..services import master_transfer as svc

router=APIRouter(tags=['主数据批量导入导出'],route_class=TransactionalRoute)


def download(kind, rows, suffix):
    try:
        content=svc.csv_text(kind,rows)
    except ValueError as exc:
        raise errors.bad_request(str(exc))
    return Response(content,media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':f'attachment; filename="{kind}_{suffix}.csv"'})


@router.get('/master-data/{kind}/template')
def template(kind: str, user: CurrentUser):
    return download(kind,[],'template')


@router.get('/master-data/{kind}/export')
def export(kind: str, conn: Conn, user: CurrentUser, family_id: str | None=None):
    try:
        rows=svc.export_rows(conn,kind,family_id)
    except ValueError as exc:
        raise errors.bad_request(str(exc))
    return download(kind,rows,'export')


@router.post('/master-data/{kind}/preview')
async def preview(kind: str, conn: Conn, file: UploadFile=File(...),
                  actor: dict=Depends(require(Perm.DRAFT_WRITE)),
                  _: dict=Depends(require_password_changed)):
    content=await file.read(20*1024*1024+1)
    if len(content)>20*1024*1024:
        raise errors.bad_request('文件超过 20MB 上限')
    if not (file.filename or '').lower().endswith(('.csv','.xlsx')):
        raise errors.bad_request('请上传 CSV 或 XLSX 文件')
    try:
        return svc.preview(conn,kind,content,file.filename,actor)
    except (ValueError,LookupError) as exc:
        raise errors.bad_request(str(exc))


@router.post('/master-data/batches/{batch_id}/commit')
def commit(batch_id: str, conn: Conn, actor: dict=Depends(require(Perm.DRAFT_WRITE)),
           _: dict=Depends(require_password_changed)):
    try:
        return svc.commit(conn,batch_id,actor)
    except (ValueError,LookupError) as exc:
        raise errors.bad_request(str(exc))
