"""서비스와 UI 화면이 공유하는 불변 애플리케이션 상태 모델."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum


class CyclePhase(str, Enum):
    """하나의 검사 구간에서 반복되며 운영자에게 표시되는 단계."""

    IDLE = "대기"
    SECURING = "정지·고정"
    LEVELING = "수평 보정"
    INSPECTING = "구간 검사 중"
    RETRACTING = "안전 위치"
    MOVING = "다음 구간 이동"
    PAUSED = "일시정지"
    COMPLETE = "검사 완료"


class ConnectionState(str, Enum):
    """모든 장비 어댑터가 공통으로 사용하는 표준 연결 상태."""

    CONNECTED = "연결됨"
    CONNECTING = "연결 중"
    DELAYED = "지연"
    DISCONNECTED = "끊김"
    ERROR = "오류"


@dataclass(frozen=True)
class EquipmentState:
    """외부 하위 시스템 하나의 최근 연결 상태."""

    name: str
    connection: ConnectionState = ConnectionState.CONNECTED
    last_received: datetime = datetime.now(timezone.utc)


@dataclass(frozen=True)
class CycleState:
    """진행 중인 원주 검사 사이클의 불변 상태 스냅샷."""

    total_segments: int = 12
    current_segment: int = 1
    completed_segments: int = 0
    phase: CyclePhase = CyclePhase.IDLE
    running: bool = False
    paused: bool = False
    velocity_mps: float = 0.0
    lift_height_m: float = 1.2
    tcp_x_m: float = 0.0
    tcp_y_m: float = 0.0
    tcp_z_m: float = 0.0
    roll_deg: float = 0.0
    pitch_deg: float = 0.0
    battery_percent: int = 85
    safe: bool = True

    @property
    def progress_percent(self) -> int:
        """완료 구간 기준 진행률을 정수 백분율로 반환한다."""
        return round(self.completed_segments / self.total_segments * 100)

    def with_phase(self, phase: CyclePhase, **changes: object) -> "CycleState":
        """새 단계와 선택적인 필드 변경을 반영한 복사본을 만든다."""
        return replace(self, phase=phase, **changes)


@dataclass(frozen=True)
class AppSnapshot:
    """UI 구독자에게 전달하는 하나의 일관된 전체 상태 객체."""

    cycle: CycleState
    plc: EquipmentState
    amr: EquipmentState
    cobot: EquipmentState
    ut: EquipmentState


def initial_snapshot() -> AppSnapshot:
    """실장비 데이터 수신 전에 사용할 안전한 초기 대기 상태를 만든다."""
    return AppSnapshot(
        cycle=CycleState(),
        plc=EquipmentState("PLC"),
        amr=EquipmentState("AMR"),
        cobot=EquipmentState("Cobot"),
        ut=EquipmentState("UT"),
    )
