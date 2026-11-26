# 실행 가이드 (터미널별 정리)

작업 흐름을 눈으로 보면서 개발하기 위한 실행 순서다. 터미널 4개를 띄운다.

> 이미 설치해 쓰던 현장 RCS PC 를 새 버전으로 올리는 순서는 [rcs_update_guide.md](rcs_update_guide.md) 에 있다.

- **터미널 1** — MQTT 브로커 (ERUT 브로커 대역 — 로컬 시험용)
- **터미널 2** — ROS 2 로봇 제어 노드 (VM 시뮬레이션 로봇에 연결)
- **터미널 3** — 운영 UI (RCS)
- **터미널 4** — ERUT 시뮬레이터 (ERUT 쪽 PC 대역)

RCS 의 외부 통신은 **ERUT 브로커(MQTT) 하나**와 TPAC(Modbus TCP 502)뿐이다. 사내 MC MQTT 와
UT 설정은 2026-10-07 에 뺐다. 현장에서는 MQTT Explorer(나중에는 랜선으로 ERUT 쪽 PC)가 ERUT 자리에 선다.

모든 터미널은 `cd /home/user/dusan_ws`에서 시작한다. direnv가 걸려 있으면 ROS 소싱과 venv 활성화가 자동으로 된다. direnv를 안 쓰면 각 터미널에서 먼저 아래를 실행한다.

```bash
source /opt/ros/humble/setup.bash && source install/setup.bash && source .venv/bin/activate
```

---

## 0. 최초 1회 준비

로컬에서 ERUT 시험을 하려면 MQTT 브로커가 필요하다(현장처럼 ERUT 브로커에 바로 붙으면 필요 없다).

```bash
sudo apt install -y mosquitto mosquitto-clients
```

설치하면 시스템 서비스로 자동 실행되므로, 터미널 1을 생략하고 `systemctl status mosquitto`로 확인만 해도 된다. 자동 실행을 끄고 직접 띄우려면:

```bash
sudo systemctl disable --now mosquitto
```

워크스페이스 빌드가 안 되어 있으면:

```bash
colcon build --symlink-install
```

빌드 후 `direnv reload` 또는 `source install/setup.bash`로 다시 소싱한다.

---

## 터미널 1 — MQTT 브로커

```bash
mosquitto -v -p 1883
```

`-v`를 붙이면 오가는 메시지가 다 찍혀서 토픽 흐름을 눈으로 볼 수 있다. 시스템 서비스로 이미 돌고 있으면 이 터미널은 생략한다.

오가는 메시지만 따로 보고 싶으면 별도 터미널에서:

```bash
mosquitto_sub -h 127.0.0.1 -t 'doosan/robot/req/#' -t 'erut/#' -v
```

---

## 터미널 2 — ROS 2 로봇 제어 노드

### VM 시뮬레이션 로봇에 연결할 때 (이번 경우)

Elite VM이 켜져 있고 IP가 `192.168.227.134`라고 가정한다. VM은 실제 컨트롤러와 같은 포트를 쓰므로 `modbus_port`는 기본값 `502` 그대로 둔다.

```bash
ros2 launch elite_robot_controller elite_cs612.launch.py robot_ip:=192.168.227.134
```

VM의 IP가 다르면 그 주소로 바꾼다. VM 쪽 IP 확인은 Elite 펜던트 화면의 네트워크 설정에서 한다.

연결이 되면 로그에 세 채널(29999 / 30001 / 502) 연결 성공이 찍힌다. **연결이 안 돼도 노드는 죽지 않고** 경고만 남기며 대기하므로, VM을 나중에 켜고 UI의 `연결` 버튼으로 다시 시도해도 된다.

### 로봇이 아예 없을 때 (VM도 없이 순수 소프트웨어로)

터미널을 하나 더 열어 파이썬 시뮬레이터를 띄운다.

```bash
python3 src/elite_robot_controller/tools/elite_robot_simulator.py
```

이 시뮬레이터는 Modbus를 `5502`에 연다(권한 없이 띄우기 위해). 그래서 노드도 포트를 맞춰 줘야 한다.

```bash
ros2 run elite_robot_controller robot_control_node --ros-args \
  -p robot_ip:=127.0.0.1 -p modbus_port:=5502
```

### 상태 확인용 (선택)

