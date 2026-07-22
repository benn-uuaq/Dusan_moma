import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "file:///C:/Users/rnd08_dev/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const OUT_DIR = "D:/Codex_ws/Project/erounsolution_project/docs/erounsolution_requests/requests/pptx";
const WORK_DIR = "D:/Codex_ws/Project/erounsolution_project/work/execution_plan_form";
const FINAL = `${OUT_DIR}/소형원자로_비파괴검사_수행계획서_폼.pptx`;

await fs.mkdir(OUT_DIR, { recursive: true });
await fs.mkdir(WORK_DIR, { recursive: true });

const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });

const C = {
  blue: "#0070C0",
  purple: "#514199",
  ink: "#111111",
  gray: "#666666",
  line: "#A6A6A6",
  light: "#F3F3F3",
  cell: "#FAFAFA",
};

function box(slide, left, top, width, height, fill = "none", line = C.line, name = "box") {
  return slide.shapes.add({
    geometry: "rect",
    name,
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: line, width: 1 },
  });
}

function text(slide, value, left, top, width, height, style = {}, name = "text") {
  const t = slide.shapes.add({
    geometry: "textbox",
    name,
    position: { left, top, width, height },
    fill: "none",
    line: { style: "solid", fill: "none", width: 0 },
  });
  t.text = value;
  t.text.style = {
    typeface: "Malgun Gothic",
    fontSize: 18,
    color: C.ink,
    ...style,
  };
  return t;
}

function header(slide, section, title, page) {
  text(slide, section, 56, 24, 160, 26, { fontSize: 17, bold: true, color: "#7A7A7A" }, "section");
  slide.shapes.add({
    geometry: "line",
    name: "header-line",
    position: { left: 210, top: 38, width: 910, height: 0 },
    line: { style: "solid", fill: C.line, width: 1 },
  });
  text(slide, "Studio 3S", 1130, 22, 110, 28, { fontSize: 20, bold: true, color: C.blue }, "studio");
  text(slide, title, 52, 68, 850, 36, { fontSize: 23, bold: true }, "slide-title");
  text(slide, `/ ${page}`, 1190, 682, 46, 18, { fontSize: 12, color: "#777777" }, "page");
}

function logo(slide, left = 62, top = 600) {
  text(slide, "이로운", left, top, 140, 44, { fontSize: 34, bold: true, color: C.purple }, "eroun-a");
  text(slide, "솔루션", left + 126, top + 8, 110, 32, { fontSize: 25, color: C.purple }, "eroun-b");
}

function table(slide, x, y, colWidths, rowHeights, cells, opts = {}) {
  const totalWidth = colWidths.reduce((a, b) => a + b, 0);
  const totalHeight = rowHeights.reduce((a, b) => a + b, 0);
  box(slide, x, y, totalWidth, totalHeight, "#FFFFFF", C.line, "table-outline");
  let cy = y;
  for (let r = 0; r < rowHeights.length; r++) {
    let cx = x;
    for (let c = 0; c < colWidths.length; c++) {
      const fill = r === 0 && opts.header !== false ? "#D9D9D9" : (c === 0 && opts.labelCol ? "#EFEFEF" : "#FFFFFF");
      box(slide, cx, cy, colWidths[c], rowHeights[r], fill, C.line, `cell-${r}-${c}`);
      const value = cells[r]?.[c] ?? "";
      text(slide, value, cx + 8, cy + 7, colWidths[c] - 16, rowHeights[r] - 10, {
        fontSize: opts.fontSize ?? 14,
        bold: r === 0 || (c === 0 && opts.labelCol),
        color: "#333333",
        alignment: opts.center ? "center" : "left",
      }, `cell-text-${r}-${c}`);
      cx += colWidths[c];
    }
    cy += rowHeights[r];
  }
}

