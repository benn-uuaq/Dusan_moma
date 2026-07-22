$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = "$projectRoot\.deps;$projectRoot\src"
$specPath = "$projectRoot\pyinstaller-spec"
New-Item -ItemType Directory -Force -Path $specPath | Out-Null

python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "SMR-Operator-UI" `
    --paths "$projectRoot\src" `
    --paths "$projectRoot\.deps" `
    --add-data "$projectRoot\src\smr_operator_ui\resources;smr_operator_ui\resources" `
    --add-data "$projectRoot\src\smr_operator_ui\styles;smr_operator_ui\styles" `
    --collect-submodules psycopg `
    --distpath "$projectRoot\dist" `
    --workpath "$projectRoot\build" `
    --specpath "$specPath" `
    "$projectRoot\src\smr_operator_ui\__main__.py"

Write-Host "EXE created: $projectRoot\dist\SMR-Operator-UI.exe"