별도 터미널에서 토픽이 실제로 나오는지 확인:

```bash
ros2 topic echo /robot/status/tcp_pose_zero
```

```bash
ros2 topic list | grep robot
```

로봇 상태를 Modbus로 직접 들여다볼 때(29999는 매번 명령을 던져야 해서 모니터링에 불편하다):

```bash
python3 -c "
from pyModbusTCP.client import ModbusClient
mb = ModbusClient(host='192.168.227.134', port=502, auto_open=True)
NAMES={0:'원격 미개방',1:'로컬 제어',2:'원격 제어'}
TASK={1:'실행 중',2:'일시 중지',3:'중지됨'}
print('제어 방법(71) :', NAMES.get(mb.read_input_registers(71,1)[0]))
print('태스크(500)   :', TASK.get(mb.read_input_registers(500,1)[0]))
print('스캔(290~298) :', mb.read_holding_registers(290,9))
print('작업영역(256) :', mb.read_holding_registers(256,4))
"
```

---

## 터미널 3 — 운영 UI (RCS)

**ROS 2가 소싱된 셸에서 실행해야** 자세 값이 UI에 들어온다. 소싱 안 된 셸에서 띄우면 UI는 뜨지만 로봇 값이 전부 `-`로 남는다.

```bash
./run_rcs.sh
```

`run_rcs.sh` 는 ERUT 에 붙는지 먼저 보고 `authbind --deep` 으로 RCS 를 띄운다. 그냥
`python -m smr_operator_ui` 로 켜면 TPAC 서버 포트 502 를 못 열어 TPAC 와 안 붙는다(최초 설정은
[rcs_update_guide.md](rcs_update_guide.md) 「처음 한 번」).

PostgreSQL에 설정을 저장하려면 실행 전에 접속 정보를 준다(없어도 UI는 동작하고 저장만 안 된다).

```bash
export SMR_DATABASE_URL="postgresql://사용자:비밀번호@127.0.0.1:5432/데이터베이스"
```

ERUT 접속 주소·포트·장치 ID·계정은 RCS **연결 설정**의 「ERUT Broker 주소」·「ERUT 포트」·「ERUT 계정」에서 정한다(기본 `127.0.0.1:1883`, 저장하면 바로 다시 붙는다).

WSL에서 창이 안 뜨면 WSLg가 동작하는지 확인한다. `echo $DISPLAY`가 비어 있으면 안 뜬다.

---

## 터미널 4 — ERUT 시뮬레이터 (ERUT 쪽 PC 대역)

협력사 ERUT 규격으로 RCS와 통신을 시험한다. 현장에서는 이 자리에 MQTT Explorer(나중에는 ERUT 쪽 PC)가 선다.

```bash
python3 mqtt_test/erut_sim.py
```

화면(if-0.13, 2026-11-24 다시 만듦):

- **연결** — 브로커 주소 · 포트 · 계정 · 비밀번호(선택) · 장치 ID. 붙으면 `erut/status`(online)를 5초마다 낸다
  (「생존 신호」를 끄면 RCS 가 20초 뒤 브릿지가 없는 것으로 보고 멈추는지 볼 수 있다).
- **모재** — 원통 · 곡판, 지름 · 높이 · (곡판) 가로 → `geometry`.
- **격자** — 시작 x · y, 구역 가로 · 세로, 열 · 행, 겹침 → 구역마다 `area` · `scan.overlap`(열 우선, 아래에서 위로).
  곡판은 열 1 개 · 판 가운데 기준으로만 만든다(차량이 판 가운데 QR 앞에서 움직이지 않는다).
- **스캔 · 작업** — 좌 · 우 끝 이동높이(`pitch_left` · `pitch_right`), 속도 %(`speed_pct` · `speed_mm_s`),
  홀더 채널 수(비우면 안 실음), Job ID 시작(구역마다 끝 숫자 + 1).
- **미리보기** — 펼친 검사면 위 구역. 눌러서 지금 구역을 고른다(노랑 = 지금, 초록 = 끝남, 빨강 테두리 = 모재 밖).
- **명령** — 접속 확인 query · 캘리브레이션 calibrate · 준비 prepare · 시작 start · 마킹 mark · 홈 home ·
  일시정지 pause · 재개(리스타트) resume · 정지(스탑) abort · 리셋 reset. prepare · start · mark 는 지금 구역으로 보낸다.
