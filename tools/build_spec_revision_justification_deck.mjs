import fs from "node:fs/promises";
import { Presentation, PresentationFile } from "file:///C:/Users/rnd08_dev/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const OUT_DIR = "D:/Codex_ws/Project/erounsolution_project/docs/erounsolution_requests/requests/pptx";
const WORK_DIR = "D:/Codex_ws/Project/erounsolution_project/work/spec_revision_justification";
const FINAL = `${OUT_DIR}/발주사_인증범위_수정근거자료.pptx`;

await fs.mkdir(OUT_DIR, { recursive: true });
await fs.mkdir(WORK_DIR, { recursive: true });

await fs.writeFile(
  `${WORK_DIR}/source-notes.txt`,
  [
    "Source 1: docs/erounsolution_requests/requests/이로운솔루션_3S_Robotics_로봇시스템_구매사양서_v.1.0.pdf",
    "Source 2: docs/09_robot_system_purchase_spec.md",
    "Source 3: docs/02_requirements.md",
    "Source 4: docs/03_system_architecture.md",
    "Source 5: docs/06_safety_risk.md",
    "Source 6: user-requested operating model: external manual, internal automatic, no simultaneous AMR+cobot motion",
  ].join("\n"),
  "utf8",
);

const deck = Presentation.create({ slideSize: { width: 1280, height: 720 } });

const C = {
  navy: "#173A63",
  blue: "#0F6CAD",
  teal: "#117C7E",
  gray: "#6B7280",
  line: "#C9CED6",
  light: "#F5F7FA",
  red: "#D94B4B",
  green: "#1F8A70",
  ink: "#111827",
};

