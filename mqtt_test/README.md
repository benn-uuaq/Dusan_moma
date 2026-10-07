# mqtt_test

RCS 를 장비 없이 시험하는 도구 모음이다. RCS 의 외부 통신은 **ERUT 브로커(MQTT) 하나**와
TPAC(Modbus TCP 502)뿐이라, 도구도 그 둘을 흉내 낸다. (사내 MC MQTT 와 그 시뮬레이터
`mqtt_job_sim.py` 는 2026-10-07 에 뺐다 — 지금은 MQTT Explorer, 나중에는 랜선으로 ERUT 쪽 PC 와 붙는다.)

| 도구 | 하는 일 |
|---|---|
| `erut_sim.py` | ERUT Robot Service(로봇 브릿지) 대역 — 규격 시나리오를 자동으로 밟는다 |
| `alarm_sim.py` | RCS 를 찔러 `evt/error` 를 내게 하는 장애 조작판 |
| `tpac_encoder_sim.py` | RCS TPAC 브리지에 붙어 동기 신호를 보는 모니터 |
| `tpac_tcp_sim.py` | TPAC 쪽 Modbus TCP 서버 단독 모의기 |
| `run_sim_test.py` | 로봇 시뮬레이터 + ROS 노드 + RCS 를 띄워 전체 경로 자동 검증 |

시뮬레이터용 브로커가 없으면 이 PC 에 mosquitto 를 띄운다(`mosquitto -p 1883`). RCS 연결 설정의
「ERUT Broker 주소」를 `127.0.0.1` 로 두면 RCS 와 시뮬레이터가 같은 브로커로 붙는다.

## 실행 환경

`tkinter`(표준 라이브러리)와 `paho-mqtt`만 있으면 되고(`pip install paho-mqtt`), ROS 나 RCS 코드(`smr_operator_ui`)에는 의존하지 않는다(`run_sim_test.py` 만 예외). Windows 에서도 그대로 실행할 수 있다.

### WSL 에서 마우스 포인터가 안 보일 때 — Windows 로 띄우기

WSLg(WSL 2.7 / WSLg 1.0.73)에서는 X11 창이 커서를 지정하면 Windows 쪽 포인터가
숨겨진다. Tk 는 X11 로만 뜨므로 Tk 시뮬레이터(`erut_sim`·`alarm_sim`·`tpac_encoder_sim`)
위에서 포인터가 사라진다(RCS 는 Qt/Wayland 라 괜찮다). 이때는 Windows 파이썬으로 띄운다.

```bash
mqtt_test/win_sim.sh erut_sim
```

- Windows 파이썬에 `paho-mqtt` 필요: `py -m pip install paho-mqtt`
- 기본 포트가 **1884** 로 잡힌다. Windows 쪽 `localhost:1883` 은 Windows mosquitto 가
  쓰고 있어서, WSL mosquitto 에 localhost 전용 1884 리스너를 더했다
  (`/etc/mosquitto/conf.d/windows_sims.conf`). Windows `localhost:1884` → WSL 브로커
  (RCS 가 쓰는 브로커)로 간다.

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

협력사 ERUT 의 Robot Service(로봇 브릿지) 역할을 대신해 RCS와 통신을 시험한다. 규격은 이 폴더의 `MQTT_인터페이스/ERUT_검사로봇_MQTT_표준인터페이스_if-0.8.xlsx`(탭6·7 은 ERUT 내부라 3S 무관).

```bash
python3 mqtt_test/erut_sim.py
```

규격은 `content` 래퍼, 숫자 timestamp, `erut/{장치ID}/…` 응답이다. 현장에서는 이 자리에 MQTT Explorer(나중에는 ERUT 쪽 PC)가 선다.

**▶ 시나리오 자동 진행**이 규격 탭3의 ①~④를 순서대로 밟는다. 각 단계의 응답을 기다렸다가 넘어가고, 못 받으면 멈추고 무엇을 못 받았는지 알린다.

- 브릿지처럼 `erut/status` 를 **5초마다** 다시 낸다. 끄면(연결 해제) RCS 는 20초 뒤 브릿지가 없는 것으로 보고 하던 일을 일시정지한다.
- 마커는 ERUT 것이다. **마커 자동 발사**가 켜져 있으면 `evt/mark_ready` 를 받고 1초 뒤 `req/mark_next` 를 보낸다. 끄면 로봇은 마킹 자리에서 계속 기다린다.
- 상태(`evt/status`)·접촉(`evt/contact`)은 바뀔 때만 한 줄로, 진행률은 `pos`·구역 이름과 함께 보인다.
- `? teleport` 버튼은 모르는 동작을 보내 RCS 가 501 로 답하는지 본다.
- 배터리·충전과 캘리브레이션 총 둘레(`total_length_mm`)는 차량 자료가 오기 전이라 RCS 가 싣지 않는다.

