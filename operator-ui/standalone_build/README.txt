SMR RCS 단독판 (TPAC 전시회용) — 소스 코드
==========================================

이 폴더(source)는 한 단계 위의 SMR-RCS-Standalone.exe 를 만든 소스 그대로입니다.
실행만 할 때는 필요 없습니다 — exe 하나에 파이썬 · Qt · 프로그램 · 설정 파일이 다 들어 있어
Python 이 없는 PC 에서도 exe 만 누르면 됩니다. 이 폴더는 고쳐서 다시 만들 때 씁니다.


폴더 구성
---------
  rcs_standalone.py        실행 파일 진입점
  smr_operator_ui\         RCS 프로그램(화면 · TPAC 중계 · 작업 순서)
    standalone\            단독판 실행기(ERUT · 차량 · PLC 없이) — launcher.py
    resources\ styles\     그림 · 글꼴 · 화면 스타일
    config\plc_io.json     (단독판에서는 안 씀)
  robot\
    elite_robot_controller\  로봇 제어(Modbus · 대시보드) — RCS 안에서 같이 돈다
    config\modbus_registers.json  로봇 레지스터 주소표
  build_standalone.ps1     실행 파일 만들기
  requirements.txt         빌드에 쓰는 패키지

  로봇 태스크(펜던트에 올리는 것)는 한 단계 위 robot_task 폴더에 있습니다.


다시 만들기 (다른 PC 에서도 됨)
-------------------------------
  준비: Windows 64bit, Python 3.11 (https://www.python.org — 설치할 때 py 런처 포함),
        처음 한 번은 인터넷(패키지 받기).

  1. 이 폴더에서 PowerShell 을 열고
         powershell -ExecutionPolicy Bypass -File build_standalone.ps1
  2. 처음에는 %LOCALAPPDATA%\SMR-RCS-build\venv 에 빌드용 가상환경을 만들고 패키지를
     받습니다(몇 분). 이미 설치된 Python 환경은 건드리지 않습니다.
  3. 끝나면 한 단계 위 폴더의 SMR-RCS-Standalone.exe 가 새 것으로 바뀝니다.
     (RCS 가 켜져 있으면 덮어쓰지 못합니다 — 먼저 끄세요.)

  설정 폴더(SMR-RCS-설정)는 건드리지 않습니다.


소스에서 바로 돌려 보기 (빌드 없이)
----------------------------------
  위 가상환경으로:
      %LOCALAPPDATA%\SMR-RCS-build\venv\Scripts\python.exe rcs_standalone.py
  이때 설정은 %APPDATA%\SMR-RCS 에 저장됩니다(exe 옆 폴더가 아님).