function placeholder(slide, label, left, top, width, height) {
  box(slide, left, top, width, height, "#F8F8F8", "#D0D0D0", "placeholder");
  text(slide, label, left + 24, top + height / 2 - 16, width - 48, 32, {
    fontSize: 20,
    bold: true,
    color: "#777777",
    alignment: "center",
  }, "placeholder-label");
}

// 1. Cover
{
  const s = deck.slides.add();
  text(s, "소형 원자로 비파괴 검사 자동화", 64, 420, 520, 30, { fontSize: 21, bold: true }, "cover-kicker");
  text(s, "수행계획서 Form", 64, 456, 520, 40, { fontSize: 30, bold: true }, "cover-title");
  text(s, "작성일: YYYY년 MM월 DD일", 64, 502, 420, 24, { fontSize: 17 }, "cover-date");
  text(s, "작성 부서 / 작성자 / 버전 입력", 64, 532, 520, 24, { fontSize: 15, color: C.gray }, "cover-meta");
  logo(s, 92, 592);
  text(s, "Studio 3S", 545, 600, 210, 48, { fontSize: 36, bold: true, color: C.blue }, "cover-studio");
  slideFooterLine(s);
}

function slideFooterLine(slide) {
  slide.shapes.add({ geometry: "line", name: "footer-line-1", position: { left: 64, top: 580, width: 0, height: 75 }, line: { style: "solid", fill: "#D9D9D9", width: 1 } });
  slide.shapes.add({ geometry: "line", name: "footer-line-2", position: { left: 448, top: 580, width: 0, height: 75 }, line: { style: "solid", fill: "#D9D9D9", width: 1 } });
  slide.shapes.add({ geometry: "line", name: "footer-line-3", position: { left: 832, top: 580, width: 0, height: 75 }, line: { style: "solid", fill: "#D9D9D9", width: 1 } });
}

// 2. Revision history
{
  const s = deck.slides.add();
  header(s, "", "문서 개정 이력", 2);
  table(s, 42, 104, [85, 140, 770, 160], Array(18).fill(29), [
    ["버전", "개정일", "개정 내용", "작성자"],
    ["0.1", "YYYY-MM-DD", "초안 작성", "작성자"],
    ...Array(16).fill(["", "", "", ""]),
  ], { fontSize: 13, center: true });
}

// 3. Table of contents
{
  const s = deck.slides.add();
  text(s, "이로운솔루션", 22, 38, 350, 70, { fontSize: 48, bold: true, color: C.purple }, "brand");
  text(s, "소형 원자로 비파괴 검사 자동화 수행계획서", 400, 52, 660, 42, { fontSize: 28, bold: true }, "toc-title");
  box(s, 0, 132, 1280, 588, "#F1F1F1", "none", "toc-bg");
  const left = 198;
  const right = 682;
  const y = 160;
  box(s, left, y, 401, 512, "#FFFFFF", "none", "toc-left");
  box(s, right, y, 401, 512, "#FFFFFF", "none", "toc-right");
  text(s, "I. 프로젝트\n개요", left + 34, y + 48, 180, 80, { fontSize: 22, bold: true, color: "#444444" }, "toc-1");
  text(s, "1. 수행 목적\n2. 수행 범위\n3. 시스템 구성\n4. 검사 운영 절차\n5. 성능 및 품질 기준\n6. 수행 일정", left + 220, y + 48, 150, 150, { fontSize: 12 }, "toc-1-items");
  text(s, "II. 장비 구성", left + 34, y + 205, 180, 40, { fontSize: 21, bold: true, color: "#444444" }, "toc-2");
  text(s, "1. AMR\n2. 리프트/아웃트리거\n3. IMU\n4. 협동로봇\n5. 초음파 검사기\n6. 펌프/물탱크/분사기", left + 220, y + 205, 160, 150, { fontSize: 12 }, "toc-2-items");
  text(s, "III. 제어/데이터", left + 34, y + 386, 190, 40, { fontSize: 21, bold: true, color: "#444444" }, "toc-3");
  text(s, "1. 통신 구성\n2. 전원/신호 구성\n3. 데이터 관리", left + 220, y + 386, 160, 100, { fontSize: 12 }, "toc-3-items");
  text(s, "IV. 안전/검증", right + 34, y + 48, 190, 60, { fontSize: 21, bold: true, color: "#444444" }, "toc-4");
  text(s, "1. 안전 인터락\n2. 테스트 계획\n3. 리스크 관리\n4. 검증 일정", right + 220, y + 48, 160, 120, { fontSize: 12 }, "toc-4-items");
  text(s, "Studio 3S", 1116, 676, 130, 30, { fontSize: 24, bold: true, color: C.blue }, "studio-bottom");
}

