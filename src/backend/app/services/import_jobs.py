"""Durable progress for background imports; business changes still commit atomically."""
import logging
import time
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from ..config import get_settings
from ..db import transaction, fetch_one, scalar
from . import data_backups, ima_demo, scale_demo

LOCK = 811038
log = logging.getLogger(__name__)


def status(conn):
    job = fetch_one(conn, 'SELECT * FROM data_import_job WHERE slot=1')
    if not job:
        return None
    # A transaction lock is a liveness signal, not a guessed time-based percentage.
    if job['state'] in ('QUEUED', 'RUNNING') and scalar(conn, 'SELECT pg_try_advisory_xact_lock(%s)', (LOCK,)):
        fresh = fetch_one(conn, 'SELECT *, clock_timestamp()-started_at > interval \'2 minutes\' AS expired FROM data_import_job WHERE slot=1')
        if fresh['id'] != job['id']:
            return fresh
        if fresh['state'] == 'RUNNING' or (fresh['state'] == 'QUEUED' and fresh['expired']):
            receipt = fetch_one(conn, 'SELECT imported_at FROM simulation_dataset WHERE dataset_code=%s',
                                (job['dataset_code'],))
            if receipt:
                conn.execute("UPDATE data_import_job SET state='COMPLETED',stage='完成',progress=100,message='数据导入已完成',finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s", (job['id'],))
            else:
                conn.execute("UPDATE data_import_job SET state='INTERRUPTED',message='导入已中断，未发现完成记录；本次数据库改动未提交。请检查服务后重新选择原数据包导入。',finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s", (job['id'],))
            job = fetch_one(conn, 'SELECT * FROM data_import_job WHERE slot=1')
        else:
            job = fresh
    return job


def enqueue(conn, actor, content, confirmation):
    if confirmation != data_backups.CONFIRMATION:
        raise ValueError('请输入确认文字：'+data_backups.CONFIRMATION)
    dataset = data_backups.validate_package(content)
    if not scalar(conn, 'SELECT pg_try_advisory_xact_lock(%s)', (LOCK,)):
        raise ValueError('已有数据导入正在执行，请查看当前进度，不要重复提交')
    current = status(conn)
    if current and current['state'] in ('QUEUED', 'RUNNING'):
        raise ValueError('已有数据导入正在排队或执行，请查看当前进度')
    job_id = str(uuid4())
    conn.execute("""INSERT INTO data_import_job(slot,id,dataset_code,state,stage,message,requested_by)
        VALUES(1,%s,%s,'QUEUED','等待开始','数据包校验通过，等待开始导入',%s)
        ON CONFLICT(slot) DO UPDATE SET id=excluded.id,dataset_code=excluded.dataset_code,
        state=excluded.state,stage=excluded.stage,message=excluded.message,requested_by=excluded.requested_by,
        completed=0,total=0,progress=0,started_at=clock_timestamp(),updated_at=clock_timestamp(),finished_at=NULL""",
        (job_id, dataset['dataset_code'], actor['user_id']))
    return job_id, dataset


def run(job_id, dataset, actor):
    # Invoked by Starlette only after the accepted response and request commit.
    # No request/session row lock is held during the long import, so the same
    # administrator can poll, refresh or navigate while the worker continues.
    with psycopg.connect(get_settings().dsn, autocommit=True, row_factory=dict_row) as monitor:
        last = [None, 0.0]
        def report(stage, completed, total, start, weight):
            now = time.monotonic()
            if last[0] == stage and completed not in (0,total) and now-last[1] < 1:
                return
            last[:] = [stage, now]
            percent = min(99, start + int(weight * completed / max(total,1)))
            monitor.execute("""UPDATE data_import_job SET state='RUNNING',stage=%s,completed=%s,total=%s,
                progress=%s,message=%s,updated_at=clock_timestamp() WHERE id=%s""",
                (stage,completed,total,percent,stage,job_id))
        committed = False
        try:
            with transaction() as conn:
                if not scalar(conn, 'SELECT pg_try_advisory_xact_lock(%s)', (LOCK,)):
                    raise ValueError('其他数据导入正在执行；本任务尚未写入，请稍后重试')
                job = fetch_one(conn, 'SELECT * FROM data_import_job WHERE slot=1')
                if not job or str(job['id']) != job_id or job['state'] != 'QUEUED':
                    return
                report('准备导入',0,0,0,0)
                module = scale_demo if dataset['dataset_code'] == scale_demo.CODE else ima_demo
                module.import_dataset(conn,actor,dataset,progress=report)
                report('提交事务',0,0,99,0)
            committed = True
            # Never advertise 100% until the business transaction has committed.
            monitor.execute("""UPDATE data_import_job SET state='COMPLETED',stage='完成',progress=100,
                message='数据导入已完成，可查看下方业务入口；重复导入不会新增同一批数据',
                finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""", (job_id,))
        except Exception as exc:
            log.exception('Data import job %s failed',job_id)
            if committed:
                # A lost status write must not misreport a committed import as rollback.
                # status() reconciles the durable receipt on the next page load.
                return
            message = (str(exc) if isinstance(exc,(ValueError,LookupError,FileExistsError)) else
                       '附件写入失败，请检查存储空间和目录写入权限' if isinstance(exc,OSError) else
                       '导入遇到异常，请联系管理员检查应用日志，任务编号：'+job_id)
            monitor.execute("""UPDATE data_import_job SET state='FAILED',message=%s,
                finished_at=clock_timestamp(),updated_at=clock_timestamp() WHERE id=%s""",
                (message+'；本次数据库改动已回滚，可排除原因后重新导入。',job_id))
