# 실행 가이드 (터미널별 정리)

작업 흐름을 눈으로 보면서 개발하기 위한 실행 순서다. 터미널 4개를 띄운다.

- **터미널 1** — MQTT 브로커
- **터미널 2** — ROS 2 로봇 제어 노드 (VM 시뮬레이션 로봇에 연결)
- **터미널 3** — 운영 UI (RCS)
- **터미널 4** — MQTT 작업 계획 시뮬레이터 (외부 MC 대역)

모든 터미널은 `cd /home/user/dusan_ws`에서 시작한다. direnv가 걸려 있으면 ROS 소싱과 venv 활성화가 자동으로 된다. direnv를 안 쓰면 각 터미널에서 먼저 아래를 실행한다.

```bash
source /opt/ros/humble/setup.bash && source install/setup.bash && source .venv/bin/activate
```

---

## 0. 최초 1회 준비

MQTT 브로커가 아직 설치되어 있지 않다.

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
mosquitto_sub -h 127.0.0.1 -t 'doosan/#' -v
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
cd operator-ui && python -m smr_operator_ui
```

PostgreSQL에 설정을 저장하려면 실행 전에 접속 정보를 준다(없어도 UI는 동작하고 저장만 안 된다).

```bash
export SMR_DATABASE_URL="postgresql://사용자:비밀번호@127.0.0.1:5432/데이터베이스"
```

MQTT 브로커가 기본값(`127.0.0.1:1883`)이 아니면:

```bash
export SMR_MQTT_HOST=127.0.0.1
export SMR_MQTT_PORT=1883
```

WSL에서 창이 안 뜨면 WSLg가 동작하는지 확인한다. `echo $DISPLAY`가 비어 있으면 안 뜬다.

---

## 터미널 4 — MQTT 작업 계획 시뮬레이터 (외부 MC 대역)

실제 외부 장치가 아직 없으므로 이 도구가 MC 역할을 대신한다.

```bash
python3 mqtt_test/mqtt_job_sim.py
```

창이 뜨면:

1. **연결** 버튼으로 브로커에 붙는다 (기본 `127.0.0.1:1883`).
2. 작업 값을 입력한다.
3. **▶ 전체 작업 시작** 을 누르면 `doosan/robot/req/job_cmd`가 나가고, UI가 그걸 받아 작업을 시작한다.
4. **⏸ 일시정지 / ⏵ 재개 / ■ 정지** 로 중간에 개입한다.

하단 로그에 **오가는 MQTT 를 전부** 보여준다 (`doosan/#`, `erut/#`, `3s/test/#`).
tcp 는 초당 여러 번 오므로 한 줄로 접어서 보여주고, 체크를 끄면 감춘다 —
안 그러면 정작 봐야 할 `res`·`evt/complete`·`evt/error` 가 묻힌다.

**작업 시작을 누르면 실제로 일어나는 일** (`JobSequencer`가 자동 진행):

```
1A: AMR을 1구역으로 이동 (더미) → 리프트 0 mm (더미) → 로봇 play → 스캔 → 완료
1B: 리프트 780 mm (더미) → 로봇 play → 스캔 → 완료
…
2A: AMR을 2구역으로 이동 → 리프트 0 mm → …
12F까지 끝나면 전체 완료
```

셀마다 `doosan/robot/job_state`로 `waiting → executing → completed`가 나가고(`job_id`가 곧 격자 이름), 제로점 좌표는 격자 이름을 달고 `doosan/robot/tcp`로 나간다. 리프트/AMR은 실제 장비가 없어 더미가 잠시 뒤 "도착"을 돌려주는 방식이다.

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
발행하게** 하므로, `mqtt_job_sim.py` 에 보이는 메시지는 실제 경로 그대로다.

```
[alarm_sim] --3s/test/inject/error--> [RCS] --erut/robot1/evt/error--> [ERUT]
```

프리셋 8개(주행부 과부하·비상정지·배터리 부족·스캐너 통신 끊김·리프트 이상·
커플런트 부족과 각 해제)와 직접 입력이 있다. `level` 이 `stop`/`estop` 이면
**진행 중 작업이 자동으로 일시정지**된다 — 장애 상황에서 헛검사를 막기 위해서다.
`req/reset` 을 보내면 걸려 있던 장애가 해제된다.

## ERUT 통신 시험 (스테이션 대역)

협력사 ERUT 규격으로 RCS와 통신을 시험한다. 터미널 4 대신 이 도구를 띄운다.

```bash
python3 mqtt_test/erut_sim.py
```

**연결** 후 **▶ 시나리오 자동 진행**을 누르면 규격 탭3의 순서를 그대로 밟는다.

```
① 접속 확인   erut/status 발행 → req/query → res 200
② 캘리브레이션 req/calibrate → res 202 → evt/complete      (시험용)
③ 검사 준비   req/prepare  → res 202 → evt/ready           (시험용)
④ 구간 검사   req/start    → res 202 → evt/progress … → evt/complete   ← 로봇 실제 동작
⑤ 마킹        req/mark     → res 202 → evt/complete        (시험용)
```

각 단계의 응답을 기다렸다가 넘어가고, 못 받으면 그 자리에서 멈추며 무엇을 못 받았는지 로그에 남긴다. 개별 버튼(`pause`/`resume`/`abort`/`reset`)으로 중간에 끼어들 수도 있다.

**지금은 로봇만 진짜다.** ④만 실제로 로봇이 움직이고 ②③⑤와 배터리·좌표는 시험용 응답이다.

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
| 시뮬레이터를 쓰는데 Modbus만 안 붙음 | `modbus_port:=5502`를 빠뜨렸다. 시뮬레이터는 502가 아니라 5502를 쓴다 |
| MQTT 발행은 되는데 UI가 반응 없음 | UI와 시뮬레이터가 같은 브로커를 보는지 확인. `mosquitto_sub -t 'doosan/#' -v`로 메시지가 실제로 오가는지 본다 |
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

---

## 참고

- 작업 모델과 격자 순회 설계: [`docs/grid_sequencer_design.md`](grid_sequencer_design.md)
- MQTT 토픽 규격: [`docs/mqtt_topic_form.md`](mqtt_topic_form.md)
- 시뮬레이터 도구 설명: [`mqtt_test/README.md`](../mqtt_test/README.md)
