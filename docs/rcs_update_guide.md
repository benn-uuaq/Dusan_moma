# RCS 리눅스 PC 업데이트 순서

현장 RCS 리눅스 PC 에서 깃허브의 새 버전을 받아 쓰는 순서다.
처음 설치가 아니라 **이미 설치해 쓰던 PC 를 새 버전으로 올리는 경우**다.
처음 설치는 [run_guide.md](run_guide.md) 「0. 최초 1회 준비」를 본다.

- 저장소: `benn-uuaq/Dusan_moma`
- 브랜치: `main` 과 `feature/ros2-workspace-setup` 은 같은 내용으로 올린다. 그 PC 가 받던 브랜치를 그대로 쓰면 된다.
- RCS 의 외부 통신은 **ERUT 브로커(MQTT) 하나**다. 사내 MC MQTT 와 UT 설정은 2026-10-07 판에서 뺐다 — 연결 설정에 「MQTT Broker 주소」·「MQTT 포트」 칸이 없어지고, 상단에 UT 표시와 설정 메뉴의 「UT 시스템 설정」이 없어진다. 예전 설정 파일에 남은 MQTT·UT 값은 읽지 않고 그냥 둔다.
- 아래 명령은 모두 워크스페이스 폴더(저장소를 받은 곳, 예: `~/dusan_ws`)에서 실행한다.
- **RCS 는 `./run_rcs.sh` 로 켠다.** 환경을 불러오고, ERUT 브로커가 닿는지 먼저 보고, `authbind` 로 RCS 를 띄운다. 그냥 `python3 -m smr_operator_ui` 로 켜면 TPAC(초음파 장비) 서버 포트 502 를 못 열어 TPAC 시뮬레이터·장비와 안 붙는다.

---

## 처음 한 번 (이 PC 에서 한 번만)

이미 해 둔 PC 면 건너뛴다.

**authbind** — TPAC 서버가 포트 502(1024 미만)를 열 수 있게 RCS 프로세스에만 권한을 준다.
`python3` 에 `setcap` 을 걸면 ROS 2 가 깨지므로 그 방법은 쓰지 않는다.

```bash
sudo apt-get install -y authbind
```

```bash
sudo touch /etc/authbind/byport/502 && sudo chmod 500 /etc/authbind/byport/502 && sudo chown "$(whoami)" /etc/authbind/byport/502
```

**MQTT 브로커(mosquitto)** — 이 PC 에서 ERUT 시뮬레이터(`mqtt_test/erut_sim.py`)로 로컬 시험을 할 때만 필요하다(연결 설정의 「ERUT Broker 주소」가 `127.0.0.1`). 현장처럼 ERUT 브로커에 바로 붙으면 설치하지 않아도 된다.

```bash
sudo apt install -y mosquitto mosquitto-clients
```

```bash
sudo systemctl enable --now mosquitto
```

---

## 1. RCS 와 로봇 제어 노드를 끈다

- RCS 창을 닫는다. 닫으면 ERUT 에 `offline` 을 직접 보내고 끊는다.
- 로봇 제어 노드(`robot_control_node`)를 터미널에서 따로 띄웠다면 `Ctrl+C` 로 끈다.
  RCS 가 노드를 데리고 있었다면 RCS 를 닫을 때 같이 꺼진다.
- 검사·마킹이 돌고 있으면 먼저 멈추고 로봇을 홈으로 보낸 뒤 끈다.

## 2. 받기 전에 그 PC 에서 고친 것이 있는지 본다

```bash
git status
```

- `nothing to commit, working tree clean` 이면 3번으로 간다.
- 고친 파일이 나오면 그 PC 에서 직접 고친 것이다. 무엇인지 확인한 뒤 정한다.
  - 버려도 되면: `git restore <파일>`
  - 남겨야 하면: 3S 담당자에게 알리고 저장소에 먼저 올린다.
