"""ERUT 표준 오류 번호 — error{code, name, detail} (if-0.9 탭5 145행~).

if-0.9 부터 요청의 결과는 맨 바깥 `status_code`(HTTP 원래 뜻 — 200 · 202 · 400 ·
409 · 500 · 501) 하나이고, **무엇이 문제인지는 error 객체**가 말한다. 숫자에
이름을 붙이던 402 · 408 · 412 · 423 · 428 은 없어지고 아래 번호로 옮겨 갔다.
같은 상황은 누가 알아채든 같은 번호다 — 장비가 거절한 것과 ERUT 브릿지가 막은
것이 같은 번호라 노트북이 한 가지로 안내한다.

name 은 그 번호에 고정된 이름(대문자 · 숫자 · _)이고, detail 은 사람이 읽는
설명(선택)이다. 제조사 코드(E9xxx)는 이름을 우리가 정한다.
"""

from __future__ import annotations

from typing import Any

#: 표준 번호 → 고정 이름. 응답이면 함께 쓰는 status_code 는 STATUS 에.
NAMES = {
    "E1002": "E_STOP",
    "E1003": "CALIBRATION_LOST",
    "E2001": "DRIVE_ERROR",
    "E2002": "HOME_FAILED",
    "E4001": "INTERNAL_ERROR",
    "E5001": "LOW_BATTERY",
    "E6001": "BAD_REQUEST",
    "E6002": "AREA_WIDTH",
    "E6003": "AREA_HEIGHT",
    "E6004": "WRONG_POINT",
    "E6005": "REQUEST_EXPIRED",
    "E6101": "BUSY",
    "E6102": "NOT_RESUMABLE",
    "E6103": "FAULT_ACTIVE",
    "E6104": "CALIBRATION_REQUIRED",
    "E6501": "NOT_IMPLEMENTED",
}

#: 거절 번호 → 응답의 status_code (탭5 145행~).
STATUS = {
    "E4001": 500, "E5001": 409,
    "E6001": 400, "E6002": 400, "E6003": 400, "E6004": 400, "E6005": 400,
    "E6101": 409, "E6102": 409, "E6103": 409, "E6104": 409,
    "E6501": 501,
}

BAD_REQUEST = "E6001"
AREA_WIDTH = "E6002"
AREA_HEIGHT = "E6003"
WRONG_POINT = "E6004"
REQUEST_EXPIRED = "E6005"
BUSY = "E6101"
NOT_RESUMABLE = "E6102"
FAULT_ACTIVE = "E6103"
CALIBRATION_REQUIRED = "E6104"
NOT_IMPLEMENTED = "E6501"
INTERNAL_ERROR = "E4001"


def make_error(code: str, name: str = "", detail: str | None = None,
               **fields: Any) -> dict:
    """error 객체를 만든다. name 을 비우면 표준 이름을 쓴다.

    detail 이 비면 칸을 뺀다(null 을 쓰지 않는다 — 탭0 10행). 함께 싣는 칸
    (max_width · max_height · waiting_point_id · probe …)은 fields 로 받는다.
    """
    error: dict[str, Any] = {"code": code, "name": name or NAMES.get(code, "UNKNOWN")}
    if detail:
        error["detail"] = str(detail)
    error.update({k: v for k, v in fields.items() if v is not None})
    return error
