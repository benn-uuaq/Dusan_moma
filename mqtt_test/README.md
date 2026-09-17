# mqtt_test

Operator UI 없이 **MC(관제) 쪽을 흉내 내는** 독립 실행 MQTT 시뮬레이터다. Operator UI가 외부(MC)에서 실제로 받는 것은 다음 두 가지뿐이므로, 이 도구도 그 범위만 다룬다.

1. 전체 작업 시작 / 일시정지 / 정지
2. 로봇이 ㄹ자를 그리기 위한 값 (검사 대상 치수 + 격자 분할 계획)

원통이 너무 커서 AMR이 원주를 여러 구역(segment)으로 나눠 돌고, 각 구역 안에서는 Cobot이 세로로 격자를 나눠 ㄹ자로 스캔한다. **구역/격자를 하나씩 넘어가는 자동 진행은 MC가 아니라 Operator UI/로봇 쪽 책임**이라, 이 도구는 시작 시점에 필요한 값만 한 번 보내고 그 이후 진행은 관여하지 않는다. 자세한 필드 설명은 [`docs/mqtt_topic_form.md`](../docs/mqtt_topic_form.md)의 T-005를 참고한다.

## 실행

```bash
pip install paho-mqtt
python3 mqtt_test/mqtt_job_sim.py
```

### WSL 에서 마우스 포인터가 안 보일 때 — Windows 로 띄우기

WSLg(WSL 2.7 / WSLg 1.0.73)에서는 X11 창이 커서를 지정하면 Windows 쪽 포인터가
숨겨진다. Tk 는 X11 로만 뜨므로 Tk 시뮬레이터(`mqtt_job_sim`·`erut_sim`·`alarm_sim`)
위에서 포인터가 사라진다(RCS 는 Qt/Wayland 라 괜찮다). 이때는 Windows 파이썬으로 띄운다.

```bash
mqtt_test/win_sim.sh mqtt_job_sim
```

- Windows 파이썬에 `paho-mqtt` 필요: `py -m pip install paho-mqtt`
- 기본 포트가 **1884** 로 잡힌다. Windows 쪽 `localhost:1883` 은 Windows mosquitto 가
  쓰고 있어서, WSL mosquitto 에 localhost 전용 1884 리스너를 더했다
  (`/etc/mosquitto/conf.d/windows_sims.conf`). Windows `localhost:1884` → WSL 브로커
  (RCS 가 쓰는 브로커)로 간다.

`tkinter`(표준 라이브러리)와 `paho-mqtt`만 있으면 되고, ROS나 Operator UI 코드(`smr_operator_ui`)에는 의존하지 않는다. Windows에서도 그대로 실행할 수 있다.

## 장애·알람 조작판 (`alarm_sim.py`)

```bash
python3 mqtt_test/alarm_sim.py
```

실제 장애 수집이 아직 없어서 버튼으로 장애를 만든다. **이 도구가 직접 `evt/error` 를 쏘지 않고 RCS 를 찔러서 RCS 가 발행하게** 한다 — 그래야 보이는 메시지가 실제 경로 그대로다.

```
[alarm_sim] --3s/test/inject/error--> [RCS] --erut/robot1/evt/error--> [ERUT]
```

`level` 이 `stop`/`estop` 이면 진행 중 작업이 자동 일시정지되고, `req/reset` 으로 해제된다. 주입 토픽(`3s/test/inject/error`)은 시험용이라, 실제 장애 수집이 붙으면 빼도 된다.

## ERUT 시뮬레이터 (`erut_sim.py`)

협력사 ERUT(스테이션) 역할을 대신해 RCS와 통신을 시험한다. 규격은 이 폴더의 `ERUT-3S_MQTT_인터페이스_*.xlsx`.

```bash
python3 mqtt_test/erut_sim.py
```

`mqtt_job_sim.py`와 다른 점: 이쪽은 **협력사로 나가는 ERUT 규격**(`content` 래퍼, 숫자 timestamp, `erut/{장치ID}/…` 응답)이고, `mqtt_job_sim.py`는 사내 MC 규격(`doosan/robot/req/job_cmd` 등)이다. 두 규격이 같은 접두어를 쓰지만 봉투가 달라 RCS 쪽에서도 `ErutClient`와 `MqttServer`로 나눠 받는다.

**▶ 시나리오 자동 진행**이 규격 탭3의 ①~④를 순서대로 밟는다. 각 단계의 응답을 기다렸다가 넘어가고, 못 받으면 멈추고 무엇을 못 받았는지 알린다.

**지금은 로봇만 진짜다** — `start`만 실제로 로봇이 움직이고, 캘리브레이션·마킹·배터리는 RCS가 시험용으로 답한다.

## TPAC Modbus TCP 이동 시뮬레이터 (`tpac_tcp_sim.py`)

TPAC이 Modbus TCP로 로봇 TCP 값을 잘 읽어가는지만 시험하기 위한 독립 도구다.
실제 로봇/Operator UI 없이 이 프로그램 혼자 Modbus TCP **서버** 역할을 하고,
내부적으로 그리는 ㄹ자(지그재그) 경로 값을 레지스터에 실시간으로 채워 넣는다.
pymodbus 등 외부 패키지 없이 표준 라이브러리(tkinter, socket)만 쓴다.

