export type DisplayGrade =
  | "A+"
  | "A"
  | "B+"
  | "B"
  | "C+"
  | "C"
  | "D+"
  | "D"
  | "E+"
  | "E";

const DISPLAY_GRADES = new Set<DisplayGrade>([
  "A+",
  "A",
  "B+",
  "B",
  "C+",
  "C",
  "D+",
  "D",
  "E+",
  "E",
]);

export function displayGrade(
  detailed?: string | null,
  base?: string | null,
): DisplayGrade | null {
  if (detailed && DISPLAY_GRADES.has(detailed as DisplayGrade)) {
    return detailed as DisplayGrade;
  }
  if (base && DISPLAY_GRADES.has(base as DisplayGrade)) {
    return base as DisplayGrade;
  }
  return null;
}

export function displayGradeOrPending(
  detailed?: string | null,
  base?: string | null,
): DisplayGrade | "등급 보류" {
  return displayGrade(detailed, base) ?? "등급 보류";
}

const DERIVED_SCORE_LABEL = /점수|등급|입지|시장성|경쟁\s*구조|수요\s*기반|접근[·ㆍ\s-]*유입|비용\s*(?:여건|리스크)|데이터\s*신뢰도|성장[\/·\s-]*안정성|성장\s*잠재/;
const NUMERIC_SCORE_VALUE = /^\s*\d{1,3}(?:\.\d+)?\s*점\s*$/;

export function userFacingMetricDisplay(
  label?: string | null,
  value?: string | null,
): string {
  if (DERIVED_SCORE_LABEL.test(label || "") || NUMERIC_SCORE_VALUE.test(value || "")) {
    return displayGradeOrPending(value);
  }
  return value || "-";
}