function box(slide, left, top, width, height, fill = "none", line = C.line, name = "box", radius = 0) {
  return slide.shapes.add({
    geometry: radius ? "roundRect" : "rect",
    name,
    position: { left, top, width, height },
    fill,
    line: { style: "solid", fill: line, width: 1 },
    borderRadius: radius ? "rounded-xl" : undefined,
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

function line(slide, x1, y1, x2, y2, color = C.line, width = 1, arrow = false) {
  slide.shapes.add({
    geometry: "line",
    position: { left: x1, top: y1, width: x2 - x1, height: y2 - y1 },
    line: { style: "solid", fill: color, width, endArrowType: arrow ? "triangle" : undefined },
  });
}

function header(slide, section, title, page) {
  text(slide, section, 56, 24, 260, 26, { fontSize: 16, bold: true, color: "#7A7A7A" }, "section");
  line(slide, 214, 38, 1110, 38, C.line, 1, false);
  text(slide, "Studio 3S", 1135, 22, 100, 28, { fontSize: 18, bold: true, color: C.blue }, "studio");
  text(slide, title, 52, 64, 1080, 40, { fontSize: 24, bold: true, color: C.ink }, "title");
  text(slide, `/ ${page}`, 1188, 682, 40, 18, { fontSize: 12, color: "#777777" }, "page");
}

function table(slide, x, y, colWidths, rowHeights, cells, opts = {}) {
  const totalWidth = colWidths.reduce((a, b) => a + b, 0);
  const totalHeight = rowHeights.reduce((a, b) => a + b, 0);
  box(slide, x, y, totalWidth, totalHeight, "#FFFFFF", C.line, "table-outline");
  let cy = y;
  for (let r = 0; r < rowHeights.length; r++) {
    let cx = x;
    for (let c = 0; c < colWidths.length; c++) {
      const fill = r === 0 ? "#E5E7EB" : c === 0 && opts.labelCol ? "#F1F5F9" : "#FFFFFF";
      box(slide, cx, cy, colWidths[c], rowHeights[r], fill, C.line, `cell-${r}-${c}`);
      const value = cells[r]?.[c] ?? "";
      text(slide, value, cx + 8, cy + 6, colWidths[c] - 16, rowHeights[r] - 10, {
        fontSize: opts.fontSize ?? 12,
        bold: r === 0 || (c === 0 && opts.labelCol),
        color: "#374151",
        alignment: opts.center ? "center" : "left",
      }, `text-${r}-${c}`);
      cx += colWidths[c];
    }
    cy += rowHeights[r];
  }
}

// Slide 1: cover
{
  const s = deck.slides.add();
  text(s, "발주 사양 수정 근거자료", 62, 204, 500, 42, { fontSize: 34, bold: true, color: C.navy }, "cover-title");
  text(s, "이로운솔루션 로봇 시스템 구매사양서 기준", 62, 252, 580, 26, { fontSize: 19, color: C.gray }, "cover-sub");
  text(s, "AMR 운용 조건 정리 및 인증 범위 협의용", 62, 288, 520, 26, { fontSize: 19, color: C.gray }, "cover-sub2");
  box(s, 62, 352, 555, 172, C.light, "none", "summary-card", 10);
  text(s, "핵심 메시지", 92, 374, 120, 24, { fontSize: 18, bold: true, color: C.teal }, "kicker");
  text(
    s,
    "AMR은 외부에서는 리모컨 수동 조작, 내부 SMR 구간에서는 제한된 자동 이동으로 운용되며, 협동로봇과 AMR은 동시에 움직이지 않도록 인터락을 둔다.",
    92,
    406,
    500,
    88,
    { fontSize: 18, color: C.ink },
    "cover-body",
  );
  text(s, "이 문서는 발주 사양 수정 협의를 위한 설명 자료이며, 최종 인증 해석은 인증기관과 재확인해야 합니다.", 62, 556, 760, 28, { fontSize: 14, color: C.gray }, "footer-note");
  text(s, "이로운솔루션", 62, 620, 150, 38, { fontSize: 28, bold: true, color: "#5A49A0" }, "brand-a");
  text(s, "Studio 3S", 252, 626, 130, 28, { fontSize: 24, bold: true, color: C.blue }, "brand-b");
}

// Slide 2: operating model
{
  const s = deck.slides.add();
  header(s, "1. 운용 모델", "현재 운용 구조가 인증 범위를 어떻게 바꾸는가", 2);
  const y = 150;
  box(s, 54, y, 330, 390, "#FFFFFF", "#2B579A", "left", 8);
  box(s, 396, y, 140, 390, "#FFF4F4", C.red, "middle", 8);
  box(s, 548, y, 678, 390, "#F7FBFB", C.teal, "right", 8);
  text(s, "외부 구간", 78, 172, 120, 24, { fontSize: 20, bold: true, color: C.navy }, "left-title");
  text(s, "수동 원격 제어", 78, 208, 170, 24, { fontSize: 16, color: C.gray }, "left-sub");
  text(s, "AMR 수동 조작\n상태 모니터링\n작업 시작/정지", 78, 248, 220, 110, { fontSize: 18, color: C.ink, lineSpacing: 1.2 }, "left-body");
  text(s, "동시\n동작\n금지", 418, 226, 100, 120, { fontSize: 22, bold: true, color: C.red, alignment: "center" }, "mid-title");
  text(s, "충돌 예방\n안전 이격 유지\n인터락 신호 적용", 412, 360, 112, 80, { fontSize: 15, color: C.red, alignment: "center" }, "mid-body");
  text(s, "SMR 내부 구간", 572, 172, 150, 24, { fontSize: 20, bold: true, color: C.teal }, "right-title");
  text(s, "제한된 자동 이동", 572, 208, 180, 24, { fontSize: 16, color: C.gray }, "right-sub");
  text(s, "아웃트리거 고정\nIMU 수평 확인\n협동로봇 UT 검사\n자동 인덱스 이동", 572, 248, 260, 128, { fontSize: 18, color: C.ink, lineSpacing: 1.2 }, "right-body");
  line(s, 318, 344, 388, 344, C.blue, 2, true);
  line(s, 530, 344, 548, 344, C.red, 2, true);
  text(s, "외부 수동 -> 내부 자동 -> 다음 위치 이동", 66, 568, 420, 26, { fontSize: 15, color: C.gray }, "caption");
}

// Slide 3: standards scope
{
  const s = deck.slides.add();
  header(s, "2. 표준 범위", "표준은 장비의 '존재'보다 '운용 범위'를 본다", 3);
  table(
    s,
    58,
    128,
    [180, 380, 540],
    [38, 54, 54, 54, 54],
    [
      ["표준", "핵심 적용 대상", "이번 프로젝트 해석 포인트"],
      ["ISO 3691-4 / KS B 7320", "무인 이동차량, driverless truck 계열", "AMR이 내부 자동 이동을 수행하므로 검토 대상 유지"],
      ["ISO 10218-1/2 / ISO/TS 15066 / KS B 7327", "산업용 로봇 및 로봇 시스템 통합, 협동로봇 안전", "협동로봇이 작업에 포함되므로 통합 안전 검토 필요"],
      ["ISO 12100", "위험성 평가", "동시운전 금지, 인터락, 접근센서, 수평보정 논리를 근거화"],
      ["로봇산업진흥원 안전인증", "시스템 단위 안전성", "최종 인증 여부는 실제 운용 구조와 통합 범위에 따라 재해석 필요"],
    ],
    { fontSize: 12, labelCol: true },
  );
  text(
    s,
    "핵심은 'AMR+협동로봇 동시 이동형 장비'가 아니라, '외부 수동 이동 + 내부 제한 자동 이동 + 동시 동작 금지'라는 운용 구조입니다.",
    62,
    546,
    1120,
    44,
    { fontSize: 17, bold: true, color: C.navy },
    "bottom-claim",
  );
}

// Slide 4: evidence from purchase spec
{
  const s = deck.slides.add();
  header(s, "3. 근거", "구매사양서와 현재 운용 모델 사이의 차이", 4);
  table(
    s,
    58,
    124,
    [250, 430, 430],
    [40, 56, 56, 56],
    [
      ["구매사양서 요구", "현재 프로젝트 운용", "사양 수정 시 논점"],
      ["실외용 4륜 AGV, 라이다/근접센서, 아웃트리거, 리모컨", "외부 수동 이동은 유지, 내부는 제한 자동 이동", "자동 이동 범위를 내부 구간으로 한정할지 협의"],
      ["협동로봇 최대 주사속도 150 mm/s 이상, IP65", "협동로봇 검사는 유지하되 AMR과 동시 이동은 금지", "협동로봇 안전 사양은 유지하되 시스템 동작 조건 분리"],
      ["좌표 동기화, 알람, 마킹 시스템, 안전 인증", "엔코더 기반 절대좌표와 인터락, 안전펜스 반영", "대체 안전장치와 시험계획으로 범위 조정 가능"],
    ],
    { fontSize: 12, labelCol: true },
  );
  box(s, 62, 410, 1120, 154, "#F8FAFC", C.line, "note", 8);
  text(
    s,
    "설명 포인트: 현재 요구사항은 장비 사양을 그대로 부정하는 것이 아니라, 장비의 실제 운용 범위를 분리해 인증 범위와 검토 범위를 재정의하려는 것입니다. 즉, 발주사 수정 협의의 근거는 '장비 성능 저하'가 아니라 '운용 조건 명확화'입니다.",
    86,
    438,
    1066,
    92,
    { fontSize: 17, color: C.ink, lineSpacing: 1.15 },
    "note-body",
  );
}

// Slide 5: proposed wording
{
  const s = deck.slides.add();
  header(s, "4. 제안", "발주사에 제안할 사양 수정 문구", 5);
  box(s, 58, 128, 548, 470, "#FFFFFF", C.line, "left", 8);
  box(s, 644, 128, 548, 470, "#FFFFFF", C.line, "right", 8);
  text(s, "권장 수정안", 80, 150, 160, 24, { fontSize: 19, bold: true, color: C.green }, "left-title");
  text(
    s,
    "AMR은 외부 구간에서 리모컨으로 수동 조작하고, SMR 내부 구간에서만 제한된 자동 이동을 수행한다. 협동로봇 검사 중에는 AMR 주행을 금지하며, 아웃트리거 고정 및 IMU 수평 보정이 완료된 경우에만 협동로봇 검사를 허가한다.",
    80,
    190,
    490,
    108,
    { fontSize: 18, color: C.ink, lineSpacing: 1.2 },
    "left-body",
  );
  text(s, "수정 근거", 666, 150, 140, 24, { fontSize: 19, bold: true, color: C.blue }, "right-title");
  const bullets = [
    "운용 구조가 수동 이동과 자동 이동으로 분리됨",
    "AMR과 협동로봇의 동시 이동을 인터락으로 차단함",
    "외부 작업자는 원격 조작만 수행하고 내부 구간은 제한 자동 운용임",
    "안전펜스, 접근센서, 알람, 마킹 시스템으로 대체 통제를 둘 수 있음",
    "따라서 적용 표준 범위는 인증기관과 재질의할 근거가 있음",
  ];
  bullets.forEach((b, i) => {
    text(s, `• ${b}`, 666, 190 + i * 54, 486, 42, { fontSize: 16, color: C.ink }, `bullet-${i}`);
  });
}

// Slide 6: next actions
{
  const s = deck.slides.add();
  header(s, "5. 다음 단계", "발주사 협의에 필요한 준비물", 6);
  table(
    s,
    58,
    124,
    [230, 420, 420],
    [40, 54, 54, 54],
    [
      ["준비 항목", "설명", "산출물"],
      ["운용 모드 설명", "외부 수동 / 내부 제한 자동 / 동시 동작 금지 흐름", "1페이지 개념도"],
      ["안전 대체안", "접근센서, 안전펜스, 인터락, 비상정지 논리", "안전요건 요약표"],
      ["사양 수정안", "구매사양서 중 적용 범위 및 표현 수정안", "수정 문구 초안"],
    ],
    { fontSize: 12, labelCol: true },
  );
  box(s, 62, 404, 1120, 156, C.light, "none", "closing", 8);
  text(
    s,
    "권장 메시지",
    88,
    428,
    150,
    24,
    { fontSize: 18, bold: true, color: C.teal },
    "closing-title",
  );
  text(
    s,
    "이 문서는 인증 회피를 주장하기 위한 자료가 아니라, 실제 운용 구조에 맞게 발주 사양을 정리하고 인증 검토 범위를 명확히 하기 위한 협의 자료입니다. 최종 판단은 인증기관과 발주사가 함께 확정해야 합니다.",
    88,
    462,
    1060,
    78,
    { fontSize: 17, color: C.ink, lineSpacing: 1.15 },
    "closing-body",
  );
}

for (const [index, slide] of deck.slides.items.entries()) {
  const stem = `slide-${String(index + 1).padStart(2, "0")}`;
  const png = await deck.export({ slide, format: "png", scale: 1 });
  await fs.writeFile(`${WORK_DIR}/${stem}.png`, new Uint8Array(await png.arrayBuffer()));
}

const montage = await deck.export({ format: "webp", montage: true, scale: 0.45 });
await fs.writeFile(`${WORK_DIR}/montage.webp`, new Uint8Array(await montage.arrayBuffer()));

const pptx = await PresentationFile.exportPptx(deck);
await pptx.save(FINAL);

console.log(FINAL);
