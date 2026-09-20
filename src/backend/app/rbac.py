"""角色与权限。

SRS-ACC-005 规定的六个角色。权限用"动作码"表达, 而不是把角色名直接散落在各处路由里,
否则新增一个角色就要翻遍全部端点。

映射关系刻意保守: 验收标准 AC-SEC-01/02/03 明确了三条边界 ——
  VIEWER 只能查看, 不可修改 Draft/BOM/文件;
  ENGINEER 不可修改受控字典;
  Released 对象不提供普通直接编辑入口(这条由对象状态而非角色控制, 见 services 层)。
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SYSTEM_ADMIN = "SYSTEM_ADMIN"
    DATA_ADMIN = "DATA_ADMIN"
    CONFIGURATION_MANAGER = "CONFIGURATION_MANAGER"
    ENGINEER = "ENGINEER"
    APPROVER = "APPROVER"
    VIEWER = "VIEWER"
    PRODUCTION = "PRODUCTION"      # 生产: 只读, 只看已发布资料
    PROCUREMENT = "PROCUREMENT"    # 采购: 只读, 只看已发布资料, 不含原生设计文件


class Perm(StrEnum):
    # 读
    READ = "read"
    READ_AUDIT = "read_audit"
    # 资料访问范围。现有角色全部保留这两项(行为不变); 生产/采购不具备。
    READ_UNRELEASED = "read_unreleased"      # 查看/下载草稿、审核中、已取消版次的内容
    DOWNLOAD_NATIVE = "download_native"      # 下载原生设计文件(PRIMARY_NATIVE)
    # 设计数据
    DRAFT_WRITE = "draft_write"              # 新建/编辑草稿对象、BOM、文件
    SUBMIT = "submit"                        # 提交审核
    APPROVE = "approve"                      # 审批
    # 受控字典 (AC-SEC-02: ENGINEER 不得有)
    DICTIONARY_WRITE = "dictionary_write"
    # 构型管理
    NUMBER_ALLOCATE = "number_allocate"
    BASELINE_RELEASE = "baseline_release"
    # 三级签署: 审核/批准时的电子签名(能否签署由"有权签署人清单"决定, 本权限只是入口)
    SIGN = "sign"
    SIGNER_MANAGE = "signer_manage"          # 维护有权签署人清单
    # 系统管理
    USER_MANAGE = "user_manage"
    SESSION_MANAGE = "session_manage"
    SYSTEM_SETTING = "system_setting"


_ACCESS = frozenset({Perm.READ_UNRELEASED, Perm.DOWNLOAD_NATIVE})

ROLE_PERMISSIONS: dict[Role, frozenset[Perm]] = {
    Role.VIEWER: frozenset({Perm.READ}) | _ACCESS,
    Role.ENGINEER: frozenset({
        Perm.READ, Perm.DRAFT_WRITE, Perm.SUBMIT, Perm.SIGN,
    }) | _ACCESS,
    Role.APPROVER: frozenset({
        Perm.READ, Perm.APPROVE, Perm.SIGN,
    }) | _ACCESS,
    Role.CONFIGURATION_MANAGER: frozenset({
        Perm.READ, Perm.DRAFT_WRITE, Perm.SUBMIT, Perm.APPROVE, Perm.SIGN, Perm.SIGNER_MANAGE,
        Perm.NUMBER_ALLOCATE, Perm.BASELINE_RELEASE, Perm.READ_AUDIT,
    }) | _ACCESS,
    Role.DATA_ADMIN: frozenset({
        Perm.READ, Perm.DICTIONARY_WRITE, Perm.READ_AUDIT,
    }) | _ACCESS,
    Role.SYSTEM_ADMIN: frozenset({
        Perm.READ, Perm.READ_AUDIT,
        Perm.USER_MANAGE, Perm.SESSION_MANAGE, Perm.SYSTEM_SETTING,
    }) | _ACCESS,
    # 生产、采购按最小权限: 只读、只见已发布内容、不含原生设计文件。
    # 若生产需要原生文件, 在此加上 Perm.DOWNLOAD_NATIVE 即可。
    Role.PRODUCTION: frozenset({Perm.READ}),
    Role.PROCUREMENT: frozenset({Perm.READ}),
}


def permissions_for(roles: list[str]) -> frozenset[Perm]:
    out: set[Perm] = set()
    for r in roles:
        try:
            out |= ROLE_PERMISSIONS[Role(r)]
        except ValueError:
            continue          # 库里出现未知角色时忽略, 不因此放大权限
    return frozenset(out)


def has(roles: list[str], perm: Perm) -> bool:
    return perm in permissions_for(roles)
