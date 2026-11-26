"""실물 크기 140 x 140 mm ArUco 마커를 벡터 PDF로 생성한다."""

import argparse

import cv2
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas

from .geometry import DICTIONARY, SIDE_M


def generate(filename, marker_id):
    """지정 ID의 ArUco 4x4_50 마커를 A4 중앙에 140 mm 크기로 그린다."""
    if not 0 <= marker_id < 50:
        raise ValueError("marker ID must be in [0, 49]")
    ar = cv2.aruco
    draw = ar.generateImageMarker if hasattr(ar, "generateImageMarker") else ar.drawMarker
    cells = draw(DICTIONARY, marker_id, 6)
    pdf = Canvas(str(filename), pagesize=A4)
    side = SIDE_M * 1000 * mm
    left, bottom = (A4[0]-side)/2, (A4[1]-side)/2
    # 비트맵 확대 대신 각 셀을 벡터 사각형으로 그려 DPI에 따른 크기·경계 왜곡을 피한다.
    for row in range(6):
        for col in range(6):
            if cells[row, col] == 0:
                pdf.rect(left+col*side/6, bottom+(5-row)*side/6, side/6, side/6, stroke=0, fill=1)
    pdf.drawCentredString(A4[0]/2, bottom+side+12*mm, "TOP   |   ArUco 4x4_50   |   ID %d" % marker_id)
    pdf.drawCentredString(A4[0]/2, bottom-12*mm, "140 x 140 mm outer black square   |   Print at 100%")
    pdf.save()


def main():
    """마커 ID와 출력 파일을 받는 명령행 진입점."""
    parser = argparse.ArgumentParser(description="Generate a vector A4 PDF with a 140 mm ArUco marker")
    parser.add_argument("--id", type=int, choices=range(50), default=0)
    parser.add_argument("--output")
    args = parser.parse_args()
    generate(args.output or f"marker_{args.id}_140mm.pdf", args.id)