// 4. Overview
{
  const s = deck.slides.add();
  header(s, "I. 프로젝트 개요", "1. 수행 개요", 4);
  table(s, 42, 104, [128, 530], [56, 56, 64, 108, 88], [
    ["수행 기간", "YYYY년 MM월 DD일 ~ YYYY년 MM월 DD일"],
    ["설치 장소", "검사 대상 위치 / 현장 주소 / 설치 장소 입력"],
    ["대상 설비", "소형 원자로 관련 검사 대상 및 검사 구간 입력"],
    ["프로젝트\n수행 범위", "§ AMR 수동 이동 및 자동 원주 이동\n§ 아웃트리거 고정 및 IMU 수평 보정\n§ 협동로봇 기반 UT 검사 수행\n§ 검사 데이터 저장 및 보고"],
    ["특이 사항", "§ 안전 인터락 조건 입력\n§ 현장 제약 및 통신 조건 입력"],
  ], { header: false, labelCol: true, fontSize: 14 });
  placeholder(s, "검사 대상 / 현장 이미지 삽입", 713, 104, 524, 222);
  placeholder(s, "AMR + 협동로봇 + UT 시스템 배치 이미지 삽입", 713, 340, 524, 356);
}

// 5. System configuration
{
  const s = deck.slides.add();
  header(s, "I. 프로젝트 개요", "2. 시스템 구성", 5);
  const boxes = [
    ["리모컨\n수동 이동", 72, 160],
    ["AMR\n자동 원주 이동", 295, 160],
    ["리프트/\n아웃트리거", 518, 160],
    ["IMU\n수평 확인", 741, 160],
    ["협동로봇\nUT 검사", 964, 160],
  ];
  for (const [label, x, y] of boxes) {
    box(s, x, y, 150, 86, "#FFFFFF", C.line);
    text(s, label, x + 10, y + 18, 130, 52, { fontSize: 18, bold: true, alignment: "center" });
  }
  for (const x of [232, 455, 678, 901]) {
    slideArrow(s, x, 203, 42);
  }
  placeholder(s, "시스템 구성도 또는 장비 배치도 삽입", 72, 310, 520, 300);
  table(s, 650, 310, [150, 380], [36, 52, 52, 52, 52, 52], [
    ["구성품", "주요 확인 사항"],
    ["AMR", "수동/자동 모드 전환, 원주 이동 기준"],
    ["아웃트리거", "전개/회수, 접지 감지, 수평 보정"],
    ["IMU", "roll/pitch 허용 기준, 안정화 시간"],
    ["협동로봇", "TCP, 접촉력, 안전 영역"],
    ["UT 시스템", "스캐너, 펌프, 물탱크, 분사기, SW"],
  ], { fontSize: 13 });
}

function slideArrow(slide, left, top, width) {
  slide.shapes.add({ geometry: "line", position: { left, top, width, height: 0 }, line: { style: "solid", fill: "#555555", width: 2, endArrowType: "triangle" } });
}