- **▶ 자동 진행** — 지금 구역부터 끝까지 아래 순서를 밟는다(원점이 잡혀 있으면 캘리브레이션은 건너뛸 수 있다).
  각 답을 기다렸다 넘어가고, 거절 · 실패(3xx~5xx)나 시간 초과면 까닭을 남기고 멈춘다.

```
① 접속 확인   erut/status 발행 → req/query → res 200
② 캘리브레이션 req/calibrate → res 202 → evt/complete
③ 검사 준비   req/prepare  → res 202 → evt/ready
④ 구간 검사   req/start    → res 202 → 3점 측정 → evt/contact(attached) → (ERUT 물 켬)
                                     → 2초 뒤 적심(비비기) → ㄹ자 스캔 → evt/progress … → evt/complete
   → 다음 구역 ③ 부터
⑤ 마킹        req/mark     → res 202 → evt/mark_ready ↔ req/mark_next → evt/complete
```

오른쪽에 RCS 상태(`evt/status` · 진행 · 접촉 · 홈)와 명령별 마지막 결과(`status_code` · 오류 번호 · 까닭)가 보인다.

**구간 검사 흐름(ERUT 작업)** — 로봇이 3점 측정 뒤 시작점에 붙으면(290 = 7) RCS 가 `evt/contact`(attached)를 낸다. ERUT 는 그걸 보고 물을 뿌리기 시작하고, RCS 는 2초(`CONTACT_LEAD_MS`) 뒤 스캔 허가(267 = 1)를 써서 로봇이 적심(비비기) → ㄹ자 스캔으로 넘어간다.

**RCS '검사 시작'(ERUT 없이)** — RCS 설정의 작업 영역·검사 대상으로 격자 전체를 돈다. 시작점에서는 기다려 줄 쪽이 없으므로 RCS 가 접촉 2초 뒤 스스로 풀어 준다.

```
1A: AMR을 1구역으로 이동 (더미) → 리프트 0 mm (더미) → 로봇 play → 스캔 → 완료
1B: 리프트 780 mm (더미) → 로봇 play → 스캔 → 완료
…
2A: AMR을 2구역으로 이동 → 리프트 0 mm → …
마지막 격자까지 끝나면 전체 완료
```

리프트/AMR은 실제 장비가 없어 더미가 잠시 뒤 "도착"을 돌려주는 방식이다(차량 모의기를 쓰면 그쪽이 움직인다 — 아래 「차량 모의기」).

로봇이 실제로 스캔을 완료해야 다음 셀로 넘어간다(레지스터 `290==5 && 295==1`). 시뮬레이터는 스스로 태스크를 돌지 않으므로, 순회를 끝까지 보려면 Modbus로 그 값을 직접 넣어 줘야 한다:

```bash
python3 -c "
from pyModbusTCP.client import ModbusClient
mb = ModbusClient(host='127.0.0.1', port=5502, auto_open=True)
mb.write_single_register(290, 4); mb.write_single_register(295, 0)   # 스캔 중
import time; time.sleep(1)
mb.write_single_register(290, 5); mb.write_single_register(295, 1)   # 완료
"
```

---

## 터미널 5 — 장애·알람 조작판

실제 장애 수집(PLC·로봇 알람)이 아직 없어서, 버튼으로 장애 상황을 만든다.

```bash
python3 mqtt_test/alarm_sim.py
```

**이 도구가 직접 evt/error 를 쏘는 게 아니다.** RCS 를 찔러서 **RCS 가 규격대로
발행하게** 하므로, `erut_sim.py` 에 보이는 메시지는 실제 경로 그대로다.

```
[alarm_sim] --3s/test/inject/error--> [RCS] --erut/robot1/evt/error--> [ERUT]
```

프리셋 8개(주행부 과부하·비상정지·배터리 부족·스캐너 통신 끊김·리프트 이상·
커플런트 부족과 각 해제)와 직접 입력이 있다. `level` 이 `stop`/`estop` 이면
**진행 중 작업이 자동으로 일시정지**된다 — 장애 상황에서 헛검사를 막기 위해서다.
`req/reset` 을 보내면 걸려 있던 장애가 해제된다.

## 한 번에 자동 검증하기