- 설정 파일(`~/.config/smr-operator-ui/settings.json`)은 저장소 밖에 있어 여기 나오지 않는다. 연결 설정·ERUT 브로커 계정·작업 영역 값은 업데이트해도 그대로 남는다.

## 3. 새 버전을 받는다

```bash
git pull
```

받은 버전을 확인한다(맨 위 줄이 깃허브의 최신 커밋과 같아야 한다).

```bash
git log --oneline -1
```

## 4. ROS 패키지를 다시 빌드한다

로봇 제어 노드와 레지스터 표(`config/modbus_registers.json`)가 바뀌었을 수 있으므로 매번 다시 빌드한다.

```bash
source /opt/ros/humble/setup.bash
```

```bash
colcon build --symlink-install --packages-select elite_robot_controller
```

`Summary: 1 package finished` 가 나오면 된다. 차량 인터페이스(`vehicle_interfaces`)가 바뀐 업데이트면 패키지 이름 없이 전체를 빌드한다.

```bash
colcon build --symlink-install
```

## 5. 환경을 불러오고 RCS 를 다시 설치한다

```bash
source install/setup.bash && source .venv/bin/activate
```

```bash
pip install -e operator-ui
```

`-e`(편집 설치)로 깔려 있으면 받은 코드가 바로 쓰이지만, 새 의존 패키지가 생겼을 수 있으므로 한 번 실행해 둔다.

## 6. 다시 켠다

로봇 제어 노드를 따로 띄워 쓰는 PC 면 노드를 먼저 띄운다(평소 쓰던 명령 그대로). 그다음 RCS 를 켠다.

```bash
./run_rcs.sh
```

`bash: ./run_rcs.sh: Permission denied` 가 나오면 실행 권한이 빠진 것이다(10/7 첫 판은 저장소에 실행 권한 없이 올라갔다 — 이번 판에서 고쳤다). 받은 뒤에도 그러면 권한을 주거나 `bash` 로 켠다.

```bash
chmod +x run_rcs.sh
```

```bash
bash run_rcs.sh
```

켜기 전에 스크립트가 **저장된 연결 설정으로 ERUT 브로커에 실제로 붙어 보고** 결과를 찍는다. 정상이면 이렇게 나온다.

```
[ERUT] 설정 /home/<사용자>/.config/smr-operator-ui/settings.json
       브로커 <ERUT 주소>:<포트> · 장치 ID robot1 · 계정 <ERUT 계정> · 비밀번호 있음
[확인] ERUT 브로커 <ERUT 주소>:<포트> 에 붙었습니다 · mosquitto version 2.1.2.
```

`[경고]` 가 나오면 아래 「ERUT 브로커에 안 붙을 때」를 본다(RCS 는 그대로 켜진다).
스크립트 없이 켤 때는 반드시 `authbind --deep` 을 붙인다(5번 환경을 불러온 터미널에서):

```bash
authbind --deep python3 -m smr_operator_ui
```

## 7. 켠 뒤 확인

| 확인할 것 | 어디서 | 정상 |
|---|---|---|
| 로봇 연결 | RCS 상단 Cobot 표시 | 연결됨 |
| ERUT 브로커 연결 | RCS 알림 | 「ERUT 브로커에 연결되었습니다」 · 「ERUT 브릿지가 온라인입니다」 |
| 규격 판 번호 | MQTT Explorer `erut/robot1/evt/info` | `interface_version` 이 지금 규격 판(예: `"0.8"`) |
| 시각 | RCS 알림 | 「ERUT 브릿지와 시각이 … 어긋납니다」 경고가 없을 것(10초 넘게 어긋나면 ERUT 가 메시지를 버린다) |
| 홈 상태 | MQTT Explorer `erut/robot1/evt/home` | 팔이 접혀 있으면 `home` |

다시 켰으면 ERUT 에 알린다 — 꺼져 있는 동안 ERUT 는 시작·재개를 「로봇에 연결할 수 없습니다」로 거절한다.

