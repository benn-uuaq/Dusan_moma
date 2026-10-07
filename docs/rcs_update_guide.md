# RCS 리눅스 PC 업데이트 순서

현장 RCS 리눅스 PC 에서 깃허브의 새 버전을 받아 쓰는 순서다.
처음 설치가 아니라 **이미 설치해 쓰던 PC 를 새 버전으로 올리는 경우**다.
처음 설치는 [run_guide.md](run_guide.md) 「0. 최초 1회 준비」를 본다.

- 저장소: `benn-uuaq/Dusan_moma`
- 브랜치: `main` 과 `feature/ros2-workspace-setup` 은 같은 내용으로 올린다. 그 PC 가 받던 브랜치를 그대로 쓰면 된다.
- 아래 명령은 모두 워크스페이스 폴더(저장소를 받은 곳, 예: `~/dusan_ws`)에서 실행한다.

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
python3 -m smr_operator_ui
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

## 자주 나는 문제

| 증상 | 원인 · 조치 |
|---|---|
| `git pull` 이 `Your local changes … would be overwritten` 으로 멈춤 | 그 PC 에서 고친 파일이 있다 — 2번으로 돌아가 정리한다 |
| `colcon build` 에서 `ros2`·`ament` 를 못 찾음 | `source /opt/ros/humble/setup.bash` 를 먼저 안 했다 |
| RCS 가 「로봇 제어 노드를 찾지 못했습니다」 | `source install/setup.bash` 를 안 한 터미널에서 켰다 |
| RCS 의 모든 로봇 버튼이 잠김 | 워크스페이스를 소싱하지 않아 레지스터 표를 못 읽었다 — 5번 다시 |
| ERUT 브로커에 안 붙음 | 연결 설정의 ERUT 주소·포트·계정 확인(설정 파일은 업데이트로 바뀌지 않는다) · 그 PC 의 인터넷 연결 확인 |
| 로봇 펜던트에 「ARC IS ZERO」 | 캘리브레이션(모재 지름) 전에 로봇을 출발시켰다 — ERUT 에서 calibrate 후 다시 시작 |