터미널 4개를 띄우지 않고 전체 파이프라인이 도는지만 확인하려면 자동 검증 스크립트를 쓴다. 시뮬레이터와 ROS 노드를 스스로 띄우고 UI를 headless로 올린 뒤, `1A`부터 마지막 셀까지 도는지 확인하고 프로세스를 정리한다.

```bash
python3 mqtt_test/run_sim_test.py
```

격자 크기를 바꾸려면 열·행을 인자로 준다.

```bash
python3 mqtt_test/run_sim_test.py 3 2
```

통과하면 종료 코드 `0`, 어긋나면 `1`과 함께 무엇이 다른지 출력한다. 자세한 내용은 [`mqtt_test/README.md`](../mqtt_test/README.md) 참고.

---

## 확인 순서 요약

```
터미널 1  브로커 띄우기
터미널 2  로봇 노드 → VM 연결 로그 확인
터미널 3  UI 띄우기 → 상단 연결 표시가 초록인지 확인
터미널 4  작업 시작 발행 → UI 메인 화면이 반응하는지 확인
          → VM 로봇이 실제로 움직이는지 확인
```

## 자주 걸리는 것

| 증상 | 원인 / 확인할 것 |
|---|---|
| UI에 자세 값이 전부 `-` | UI를 ROS 소싱 안 된 셸에서 띄웠다. 터미널 3에서 `ros2 topic list`가 되는지 확인 |
| 노드가 계속 연결 경고만 냄 | VM이 꺼져 있거나 IP가 다르다. VM 펜던트에서 IP 확인 후 `robot_ip:=` 교체 |
| RCS 「연결 안 됨」 · M2002 「로봇 Modbus 연결이 없습니다」인데 로봇 ping · 502 는 된다 (WSL) | **같은 PC 의 ROS 노드끼리 못 본다.** WSL 미러 네트워크(`networkingMode=mirrored`)에서는 127.0.0.1 로 가는 UDP 중 약 1.4 KB 넘는 것이 사라지고, Fast DDS 탐색 묶음(4 KB 남짓)이 그 크기라 탐색이 끝나지 않는다. RCS 는 켤 때 `FASTRTPS_DEFAULT_PROFILES_FILE` 을 `operator-ui/src/smr_operator_ui/config/fastdds_profile.xml`(한 데이터그램 1400 B)로 걸고, 로봇 · AGV 제어 노드도 물려받는다. 바탕화면 실행 아이콘(`tools/linux_launchers`)도 건다. 터미널에서 `ros2` 명령 · 노드를 따로 띄울 때는 같은 줄을 먼저 export 하고 `ros2 daemon start`(**`ros2 daemon stop` 은 쓰지 않는다** — 미러 네트워크에서는 127.0.0.1 의 빈 TCP 포트 접속이 거절되지 않고 멈춰서, 데몬이 없을 때 `ros2` 명령이 데몬을 찾다 멈춘다. 데몬이 엉뚱한 환경으로 떠 있으면 `pkill -f _ros2_daemon` 뒤 `ros2 daemon start`). 로봇 제어 노드는 연결 설정의 「협동로봇 IP」로 뜨고 설정을 불러오면 저절로 붙는다(기록 `~/.ros/log/smr_robot_node.log`) |
| 외부 PC 의 AGV 노드와 ROS 토픽이 안 오간다 (RCS 가 WSL) | 연결 설정 「외부 PC 확인」을 누른다. ping 은 되는데 ROS 로 안 보이면: ① 두 PC 의 `ROS_DOMAIN_ID` 가 같은지(RCS 연결 설정 「ROS 도메인」) ② 외부 PC 의 `ROS_LOCALHOST_ONLY` 가 1 이 아닌지 ③ **Windows Hyper-V 방화벽** — WSL(미러 네트워크)로 들어오는 UDP 는 기본으로 막힌다. 관리자 PowerShell 에서 한 번: `New-NetFirewallHyperVRule -Name ROS2-DDS-ToWSL -DisplayName 'ROS 2 DDS to WSL' -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol UDP -RemoteAddresses 192.168.11.0/24 -Action Allow` (지우기: `Remove-NetFirewallHyperVRule -Name ROS2-DDS-ToWSL`) ④ 외부 PC 도 `fastdds_profile.xml` 을 `FASTRTPS_DEFAULT_PROFILES_FILE` 로 건다(큰 UDP 조각이 WSL 에서 사라지지 않게) |
| 시뮬레이터를 쓰는데 Modbus만 안 붙음 | `modbus_port:=5502`를 빠뜨렸다. 시뮬레이터는 502가 아니라 5502를 쓴다 |
| ERUT 요청을 보냈는데 RCS 가 반응 없음 | RCS 연결 설정의 ERUT 브로커와 시뮬레이터(또는 MQTT Explorer)가 같은 브로커를 보는지 확인. `mosquitto_sub -t 'doosan/robot/req/#' -t 'erut/#' -v`로 메시지가 실제로 오가는지 본다. 시각이 10초 넘게 어긋나면 ERUT 가 버린다 |
| UI 창이 안 뜸 | WSLg 문제. `echo $DISPLAY` 확인 |
| 로봇이 안 움직임 | **① 원격 제어 모드인지 확인.** 로컬 제어면 `play`/`stop` 이 전부 거부된다. Modbus 레지스터 `71` 이 `2`(원격)여야 한다. `2`가 아니면 29999 로 `remoteControl -on`. ② 펜던트에서 태스크(`dusan_v1`)가 로드되어 있는지 확인 |
| 첫 셀에서 안 넘어감 | 로봇이 완료(`290==5 && 295==1`)를 안 냈다. Modbus 로 `290~298` 과 `500`(1 실행중/3 중지됨)을 직접 읽어 확인한다 |
| 로봇이 너무 많은 줄을 긁음 | 작업 영역(`256~259`)에 이전 값이 남아 있다. `dus_init` 은 태스크 시작 때만 읽으므로 값을 쓴 뒤 `stop`→`play` 로 재시작해야 반영된다 |

