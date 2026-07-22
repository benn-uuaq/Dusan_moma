from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum


class CyclePhase(str, Enum):
    IDLE = "대기"
    SECURING = "정지·고정"
    LEVELING = "수평 보정"
    INSPECTING = "구간 검사 중"
    RETRACTING = "안전 위치"
    MOVING = "다음 구간 이동"
    PAUSED = "일시정지"
    COMPLETE = "검사 완료"


class ConnectionState(str, Enum):
    CONNECTED = "연결됨"
    CONNECTING = "연결 중"
    DELAYED = "지연"
    DISCONNECTED = "끊김"
    ERROR = "오류"


@dataclass(frozen=True)
class EquipmentState:
    name: str
    connection: ConnectionState = ConnectionState.CONNECTED
    last_received: datetime = datetime.now(timezone.utc)


@dataclass(frozen=True)
class CycleState:
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
        return round(self.completed_segments / self.total_segments * 100)

    def with_phase(self, phase: CyclePhase, **changes: object) -> "CycleState":
        return replace(self, phase=phase, **changes)


@dataclass(frozen=True)
class AppSnapshot:
    cycle: CycleState
    plc: EquipmentState
    amr: EquipmentState
    cobot: EquipmentState
    ut: EquipmentState


def initial_snapshot() -> AppSnapshot:
    return AppSnapshot(
        cycle=CycleState(),
        plc=EquipmentState("PLC"),
        amr=EquipmentState("AMR"),
        cobot=EquipmentState("Cobot"),
        ut=EquipmentState("UT"),
    )
