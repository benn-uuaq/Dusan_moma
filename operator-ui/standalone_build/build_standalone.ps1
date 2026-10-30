# SMR RCS 단독판(Windows, 로봇 + TPAC) 실행 파일 만들기
#
#   powershell -ExecutionPolicy Bypass -File build_standalone.ps1
#
# 두 곳에서 돈다.
#   1. 워크스페이스(WSL)의 operator-ui\standalone_build 에서 돌리면
#      소스를 모아 배포 폴더(바탕화면\TPAC지원)의 source 폴더를 새로 채우고,
#      그 source 로 실행 파일을 만든다. 로봇 태스크 · 사용법도 같이 넣는다.
#   2. 배포 폴더의 source 안에서 돌리면(다른 PC) 워크스페이스 없이 그 자리의
#      소스로 실행 파일을 만들어 한 단계 위 폴더(TPAC지원)에 넣는다.
# 어느 쪽이든 실행 파일은 언제나 배포 폴더 source 의 코드로 만든다.
#
# 필요한 것: Windows 64bit, Python 3.11(python.org 설치본 또는 py 런처),
# 처음 한 번 패키지를 받을 인터넷. 빌드용 가상환경은
# %LOCALAPPDATA%\SMR-RCS-build\venv 에 따로 만든다(설치된 Python 은 건드리지 않는다).
param(
    [string]$Repo = "\\wsl.localhost\Ubuntu-22.04\home\user\dusan_ws",
    [string]$Out = ""
)
$ErrorActionPreference = "Stop"

function Copy-Tree($from, $to) {
    # 폴더를 통째로 맞춘다(지운 파일도 지운다). 캐시는 뺀다.
    robocopy $from $to /MIR /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "복사 실패: $from" }
}

$portable = Test-Path (Join-Path $PSScriptRoot "smr_operator_ui")
if ($portable) {
    $src = $PSScriptRoot
    if (-not $Out) { $Out = Split-Path $PSScriptRoot -Parent }
} else {
    if (-not $Out) { $Out = Join-Path ([Environment]::GetFolderPath("Desktop")) "TPAC지원" }
    $src = Join-Path $Out "source"
    $here = Join-Path $Repo "operator-ui\standalone_build"
    $tasks = Join-Path $Repo "robot_task\dusan_task_v5"
    New-Item -ItemType Directory -Force -Path $src | Out-Null
    Copy-Tree (Join-Path $Repo "operator-ui\src\smr_operator_ui") (Join-Path $src "smr_operator_ui")
    Copy-Tree (Join-Path $Repo "src\elite_robot_controller\elite_robot_controller") (Join-Path $src "robot\elite_robot_controller")
    New-Item -ItemType Directory -Force -Path (Join-Path $src "robot\config") | Out-Null
    Copy-Item (Join-Path $Repo "src\elite_robot_controller\config\modbus_registers.json") (Join-Path $src "robot\config\") -Force
    foreach ($f in "rcs_standalone.py", "build_standalone.ps1", "requirements.txt", "README.txt") {
        Copy-Item (Join-Path $here $f) $src -Force
    }
    Copy-Item (Join-Path $here "사용법.txt") $Out -Force
    # 전시판 로봇 태스크 — 컨트롤러의 Dusan/dusan_v5 에 그대로 넣는다. 함께 쓰는
    # 논센서판 스크립트(move_start · move_start_waypoint · probe_r)도 넣어 새 컨트롤러에도
    # 빠진 파일이 없게 한다.
    $task = Join-Path $Out "robot_task\dusan_v5"
    New-Item -ItemType Directory -Force -Path (Join-Path $task "scripts_nosensor") | Out-Null
    Copy-Item (Join-Path $tasks "dusan_v5_tpac_exhibition.task") $task -Force
    Copy-Tree (Join-Path $tasks "scripts_tpac_exhibition") (Join-Path $task "scripts_tpac_exhibition")
    foreach ($f in "dus5_move_start.script", "dus5_move_start_waypoint.script", "dus5_probe_r.script") {
        Copy-Item (Join-Path $tasks "scripts_nosensor\$f") (Join-Path $task "scripts_nosensor") -Force
    }
}

$build = Join-Path $env:LOCALAPPDATA "SMR-RCS-build"
# 여기부터는 pip · PyInstaller 가 진행 상황을 stderr 로 낸다. PowerShell 5.1 은 출력을
# 돌려받을 때 그걸 오류로 보고 Stop 이면 멈춰 버린다 — 성공 여부는 종료 코드로만 본다.
$ErrorActionPreference = "Continue"
$py = Join-Path $build "venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    # 빌드는 Python 3.11 로 한다 — python.org 설치본이 있으면 그것, 없으면 py 런처.
    $base = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $base) { & $base -m venv (Join-Path $build "venv") }
    else { & py -3.11 -m venv (Join-Path $build "venv") }
    if (-not (Test-Path $py)) { throw "빌드용 가상환경을 만들지 못했습니다 — Python 3.11 을 설치하세요" }
    & $py -m pip install --upgrade pip
    & $py -m pip install -r (Join-Path $src "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "패키지 설치 실패 — 인터넷 연결을 확인하세요" }
}

# 소스 폴더에 __pycache__ 를 남기지 않는다(배포 폴더 source 가 지저분해진다).
$env:PYTHONDONTWRITEBYTECODE = "1"
& $py -m PyInstaller `
    --noconfirm --clean --onefile --windowed `
    --name "SMR-RCS-Standalone" `
    --paths $src `
    --paths (Join-Path $src "robot") `
    --add-data "$src\smr_operator_ui\resources;smr_operator_ui\resources" `
    --add-data "$src\smr_operator_ui\styles;smr_operator_ui\styles" `
    --add-data "$src\smr_operator_ui\config;smr_operator_ui\config" `
    --add-data "$src\robot\config\modbus_registers.json;config" `
    --hidden-import "elite_robot_controller.robot.robot_control_node" `
    --hidden-import "smr_operator_ui.standalone.inprocess_node" `
    --exclude-module psycopg --exclude-module paho `
    --distpath (Join-Path $build "dist") `
    --workpath (Join-Path $build "work") `
    --specpath $build `
    (Join-Path $src "rcs_standalone.py")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 실패 ($LASTEXITCODE)" }

New-Item -ItemType Directory -Force -Path $Out | Out-Null
try {
    Copy-Item (Join-Path $build "dist\SMR-RCS-Standalone.exe") $Out -Force -ErrorAction Stop
} catch {
    throw "실행 파일을 $Out 에 넣지 못했습니다 — RCS 가 켜져 있으면 끄고 다시 돌리세요"
}
Write-Host "완료: $Out\SMR-RCS-Standalone.exe"
exit 0
