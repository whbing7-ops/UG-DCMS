# UG-DCMS 一键 Setup.exe

目标安装行为：

1. 双击 `UG-DCMS-Setup-1.0.0-rc2.exe`，自动申请管理员权限。
2. 检测 64 位 Windows。
3. 检测 Python 3.11+；缺失时优先使用安装包内 `python-installer.exe`，否则通过 winget 安装 Python 3.12。
4. 检测 PostgreSQL 16/17 binaries；缺失时优先使用安装包内 `postgresql-installer.exe`，否则通过 winget 安装 PostgreSQL 16。
5. 创建 **UG-DCMS 独立 PostgreSQL cluster**，默认端口 `55432`，服务名 `UGDCMS-PostgreSQL`，不复用/修改现有业务数据库。
6. 自动生成数据库强随机密码，创建 `dcms` 账户和 `dcms` 数据库并执行全部 migrations。
7. 创建 Python venv 并安装 backend requirements。
8. 使用 WinSW 注册 `UGDCMS-App` Windows 服务；依赖数据库服务，失败自动重启，开机自动启动。
9. Windows 防火墙仅对 Domain/Private 网络开放 TCP 8080。
10. 创建所有用户桌面和开始菜单 `UG-DCMS` 快捷方式，打开 `http://localhost:8080`。
11. 安装结束执行健康检查。
12. 卸载时移除应用服务、数据库服务注册和防火墙规则；**默认保留 PostgreSQL 数据目录，避免误删业务数据**。

## 构建 Setup.exe

在 Windows x64 构建机中，以 PowerShell 执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Build-Setup.ps1
```

脚本会下载固定版本 WinSW；若没有 Inno Setup 6，会通过 winget 安装，然后生成：

`installer\output\UG-DCMS-Setup-1.0.0-rc2.exe`

### 离线完整包

将官方 Python 3.12 x64 安装器重命名为：

`windows\prerequisites\python-installer.exe`

将官方 PostgreSQL 16 x64 Windows installer 重命名为：

`windows\prerequisites\postgresql-installer.exe`

然后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\installer\Build-Setup.ps1 -Offline
```

这样生成的 Setup.exe 不依赖安装现场通过 winget 下载 Python/PostgreSQL。

## 服务

- `UGDCMS-PostgreSQL`：数据库服务，自动启动。
- `UGDCMS-App`：FastAPI/Uvicorn 应用服务，自动启动，并依赖 PostgreSQL。

## 默认端口

- Web/UI/API：`8080`，局域网 Domain/Private 网络允许访问。
- PostgreSQL：`55432`，仅监听 `127.0.0.1`，不会暴露到局域网。
