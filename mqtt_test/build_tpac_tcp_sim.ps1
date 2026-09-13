$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "TPAC-TCP-Sim" `
    --distpath "$projectRoot\dist" `
    --workpath "$projectRoot\build" `
    --specpath "$projectRoot\pyinstaller-spec" `
    "$projectRoot\tpac_tcp_sim.py"

Write-Host "EXE created: $projectRoot\dist\TPAC-TCP-Sim.exe"
