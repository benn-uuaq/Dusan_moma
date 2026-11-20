# 차량(AMR+리프트) 상태. 차량 제어 노드가 주기적으로 발행한다.
std_msgs/Header header   # 타임스탬프 및 프레임 ID (필수 권장)

# 시스템 상태 및 작업
string state             # 예: "STOP", "RUNNING", "PAUSED", "HOLD", "ERROR"
string hold              # "SET", "RELEASE"
int32 error_code         # 0: 정상, 그 외 에러 코드
string error_msg         # 에러 세부 내용
string job_id            # 현재 수행 중인 작업 식별자

# 거리 및 속도 제어/모니터링 (단위: m, m/s)
float64 set_dist         # 목표 이동 거리
float64 mv_dist          # 실제 이동한 거리
float64 speed            # 현재 이동 속도

# 센서 데이터
float64 sen1_dist        # 센서 1 측정 거리
float64 sen2_dist        # 센서 2 측정 거리

# 6자유도 위치
Position6D position

# 리프트 높이
float64 lift_h           # 리프트 높이 (단위: m)

# ---- RCS 와 맞춘 필드 (2026-11-17 — docs/AGV_노드_토픽_서비스_목록_20261119.xlsx) ----------
# 값을 아직 못 채우면 기본값 그대로 둔다 — RCS 는 기본값을 「모름」으로 보고 조건에서 뺀다.
uint8 battery_pct 255        # 배터리 잔량 [%] 0~100. 255 = 모름 · 배터리 없음(ERUT 에 싣지 않는다)
bool charging false          # 충전 중
bool estop false             # 차량 비상정지 눌림 → RCS 가 로봇 · 작업을 세우고 ERUT E1002
bool obstacle_stop false     # 접근 감지(안전 스캐너)로 섰다 → RCS 가 작업을 일시정지
int8 level_state -1          # 아웃트리거 수평: 1 맞음 · 0 안 맞음(로봇 검사 불가) · -1 모름
float64 standoff_meas -1.0   # 지금 잰 거리 — 차량 안쪽 면 ↔ 모재 표면 [m]. 모르면 -1
string lap_state "IDLE"      # 캘리브레이션 주행(MeasureLap — LAP 한 바퀴 · ALIGN QR 정렬): IDLE · RUNNING · DONE · FAILED
float64 lap_path_m 0.0       # 이번 주행에서 차량 중심이 달린 거리 [m] (ALIGN 은 정렬하며 움직인 거리 · 참고)
bool lap_origin_ok false     # LAP: 원점(QR)을 다시 찾아 원점에 섰다 · ALIGN: QR 마커에 맞춰 섰다
string lap_error ""          # FAILED 일 때 까닭

