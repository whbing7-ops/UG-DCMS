UG-DCMS rc2.2 builder fix

Changes:
- Detects ISCC.exe in both Program Files and Program Files (x86).
- Detects per-user Inno Setup installations.
- Reads Windows uninstall registry keys for InstallLocation.
- Falls back to a focused Program Files search.
- Re-checks ISCC.exe after winget installation.

Double-click BUILD-SETUP.cmd.
Expected output: installer\output\UG-DCMS-Setup-1.0.0-rc2.2.exe
