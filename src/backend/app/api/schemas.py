"""请求与响应模型。"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserBrief(BaseModel):
    id: UUID
    username: str
    full_name: str
    roles: list[str]
    must_change_password: bool


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserBrief
    permissions: list[str]


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64,
                          pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")
    full_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    employee_no: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=10, max_length=72)
    roles: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    is_active: bool | None = None
    roles: list[str] | None = None
    reason: str | None = Field(default=None, max_length=256)


class PasswordResetRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=72)
    reason: str = Field(min_length=1, max_length=256)


class LicenseStatus(BaseModel):
    limit: int
    active_accounts: int
    available: int
    accounts: list[dict]
