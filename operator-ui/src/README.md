# Source layout

PyQt6 UI 구현은 `smr_operator_ui` Python 패키지 아래에서 진행한다.

- `smr_operator_ui/components/`: 재사용 가능한 `QWidget` 컴포넌트
- `smr_operator_ui/screens/`: `QStackedWidget`에 배치되는 화면
- `smr_operator_ui/services/`: PLC, ROS 2, 장비 통신 어댑터
- `smr_operator_ui/state/`: 명령 및 장비 상태 모델
- `smr_operator_ui/styles/`: Python 디자인 토큰과 QSS
- `smr_operator_ui/resources/`: 아이콘, 이미지, Qt 리소스

통신 작업은 GUI 스레드와 분리하고, 상태 갱신은 `pyqtSignal`을 통해 메인 스레드로 전달한다.