## 터미널 5 — 차량 모의기 (AMR·리프트·아웃트리거)

실제 차량 제어 노드가 없을 때 쓴다. 움직이는 모습이 창으로 보인다.

```bash
ros2 launch vehicle_sim vehicle_sim.launch.py
```

**RCS 에서 차량을 쓰려면 두 가지가 필요하다.**

1. RCS 를 **워크스페이스를 소싱한 터미널**에서 띄운다(`source install/setup.bash`).
   안 하면 `vehicle_interfaces` 를 못 읽어 차량에 명령을 보내지 못한다.
2. 연결 설정 → **차량 제어 = ROS 차량 노드** 로 바꾼다(고르면 바로 적용된다).
   기본값 '더미' 로 두면 화면 순서만 흐르고 차량은 가만히 있는다.

붙었는지는 상단 **AMR** 표시가 초록인지, 수동 제어 화면의 버튼이 열리는지로 본다.
작업을 시작할 때 차량이 안 붙어 있으면 알람으로 알려 준다.

| 증상 | 확인 |
|---|---|
| 작업은 도는데 차량이 안 움직임 | 차량 제어가 '더미' 다. 연결 설정에서 바꾼다 |
| ROS 차량으로 바꿨는데 명령이 안 나감 | RCS 를 소싱한 터미널에서 다시 띄운다 |
| 상태가 안 들어옴 | 토픽 이름 확인(`ros2 topic echo /vehicle/robot_status`). 이름이 다르면 `SMR_VEHICLE_NS` 로 맞춘다 |

AGV 담당이 만든 **실제 차량 제어 노드 + 테스트 PLC** 로 시험하려면 [`docs/agv_test_plc_guide.md`](agv_test_plc_guide.md)
를 본다(이 모의기와 같이 띄우지 않는다). 모의기는 그 제어 노드와 같은 규칙으로 돈다 — 도착하면 HOLD, 다음 주행 전
FINISH, 일시정지 PAUSE, 리프트 `lift_control`, 운전 모드(`ros2 param set /vehicle_sim mode 1` = 주행 수동).

---

## 참고

- 작업 모델과 격자 순회 설계: [`docs/grid_sequencer_design.md`](grid_sequencer_design.md)
- ERUT MQTT 규격: `mqtt_test/MQTT_인터페이스/ERUT_검사로봇_MQTT_표준인터페이스_if-0.10.xlsx`
- 옛 사내 MC 토픽 규격(지금은 안 씀): [`docs/mqtt_topic_form.md`](mqtt_topic_form.md)
- 시뮬레이터 도구 설명: [`mqtt_test/README.md`](../mqtt_test/README.md)
