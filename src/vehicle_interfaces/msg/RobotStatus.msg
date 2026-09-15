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