## 8. 로봇 태스크가 바뀐 업데이트면 로봇에도 올린다

`robot_task/` 아래 `.task` 파일이 바뀐 업데이트는 **로봇 컨트롤러에 다시 올려야** 적용된다. `git pull` 만으로는 로봇에 들어가지 않는다.

바뀌었는지 보는 법(받기 전 버전과 비교 — `<이전 커밋>` 은 3번 전에 `git log --oneline -1` 로 본 값):

```bash
git diff --stat <이전 커밋> HEAD -- robot_task
```

올리는 파일 — 지금 쓰는 v5 기준:

| 파일 | 쓰는 때 |
|---|---|
| `robot_task/dusan_task_v5/dusan_v5_nosensor_seq.task` | 논센서 판 구역 검사 |
| `robot_task/dusan_task_v5/dusan_v5.task` | 센서 판 구역 검사 |
| `robot_task/dusan_task_v5/dusan_v5_nosensor_mark.task` · `dusan_v5_mark.task` | 마킹 |

- 기존에 올리던 방법(펜던트 파일 관리 등)으로 로봇의 같은 자리(`Dusan/dusan_v5/`)에 덮어쓴다. RCS 는 이 경로의 태스크를 불러 쓴다.
- `.task` 안에 스크립트 본문이 들어 있으므로 `.task` 만 올리면 된다(`scripts*/*.script` 는 따로 올리지 않는다).
- 설치 변수 목록(`dusan_v5.configuration.variables`)이 바뀐 경우에만 설치 설정도 다시 올린다. 바뀌었는지는 위 `git diff --stat` 에 이 파일이 나오는지로 본다.
- 올린 뒤 RCS 「태스크 판」 설정이 쓰려는 판(v5 · 센서/논센서)과 맞는지 확인한다.

## 9. 되돌리기 (새 버전에 문제가 있을 때)

받기 전 버전으로 돌아간다(`<이전 커밋>` 은 3번 전에 본 값).

```bash
git checkout <이전 커밋>
```

그 뒤 4~6번(빌드 · 설치 · 다시 켜기)을 똑같이 한다. 문제를 3S 담당자에게 알리고, 고친 버전이 올라오면 원래 브랜치로 돌아와 다시 받는다.

```bash
git checkout main && git pull
```

## ERUT 브로커에 안 붙을 때

RCS 알림에 「ERUT 브로커에 연결되었습니다」가 안 뜨거나 「ERUT 브로커 … 연결 거부 (rc=…)」가 뜬다. 또는 `./run_rcs.sh` 가 `[경고]` 를 낸다.

1. **점검 도구를 돌린다.** RCS 가 쓸 설정(어느 파일에서 읽었는지, 주소·포트·장치 ID·계정 — 비밀번호는 있는지만)을 찍고, RCS 와 같은 방식(MQTT 3.1.1·같은 계정)으로 실제로 붙어 본 뒤 브로커의 답을 알려 준다. 접속 ID 는 `3s-robot1-check` 라 돌고 있는 RCS 를 끊지 않는다.

   ```bash
   source .venv/bin/activate && python3 -m smr_operator_ui.erut_check
   ```