```bash
python3 mqtt_test/tpac_tcp_sim.py
```

**"서버 열기"로 먼저 서버만 연 뒤**(값은 전부 0으로 고정) TPAC이 접속해서
모니터링을 시작하게 하고, 그다음 **"동작 시작"**을 누르면 그때부터 값이 움직인다.
TPAC 쪽에는 화면에 표시되는 "이 PC의 실제 IP"를 알려주면 된다(바인드 주소 자체는
외부에서 접속할 수 있게 기본값 `0.0.0.0`으로 둔다). 레지스터 배치는
`TPAC 연결 가이드`에서 실제 접속 로그로 확인한 것과 동일하다: 주소 1(항상 0),
400~402(TCP X/Y/Z, 0.1mm), 410~412(TCP 속도 X/Y/Z, mm/s). FC3/FC4 읽기만
지원한다(TPAC은 쓰기를 하지 않는다).

이동은 실제 로봇이 그리는 ㄹ자를 그대로 흉내 낸다. 한 줄마다 **가로로 쓸기(Y)
→ 줄 끝에서 한 칸 상승(Z)** 을 반복하는데, 이는 `dus_pass_r`/`dus_pass_l`/`dus_up`
스크립트가 하는 것과 같다. 상승 구간은 `dus_up` 과 똑같이 Z 만 바꾼다.

**X 는 제로점 기준이라 0 과 +75.8 사이만 움직인다.** 로봇이 제로점 좌표계를 Rz 180 도로 뒤집어 내보내므로(반시계 스캔) 깊이가 + 다. 실기가 내보내는 280~285 는
제로점(호의 끝, 벽에 닿은 자리) 기준 변위이고, 검사면이 로봇 쪽으로 볼록해서
호 가운데로 갈수록 로봇이 뒤로 물러나기 때문이다.

```
X =    0     호 양 끝 (= 제로점). 줄이 바뀌는 상승 구간도 여기다.
X =  +75.8   호 가운데 (R844.6 · 호 721 의 새그)
```

**후퇴 동작은 좌표에 섞지 않는다.** 실기에서 벽에서 물러나는 것은 probe_c/l/r 과
goto_zero 에서만 일어나는데, 그 구간은 state 가 6 이 아니라 280~285 로 나가지
않는다. 스캔 중(state 6)의 `dus_up` 은 Z 만 바꾸고 `dus_pass_*` 는 호만 따라가므로
발행되는 X 는 **호의 새그뿐**이다.

한 판(ㄹ자 한 번)을 마치면 물러난 깊이를 유지한 채 시작 자리로 곧장 돌아오고,
**영점에서 2초 쉰 뒤**(`영점 대기(s)` 입력) 다시 그린다. 예전에는 경로를 거꾸로
되짚어 걸어서 Z 가 계단 모양으로 도로 내려왔는데, 실제 로봇에는 없는 동작이라
없앴다. 경로가 시작점으로 **닫혀** 있어 되감을 때 좌표가 튀지 않는다.

속도는 구간마다 **사다리꼴 프로파일**을 따른다 — 코너에서 섰다가 가속도
(기본 400mm/s²)로 올리고 다음 코너 앞에서 같은 비율로 감속한다. 그리고 위치는
**실제로 흐른 시간(monotonic 시계)만큼만** 전진시킨다. "한 틱은 30ms일 것"이라
가정하고 고정 거리를 더하면, 부하가 걸렸을 때 레지스터에는 100mm/s가 나가면서
실제 좌표는 그보다 느리게 움직이는 거짓말이 된다.

**갱신은 2ms 주기이고 윈도우 타이머 눈금을 1ms 로 올려 둔다.** 레지스터
410~412 로 내보내는 속도는 구조상 설정값을 넘을 수 없지만(`v = min(설정속도,
√(2a·s), √(2a·남은거리))`), 받는 쪽이 **좌표 차이로 속도를 계산**하면 갱신 주기가
드러난다. 윈도우 기본 눈금은 15.6ms 라 30ms 를 요청해도 31.2/46.9ms 에 깨어나고,
그 들쭉날쭉함이 폴링 주기와 어긋나면 설정값의 **2.3배**까지 튄다. 눈금을 1ms 로
내리고 갱신을 폴링보다 훨씬 촘촘한 2ms 로 돌리면 **1.04~1.11배**로 줄어든다
(한 틱 4.2µs, CPU 0.2 %). 완전히 없애려면 TPAC 이 410~412 를 그대로 읽으면 된다.

**Windows exe로 만들기** — 이 저장소는 리눅스(WSL)에 있어서 진짜 Windows
실행 파일은 이 환경에서 만들 수 없다. Windows PC에 Python을 설치한 뒤:

```powershell
pip install pyinstaller
mqtt_test\build_tpac_tcp_sim.ps1
```

`mqtt_test\dist\TPAC-TCP-Sim.exe`가 생긴다.

## 자동 검증 스크립트