// 6. Equipment spec matrix
{
  const s = deck.slides.add();
  header(s, "II. 장비 구성", "3. 장비 사양 입력표", 6);
  table(s, 42, 104, [120, 210, 135, 135, 470], [34, ...Array(11).fill(46)], [
    ["구분", "항목", "전원", "통신", "비고 / 확인사항"],
    ["AMR", "모델명 / 제조사", "", "", "리모컨, 자동 원주 이동 기능"],
    ["리프트", "가반하중 / 스트로크", "", "", "높이 범위, 반복정밀도"],
    ["아웃트리거", "개수 / 제어 방식", "", "", "접지 감지, 높이 보정"],
    ["IMU", "모델명 / 정확도", "", "", "roll/pitch/yaw, 샘플링"],
    ["협동로봇", "모델명 / 작업반경", "", "", "SDK, 안전 기능"],
    ["UT 스캐너", "모델명 / 도면", "", "", "입출력, 필요 스펙"],
    ["UT Software", "운영 방식 / API", "", "", "데이터 포맷, SDK"],
    ["펌프", "모델명 / 압력", "", "", "입출력, 필요 압력"],
    ["물탱크", "용량 / 설치 위치", "", "", "보충 방식"],
    ["분사기", "모델명 / 노즐", "", "", "분사량, 설치 조건"],
    ["기타", "전원장치/센서/케이블", "", "", "추가 구성품"],
  ], { fontSize: 12, center: true });
}

// 7. Workflow
{
  const s = deck.slides.add();
  header(s, "I. 프로젝트 개요", "4. 검사 운영 절차", 7);
  const steps = [
    "리모컨 수동 이동",
    "기준 위치 확인",
    "아웃트리거 전개",
    "IMU 수평 확인",
    "협동로봇 UT 검사",
    "다음 위치 자동 이동",
  ];
  let x = 70;
  for (let i = 0; i < steps.length; i++) {
    box(s, x, 160, 160, 82, "#FFFFFF", C.line);
    text(s, `${i + 1}`, x + 14, 182, 26, 26, { fontSize: 18, bold: true, color: "#FFFFFF", alignment: "center" });
    box(s, x + 10, 178, 32, 32, "#000000", "#000000");
    text(s, steps[i], x + 48, 178, 92, 44, { fontSize: 15, bold: true, alignment: "center" });
    if (i < steps.length - 1) slideArrow(s, x + 168, 202, 35);
    x += 190;
  }
  table(s, 72, 310, [170, 410, 420], [36, 54, 54, 54, 54, 54], [
    ["단계", "입력 정보", "확인 기준"],
    ["수동 이동", "목적지, 작업자, 리모컨 상태", "주변 장애물 및 정위치"],
    ["수평 보정", "IMU roll/pitch, 아웃트리거 상태", "허용 기울기 이내"],
    ["UT 검사", "프로브, 설정값, 스캔 경로", "신호 품질 및 파일 저장"],
    ["위치 이동", "다음 검사 위치", "로봇 후퇴 및 프로브 분리"],
    ["종료", "로그, 데이터, 알람 이력", "작업 ID 기준 추적성"],
  ], { fontSize: 13 });
}

// 8. AMR leveling
{
  const s = deck.slides.add();
  header(s, "II. 장비 구성", "5. AMR 수평 보정 및 인터락", 8);
  table(s, 58, 118, [190, 390, 420], [38, 58, 58, 58, 58, 58], [
    ["항목", "입력/설정값", "확인/판정 기준"],
    ["IMU", "roll, pitch, yaw, 진동", "허용 기울기 이내"],
    ["아웃트리거", "전개 상태, 접지 상태, 높이", "전개 완료 및 접지 확인"],
    ["검사 허가", "수평 안정화 시간, 재보정 횟수", "조건 만족 시 RobotInspection"],
    ["이동 허가", "로봇 후퇴, 프로브 분리", "이동 전 안전 위치 확인"],
    ["오류 대응", "수평 불량, 접지 불량, 센서 이상", "검사 금지 및 작업자 확인"],
  ], { fontSize: 13 });
  placeholder(s, "아웃트리거 배치 / 수평 보정 개념도 삽입", 138, 470, 1000, 150);
}

