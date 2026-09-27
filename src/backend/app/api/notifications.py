from fastapi import APIRouter, Header, Depends
from pydantic import BaseModel, Field
from ..deps import Conn, CurrentUser, require_password_changed
from ..txroute import TransactionalRoute
from ..services import notifications as svc

router=APIRouter(prefix='/notifications',tags=['待办与通知'],route_class=TransactionalRoute)
class Ids(BaseModel):
    ids:list[int]=Field(max_length=100)
class PairCode(BaseModel):
    code:str=Field(min_length=20,max_length=64)

@router.get('/overview')
def overview(conn:Conn,user:CurrentUser):return svc.overview(conn,user)
@router.post('/read')
def read(payload:Ids,conn:Conn,user:CurrentUser):return svc.mark_read(conn,user['user_id'],payload.ids)
@router.post('/read-request/{request_id}')
def read_request(request_id:str,conn:Conn,user:CurrentUser):
    from ..db import execute
    execute(conn,'UPDATE notification SET read_at=COALESCE(read_at,now()) WHERE user_id=%s AND request_id=%s',(user['user_id'],request_id))
    return {'ok':True}
@router.post('/pair')
def pair(conn:Conn,user:dict=Depends(require_password_changed)):return svc.pair(conn,user)
@router.post('/disconnect')
def disconnect(conn:Conn,user:CurrentUser):return svc.disconnect(conn,user)
@router.post('/desktop/redeem')
def redeem(payload:PairCode,conn:Conn):return svc.redeem(conn,payload.code)
@router.get('/desktop/poll')
def poll(conn:Conn,authorization:str|None=Header(default=None)):return svc.poll(conn,svc.device(conn,authorization))
@router.post('/desktop/ack')
def ack(payload:Ids,conn:Conn,authorization:str|None=Header(default=None)):
    return svc.acknowledge(conn,svc.device(conn,authorization),payload.ids)

@router.post('/desktop/logout')
def desktop_logout(conn:Conn,authorization:str|None=Header(default=None)):
    from ..db import execute
    d=svc.device(conn,authorization)
    execute(conn,'UPDATE notification_device SET revoked_at=now() WHERE id=%s',(d['id'],))
    return {'ok':True}
