"""统一错误模型。

数据库触发器抛出的不变量错误带有 DCMS-XXX 前缀, 这里把它们翻译成对使用者有意义的
HTTP 响应, 同时保留原始不变量编号, 使前端和审计都能追溯到具体是哪一条规则拦下的。
"""
from __future__ import annotations

import logging
import re

import psycopg
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# 触发器消息形如: DCMS-INV-009: BOM 形成循环: ...
logger = logging.getLogger("dcms.errors")

# 触发器消息形如: DCMS-INV-009: BOM 形成循环: ...
_RULE_RE_UNUSED = None
_RULE_RE = re.compile(r"(DCMS-[A-Z0-9§\-]+):\s*(.*)", re.S)

FIELD_CN = {
    "username":"账户名", "password":"密码", "old_password":"当前密码", "new_password":"新密码",
    "full_name":"姓名", "email":"邮箱", "employee_no":"员工编号", "roles":"角色",
    "external_part_number":"外部件号", "name_cn":"中文名称", "name_en":"英文名称",
    "namespace_code":"来源", "manufacturer_code":"制造商", "external_class_code":"外部件分类",
    "project_code":"项目编号", "project_applicability":"项目适用范围",
    "project_evaluation_basis":"项目评价依据", "evaluation_basis":"评价依据",
    "software_number":"软件编号", "software_type":"软件类型", "version":"版本号",
    "build":"构建号", "hash_sha256":"SHA-256 摘要", "supplier_revision":"供应商版本",
    "supplier_document":"供应商文件", "supplier_document_date":"供应商文件日期",
    "file_number":"文件编号", "file_type_code":"文件类型", "title_cn":"中文名称",
    "title_en":"英文名称", "change_summary":"变更摘要", "approver_user_id":"审批人",
    "item_number":"项号", "child_object_code":"子件号", "quantity":"数量", "unit_code":"单位",
    "reference_designator":"位号", "applicability_rule_code":"适用性规则", "applicability":"适用范围",
    "rule_code":"规则编号", "context_code":"构型编号", "expression":"适用性表达式",
    "attributes":"属性", "baseline_type":"基线类型", "scope_note":"范围说明",
    "change_reference":"变更依据", "item_type":"明细类型", "item_role":"明细角色",
    "target":"内容标识", "reason":"原因", "description":"说明", "notes":"备注",
    "primary_class_code":"一级类别", "physical_class_id":"二级分类", "object_level_code":"对象层级",
    "core_term_id":"核心词", "qualifier_1_id":"限定词一", "qualifier_2_id":"限定词二",
    "primary_function_id":"主功能", "family_definition":"设计族定义",
    "allowed_variation":"允许变化", "excluded_variation":"排除变化", "new_family_reason":"新建原因",
    "formal_name_cn":"中文正式名称", "formal_name_en":"英文正式名称", "requested_dash":"申请 Dash 号",
    "frequency":"频率", "hour":"执行小时", "weekday":"星期", "retention":"保留份数",
    "enabled":"启用状态", "confirmation":"确认文字", "file":"文件", "max_depth":"最大展开层级",
}

STATUS_CN = {"PENDING":"待处理", "ACTIVE":"有效", "DRAFT":"草稿", "WORKING":"工作中",
             "IN_REVIEW":"审核中", "APPROVED":"已批准", "REJECTED":"已拒绝", "RETURNED":"已退回",
             "RELEASED":"已发布", "CANCELLED":"已取消", "PREVIEW":"预览", "ACCEPTED":"已接受",
             "OBSOLETE":"已作废", "DEPRECATED":"已停用", "SUPERSEDED":"已取代", "OPEN":"未关闭"}


def localize_message(message: str) -> str:
    """翻译可能进入界面的状态词；编号、件号和规则代码保持原样。"""
    result = str(message)
    for token, label in STATUS_CN.items():
        result = re.sub(rf"(?<![A-Z0-9_]){token}(?![A-Z0-9_])", label, result)
    return result.replace("Applicability", "适用性").replace("StorageKey", "存储键")