// 9. Software and data
{
  const s = deck.slides.add();
  header(s, "III. 제어/데이터", "6. 소프트웨어 및 데이터 관리", 9);
  table(s, 58, 118, [180, 330, 500], [38, 58, 58, 58, 58, 58, 58], [
    ["모듈", "역할", "확인 사항"],
    ["장비 연동", "AMR/리프트/아웃트리거/IMU/로봇/UT 통신", "제조사 API, 통신 방식"],
    ["시퀀스 제어", "검사 상태 머신 실행", "정지, 재시작, 오류 처리"],
    ["수평 보정", "IMU 기반 아웃트리거 제어", "허용값, 재시도, 안정화"],
    ["데이터 저장", "UT 원본, 장비 로그, 작업 로그 저장", "작업 ID, 파일명 규칙"],
    ["운영 UI", "상태 표시, 알람, 검사 실행", "작업자 권한, 사용성"],
    ["리포트", "검사 결과 요약 및 산출물", "PDF/DOCX/XLSX 양식"],
  ], { fontSize: 13 });
}

// 10. Safety and validation
{
  const s = deck.slides.add();
  header(s, "IV. 안전/검증", "7. 안전 인터락 및 테스트 계획", 10);
  table(s, 58, 118, [220, 380, 410], [38, 56, 56, 56, 56, 56, 56], [
    ["구분", "조건", "검증 방법"],
    ["아웃트리거", "미전개 시 검사 금지", "상태 신호 오류 주입"],
    ["IMU", "수평 이탈 시 검사 금지", "roll/pitch 임계값 테스트"],
    ["AMR 주행", "검사 중 주행 명령 차단", "RobotInspection 상태에서 명령 입력"],
    ["로봇", "이동 전 안전 위치 확인", "후퇴 미완료 상태 테스트"],
    ["UT 데이터", "저장 실패 시 검사 중지", "저장소 오류 주입"],
    ["비상정지", "모든 자동 명령 중단", "E-Stop 입력 및 복구 절차"],
  ], { fontSize: 13 });
}

// 11. Schedule
{
  const s = deck.slides.add();
  header(s, "IV. 안전/검증", "8. 수행 일정 및 산출물", 11);
  table(s, 58, 118, [170, 250, 250, 250, 120], [38, 56, 56, 56, 56, 56, 56], [
    ["단계", "주요 작업", "산출물", "검토 기준", "일정"],
    ["1", "요구사항 확정", "요구사항 정의서", "주관사 확인", ""],
    ["2", "장비 사양 확정", "사양표/도면", "모델명/입출력", ""],
    ["3", "설계", "시스템 구성도/절차서", "인터락 반영", ""],
    ["4", "개발", "제어 SW/데이터 모듈", "시뮬레이션 통과", ""],
    ["5", "통합", "통합 테스트 결과", "장비 연동", ""],
    ["6", "검증", "검증 보고서", "안전/품질 기준", ""],
  ], { fontSize: 13, center: true });
}

// 12. Issue tracker
{
  const s = deck.slides.add();
  header(s, "", "부록. 이슈 및 확인사항 관리", 12);
  table(s, 42, 104, [80, 130, 610, 150, 170], Array(16).fill(34), [
    ["ID", "등록일", "이슈 / 확인사항", "담당", "상태"],
    ["ISS-001", "YYYY-MM-DD", "예: UT 스캐너 입출력 사양 확인 필요", "담당자", "대기"],
    ...Array(14).fill(["", "", "", "", ""]),
  ], { fontSize: 12, center: true });
}

for (const [index, slide] of deck.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  const png = await deck.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(`${WORK_DIR}/${stem}.png`, new Uint8Array(await png.arrayBuffer()));
}

const montage = await deck.export({ format: "webp", montage: true, scale: 0.5 });
await fs.writeFile(`${WORK_DIR}/final-montage.webp`, new Uint8Array(await montage.arrayBuffer()));

const pptx = await PresentationFile.exportPptx(deck);
await pptx.save(FINAL);

console.log(FINAL);
console.log(`${WORK_DIR}/final-montage.webp`);