2. 결과에 따라 고친다.

   | 결과 | 뜻 · 조치 |
   |---|---|
   | `저장된 ERUT 브로커 주소가 없어 기본값으로 붙습니다` | 이 PC 에 ERUT 접속 정보가 저장된 적이 없다 — RCS 연결 설정에 ERUT 주소·포트·계정·비밀번호를 넣고 **저장**한다. 접속 정보는 저장소에 없고 PC 마다 설정 파일(`~/.config/smr-operator-ui/settings.json`)에만 있으므로, 새 PC 는 따로 넣어야 한다 |
   | `닿지 않습니다` | 그 주소·포트까지 네트워크가 안 닿는다 — 랜선·인터넷, 주소·포트 오타. 주소가 `127.0.0.1` 이면 이 PC 의 mosquitto 가 꺼져 있다(로컬 시험용) |
   | `rc=3` (서버 사용 불가) | **ERUT 브로커가 아닌 다른 브로커에 붙었다.** ERUT 브로커는 mosquitto 라 이 값을 보내지 않는다(계정이 틀리면 5). 주소·포트를 ERUT 가 알려 준 값으로 고친다. 주소가 `127.0.0.1` 이면 도구가 찍는 「이 PC 의 1883 포트」 줄로 그 포트를 쓰는 프로그램을 확인한다 |
   | `rc=5` (권한 없음) · `rc=4` | ERUT 계정·비밀번호가 틀렸다 |
   | `rc=2` | 클라이언트 ID 거부 — 「ERUT 장치 ID」 확인 |
   | `rc=1` | MQTT 3.1.1 을 안 받는 곳 — 주소·포트가 ERUT 브로커인지 확인 |
   | `[확인] … 붙었습니다 · mosquitto version …` 인데 RCS 에서만 안 붙음 | RCS 를 다시 켠다. 예전 판은 설정을 읽기 전에 기본값 `127.0.0.1:1883` 에 먼저 붙어 보다가 그 자리의 다른 브로커에 거절당하는 일이 있었다(10/7 두 번째 판에서 고쳤다) |

3. 로컬 시험(주소 `127.0.0.1`)이면 이 PC 의 mosquitto 를 본다.

   ```bash
   systemctl status mosquitto
   ```

   꺼져 있으면 켠다(없으면 「처음 한 번」의 설치부터).

   ```bash
   sudo systemctl enable --now mosquitto
   ```

4. 고쳤으면 RCS 연결 설정을 **저장**한다 — 저장하면 바로 다시 붙는다. 붙으면 알림에 「ERUT 브로커에 연결되었습니다」가 뜬다. 점검 도구를 다시 돌려 `[확인]` 을 본다.
5. 붙었는데 ERUT 요청이 안 먹으면 시각을 본다 — 10초 넘게 어긋난 메시지는 버려진다. 이 PC 를 인터넷 시간(NTP)에 맞춘다.

## 자주 나는 문제

| 증상 | 원인 · 조치 |
|---|---|
| `git pull` 이 `Your local changes … would be overwritten` 으로 멈춤 | 그 PC 에서 고친 파일이 있다 — 2번으로 돌아가 정리한다 |
| `./run_rcs.sh: Permission denied` | 실행 권한이 없다 — `chmod +x run_rcs.sh` 또는 `bash run_rcs.sh` (6번) |
| 「ERUT 브로커 … 연결 거부 (rc=3)」 | ERUT 브로커가 아닌 곳에 붙었다 — 「ERUT 브로커에 안 붙을 때」의 점검 도구 |
| TPAC(초음파 장비) 시뮬레이터·장비와 안 붙음, 통신 로그에 `[Errno 13] Permission denied` | `authbind` 없이 켰다 — `./run_rcs.sh` 로 켠다. 처음이면 「처음 한 번」의 authbind 설정 |
| `colcon build` 에서 `ros2`·`ament` 를 못 찾음 | `source /opt/ros/humble/setup.bash` 를 먼저 안 했다 |
| RCS 가 「로봇 제어 노드를 찾지 못했습니다」 | `source install/setup.bash` 를 안 한 터미널에서 켰다 |
| RCS 의 모든 로봇 버튼이 잠김 | 워크스페이스를 소싱하지 않아 레지스터 표를 못 읽었다 — 5번 다시 |
| ERUT 브로커에 안 붙음 | 위 「ERUT 브로커에 안 붙을 때」(설정 파일은 업데이트로 바뀌지 않는다) |
| 로봇 펜던트에 「ARC IS ZERO」 | 캘리브레이션(모재 지름) 전에 로봇을 출발시켰다 — ERUT 에서 calibrate 후 다시 시작 |