## TPAC 엔코더 보드 모의기 (`tpac_encoder_sim.py`)

RCS 의 TPAC 브리지(Modbus TCP 서버)에 **클라이언트로 붙어** TPAC 엔코더 보드가 읽는
자리를 그대로 읽는다 — 브리지가 보내는 동기 신호가 제대로 가는지 보는 모니터다.
브리지는 여러 클라이언트를 받으므로 **실제 TPAC 과 동시에** 붙여도 된다.

```bash
python3 mqtt_test/tpac_encoder_sim.py --host 127.0.0.1 --port 502 --connect
mqtt_test/win_sim.sh tpac_encoder_sim --connect      # WSL 에서 포인터가 안 보일 때
```

- 읽는 자리: Coil 16~18(FC1) DO[0] 방향 / DO[1] 스캔 / DO[2] 리셋, Register 1 bit 0~2(FC3),
  Register 400~405 TCP.
- 보여 주는 것: DO 램프와 **FC1 / FC3 일치 여부**, 엔코더 보드처럼 센 라인 수·C-scan 카운터·
  누적 거리·동결 중 이동, 라인별 기록, 신호 변화 로그(직전 상태 유지 시간 — 50 ms 미만이면 빨강),
  최근 20 초 로직 분석기.
- 표준 라이브러리만 쓴다(Windows 파이썬 그대로 실행).

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

`run_sim_test.py`는 장비 없이 **전체 경로를 한 번에 확인**한다. 로봇 시뮬레이터와 ROS 노드를 스스로 띄우고, 운영 UI를 headless로 올린 뒤 시나리오를 차례로 돌린다. 끝나면 띄운 프로세스를 정리하고 통과/실패를 종료 코드로 알린다.

```bash
source /opt/ros/humble/setup.bash && source install/setup.bash
python3 mqtt_test/run_sim_test.py               # 네 시나리오 전부 (2열 × 3행)
python3 mqtt_test/run_sim_test.py 3 2           # 격자 크기를 바꿔서 (열, 행)
python3 mqtt_test/run_sim_test.py --only rcs io # 고른 것만
```

| 시나리오 | 확인하는 것 |
|---|---|
| `rcs` | RCS 단독 작업('검사 시작'과 같은 격자 순회)이 `1A → 1B → … → 2A …` 를 빠짐없이 도는지. ERUT 가 없으므로 셀마다 원점에서 RCS 가 접촉 2초 뒤 스스로 `scan_go` 를 쓰는지. 작업 영역 레지스터(256~259)가 셀 치수로 실리는지 |
| `erut` | ERUT 표준 if-0.8 한 바퀴 — 자기소개(`evt/info`) → `calibrate`(차량이 모재를 한 바퀴 — `total_length_mm` ≈ π·지름, 로봇은 움직이지 않고 query `home` 이 home) → `prepare`(차량·리프트만, activity preparing → `evt/ready`, 로봇은 홈) → `start`(로봇 3점 측정 → 시작점에서 `evt/contact` attached → 2초 뒤 스캔 · 정수 진행률 · `pos`/`location` 이 실린 `evt/complete`) → `mark`(점에서 로봇이 서서 `evt/mark_ready` ↔ `req/mark_next`) → `home`(`evt/complete action=home`) → 비상정지 → `reset`(`cleared=true`, idle 복귀) |
| `io` | I/O 화면의 로봇 디지털 출력이 레지스터 2 의 그 비트만 바꾸는지, 화면 표시가 로봇 값을 따라오는지 |
| `tpac` | 스캔 구간 신호 DO[0..2] 가 전진·후진·동결 순서대로 나가는지, 스캔 중에 리셋이 서지 않는지 |

로봇 시뮬레이터(`src/elite_robot_controller/tools/elite_robot_simulator.py`)는 지금 로봇 태스크와 **같은 상태 흐름**을 흉내 낸다. `task -p` 로 마킹 태스크를 불러 두면 마킹 흐름(자리에서 278 대기)을 돌고, 홈 스크립트가 오면 끝에 홈 플래그(276)를 세운다 — 차량 고정 대기(309) → 3점 측정(300~305 · 330~355) → 원점 대기(267 허가) → 적심 → ㄹ자 스캔(구간 신호 277) → 완료. 홈 플래그(276)와 태스크 상태(500)도 같이 움직인다.

- `--wall-error 0.3` : 실제 벽이 입력 반지름보다 이만큼 어긋나 있다고 친다(캘리브레이션이 이 값을 잡아낸다).
- `--max-rows 6` : ㄹ자 줄 수 상한. 순회를 보는 게 목적이라 줄을 다 그리지 않는다.

실제 로봇 기구학을 재현하는 것은 아니다 — 경로와 시간은 흉내만 내고, 실장비에서는 그 부분을 로봇이 스스로 채운다.
