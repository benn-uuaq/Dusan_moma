"""수식 모듈이 예외 대신 결과와 오류를 전달할 때 사용하는 자료형."""

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class CalcResult(Generic[T]):
    """계산 성공 값 또는 사용자에게 전달할 오류 중 하나를 담는다."""
    value: T | None = None
    error: str | None = None


@dataclass(frozen=True)
class Vector3:
    """문서 좌표와 ROS 좌표 변환에 사용하는 3차원 벡터."""
    x: float
    y: float
    z: float