def _validation_message(err: dict) -> str:
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing": return "为必填项"
    if kind == "string_too_short":
        n = ctx.get("min_length", 1)
        return "不能为空" if n == 1 else f"不得少于 {n} 个字符"
    if kind == "string_too_long": return f"不得超过 {ctx.get('max_length')} 个字符"
    if kind in {"greater_than", "greater_than_equal"}: return f"不得小于 {ctx.get('ge', ctx.get('gt'))}"
    if kind in {"less_than", "less_than_equal"}: return f"不得大于 {ctx.get('le', ctx.get('lt'))}"
    if kind in {"int_parsing", "int_type"}: return "必须是整数"
    if kind in {"float_parsing", "float_type", "decimal_parsing"}: return "必须是数字"
    if kind in {"bool_parsing", "bool_type"}: return "必须选择是或否"
    if kind in {"date_from_datetime_parsing", "date_parsing", "datetime_parsing"}: return "日期格式不正确"
    if kind in {"uuid_parsing", "uuid_type"}: return "标识格式不正确"
    if kind == "json_invalid": return "JSON 格式不正确"
    if kind in {"literal_error", "enum"}: return "请选择有效选项"
    if kind == "extra_forbidden": return "不是允许提交的字段"
    if kind.startswith("list_"): return "列表内容不符合要求"
    return "输入内容不符合要求"


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    messages = []
    for err in exc.errors():
        loc = [x for x in err.get("loc", ()) if x not in {"body", "query", "path", "header"}]
        key = next((x for x in reversed(loc) if isinstance(x, str)), "input")
        field = FIELD_CN.get(key, "输入内容")
        messages.append(f"{field}：{_validation_message(err)}")
    return JSONResponse(status_code=422, content={"error": {
        "code":"VALIDATION_ERROR", "message":"；".join(dict.fromkeys(messages)), "rule":None}})


class DcmsError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, rule: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.message = message
        self.rule = rule


def forbidden(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(403, "FORBIDDEN", message, rule)


def not_found(message: str) -> DcmsError:
    return DcmsError(404, "NOT_FOUND", message)


def bad_request(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(400, "BAD_REQUEST", message, rule)


def unauthorized(message: str) -> DcmsError:
    return DcmsError(401, "UNAUTHORIZED", message)


def conflict(message: str, rule: str | None = None) -> DcmsError:
    return DcmsError(409, "CONFLICT", message, rule)


def license_exhausted(message: str) -> DcmsError:
    # INV-024 / SRS-ACC-003: 并发许可耗尽, 用 429 而非 403,
    # 因为这是容量限制而非权限问题, 客户端稍后重试是合理行为
    return DcmsError(429, "LICENSE_EXHAUSTED", message, "INV-024")


async def dcms_error_handler(request: Request, exc: DcmsError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": localize_message(exc.message), "rule": exc.rule}},
    )


async def db_error_handler(request: Request, exc: psycopg.Error) -> JSONResponse:
    """把数据库不变量违规翻译成 409, 其余数据库错误按 500 处理。

    不变量违规是"用户请求违反了受控规则", 属于客户端错误; 其它数据库错误是系统问题。
    """
    raw = str(getattr(exc, "diag", None) and exc.diag.message_primary or exc)
    m = _RULE_RE.search(raw)
    if m:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "RULE_VIOLATION", "message": localize_message(m.group(2).strip()),
                               "rule": m.group(1)}},
        )
    # 输入格式错误(如把 "compare" 当 uuid 传入)是客户端问题, 不是服务器故障。
    # 归到 500 会让调用方以为系统坏了, 也会淹没真正的内部错误。
    if isinstance(exc, psycopg.errors.DataError):
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "INVALID_INPUT",
                               "message": "请求参数格式不正确", "rule": None}})

    constraint = getattr(getattr(exc, "diag", None), "constraint_name", None)
    if constraint:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "CONSTRAINT_VIOLATION",
                               "message": f"违反数据库约束 {constraint}",
                               "rule": constraint}},
        )
    # 生产环境不外泄数据库细节; 非生产环境保留原文, 否则排障时只能看到
    # "数据库操作失败"六个字, 真正的约束名和上下文全被吞掉。
    from .config import get_settings
    detail = raw if get_settings().environment != "PROD" else None
    logger.exception("未归类的数据库错误: %s", raw)
    return JSONResponse(status_code=500,
                        content={"error": {"code": "INTERNAL", "message": "数据库操作失败",
                                           "rule": None, "detail": detail}})


async def recursion_handler(request: Request, exc: RecursionError) -> JSONResponse:
    """递归超限属于输入过于复杂, 是客户端问题, 不该报成服务器故障。"""
    logger.warning("请求触发递归超限: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "INPUT_TOO_COMPLEX",
                           "message": "请求结构过于复杂, 无法处理", "rule": None}})