`run_sim_test.py`는 장비 없이 **전체 파이프라인을 한 번에 검증**한다. 로봇 시뮬레이터와 ROS 노드를 스스로 띄우고, 운영 UI를 headless로 올린 뒤 `job_cmd`를 넣어 `1A`부터 마지막 셀까지 자동으로 도는지 확인한다. 끝나면 띄운 프로세스를 정리하고 통과/실패를 종료 코드로 알린다.

```bash
source /opt/ros/humble/setup.bash && source install/setup.bash
python3 mqtt_test/run_sim_test.py
```

격자 크기를 바꿔 볼 수도 있다(열, 행 순서).

```bash
python3 mqtt_test/run_sim_test.py 3 2
```

확인하는 것:

- 셀을 `1A → 1B → … → 2A → …` 순서로 빠짐없이 도는지
- 마지막에 `DONE` 상태가 되는지
- `job_state`가 셀마다 3건(`waiting`/`executing`/`completed`) 나가는지
- 작업 영역 레지스터(256~259)가 셀 치수로 **한 번만** 기록되는지

로봇의 스캔 완료 신호는 시뮬레이터의 Modbus 레지스터(`290`, `295`)를 직접 써서 흉내 낸다. 실제 로봇 태스크가 도는 게 아니므로, 실장비에서는 이 부분만 로봇이 스스로 채운다.

## 화면 구성

- **MQTT Broker**: Host/Port/Client ID 입력 후 연결·연결 해제. 연결되면 로봇 상태(`robot_state`), 에러, TCP, Job 상태, 각 명령의 응답(`resp/*`) Topic을 모두 구독해 로그에 표시한다.
- **전체 작업 시작 값**: 검사 대상 치수(`job_info`: 지름/높이/두께/이동거리)와 격자 분할 계획(`plan`: 열 수/행 수/셀 가로/셀 세로/겹침)을 입력한다.
  - **▶ 전체 작업 시작**: `doosan/robot/req/job_cmd`에 `job_info` + `plan`을 실어 발행한다. 이 명령 자체가 전체 작업을 시작시킨다.
  - **높이(`job_info.height`)는 원통 전체 높이**(예: 6000 mm)이고, **셀 세로(`plan.cell_height`)는 로봇이 한 번에 닿는 셀 하나의 높이**(예: 800 mm)다. 둘은 다른 값이다 — ㄹ자는 셀 하나에만 그린다.
  - 스캐너 유효높이(`scan_h`)는 장비 고유값이라 여기서 보내지 않는다. RCS의 작업 영역 설정에 있는 값을 쓴다.
  - **⏸ 일시정지 / ⏵ 재개**: `doosan/robot/req/mc_cmd`에 `amr`/`cobot`을 `"stop"`/`"run"`으로 실어 발행한다.
  - **■ 정지**: `doosan/robot/req/job_clear`로 진행 중인 작업을 완전히 중단한다.
- **원점 프로브 확인**: 로봇은 3점 측정을 마치고 원점에 서면 **거기서 멈춘다**. 프로브가 벽에 제대로 눌렸는지는 로봇이 알 수 없어서 확인은 바깥이 한다.
  - RCS가 `doosan/robot/probe_gate` 로 `state: waiting` 을 보내면 두 버튼이 열린다.
  - **✔ 프로브 눌림 확인 — 스캔 시작**: `doosan/robot/req/probe_ack` 에 `pressed: true` 를 실어 보낸다. 로봇이 적심(비비기) 후 스캔으로 넘어간다.
  - **✘ 눌림 불량 — 대기 유지**: `pressed: false` + `reason`. 로봇은 풀리지 않고 원점에서 계속 기다린다 — 프로브가 안 붙은 채로 훑으면 검사가 성립하지 않는다.
  - ERUT 규격에서는 이 자리가 `evt/ready`(stage=at_origin) → `req/start` 다. 사내 MC 규격에는 대응하는 동작이 없어 통로를 따로 뒀다.
- **로봇 동작 속도 (2~100 %)**: 슬라이더로 값을 맞추고 **속도 전송**을 누르면 `doosan/robot/req/speed`(T-010)가 나간다. `10 / 25 / 50 / 75 / 100 %` 프리셋 버튼은 맞추는 즉시 보낸다. **작업 중에도 바로 반영된다** — 노드가 29999 `speed -v` 로 전달한다.
  - **Reset / EMS(비상정지)**: 각각 `doosan/robot/req/reset`, `doosan/robot/req/ems`를 발행한다.
- **송수신 로그**: 발행한 명령과 수신한 상태/응답을 시간 순으로 보여준다.

## 참고

- `operator-ui/tests/unit/mqtt_test_ui.py`도 비슷한 목적의 도구지만 Operator UI 저장소 하위에 있어 그쪽 개발·테스트 환경(venv, PyQt6 관련 의존성)이 갖춰져야 실행하기 편하다. 이 도구는 **Operator UI와 완전히 분리된, MC 쪽 관점의 시뮬레이터**로 별도 위치에 둔다.
- 실제 Broker가 없다면 `mosquitto`를 로컬에 띄워 테스트한다: `mosquitto -p 1883`.
