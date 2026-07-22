# 데이터 관리 계획

## 데이터 종류

| 데이터 | 설명 | 예시 |
| --- | --- | --- |
| 검사 계획 | 검사 대상, 위치, 조건 | plan_id, target, scan_path |
| 작업 로그 | 검사 실행 단계와 이벤트 | start_time, operator, state |
| 장비 로그 | 장비 상태와 명령 이력 | robot_pose, lift_height, amr_position |
| 좌표 동기화 데이터 | 협동로봇, 리프트, AGV 위치를 통합한 3D 절대좌표 | robot_xyz, lift_y, agv_pose, encoder_time |
| UT 원본 데이터 | 초음파 검사기가 저장한 원본 파일 | A-scan, B-scan, C-scan |
| 분석 결과 | 결함 후보, 판정, 주석 | indication, depth, amplitude |
| 마킹 이벤트 | UT 기준 초과 신호와 마킹 시스템 트리거 이력 | trigger_time, marker_id, position |
| 리포트 | 작업자 검토용 결과 문서 | PDF, DOCX, XLSX |

## 작업 ID 규칙

작업 ID는 검사 데이터와 로그를 연결하기 위한 기본 키로 사용한다.

```text
YYYYMMDD_TARGET_LOCATION_SEQUENCE
```

예시:

```text
20260616_SMRVessel_WeldA_001
```

## 파일명 규칙

```text
{work_id}_{device}_{data_type}_{timestamp}.{ext}
```

예시:

```text
20260616_SMRVessel_WeldA_001_UT_raw_20260616T103000.dat
20260616_SMRVessel_WeldA_001_robot_pose_20260616T103000.csv
```

## 메타데이터 항목

| 항목 | 설명 |
| --- | --- |
| work_id | 작업 ID |
| operator | 작업자 |
| target | 검사 대상 |
| location | 검사 위치 |
| probe_id | 프로브 식별자 |
| ut_setting_id | 초음파 검사 설정 ID |
| scan_path_id | 로봇 스캔 경로 ID |
| coordinate_sync_id | 좌표 동기화 데이터 ID |
| marker_event_id | 마킹 이벤트 ID |
| start_time | 검사 시작 시각 |
| end_time | 검사 종료 시각 |
| result_status | 정상, 재검사 필요, 오류 |

## 보관 원칙

- 원본 데이터는 수정하지 않는다.
- 분석 결과와 리포트는 원본 데이터와 별도 파일로 저장한다.
- 데이터 삭제는 승인 절차를 둔다.
- 장비 설정값 변경 이력을 보관한다.
- 중요한 검사 데이터는 이중 백업을 검토한다.
- 좌표 동기화 데이터는 UT 원본 데이터와 동일 작업 ID로 연결한다.
- 마킹 트리거 이벤트는 발생 시각, 좌표, UT 기준 초과 신호와 함께 보관한다.
