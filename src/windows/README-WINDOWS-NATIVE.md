# UG-DCMS Windows 原生部署

此模式不使用 Docker / Linux 容器 / Nginx。FastAPI 直接在 Windows Python Runtime 中运行并同时托管前端；PostgreSQL 使用 Windows Service。

## 前置条件
- Windows 10/11 x64 或 Windows Server 2019+
- Python 3.11+ x64
- PostgreSQL 16 x64（Windows 安装版）

## 安装
以 PowerShell 运行：
`powershell -ExecutionPolicy Bypass -File .\windows\install-native.ps1`

安装默认目录：`C:\ProgramData\UG-DCMS`。
启动：`C:\ProgramData\UG-DCMS\start-native.ps1`
访问：`http://服务器IP:8080`

> 本交付包含 Windows 原生部署脚本，但未包含第三方 Python/PostgreSQL 安装包，也未在 Linux 构建环境伪造 Windows EXE。若需要单一 EXE，可在 Windows 构建机执行后续 PyInstaller 打包；数据库仍建议作为 PostgreSQL Windows Service 独立运行。
