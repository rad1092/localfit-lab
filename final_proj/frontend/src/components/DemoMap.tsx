"use client";

import type { SpatialZoneAnalysis } from "@/types/spatial";
import { FlaskConical, MapPin, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";

interface DemoMapProps {
  lat: number;
  lng: number;
  areaName?: string;
  areaCode?: string;
  enableAnalysisTools?: boolean;
  onZoneAnalysisChange?: (analysis: SpatialZoneAnalysis | null) => void;
}

const DEMO_AREAS = [
  { code: "DEMO-HONGDAE", name: "홍대입구역", lat: 37.5563, lng: 126.9237, x: 25, y: 39, grade: "A+" },
  { code: "DEMO-YEONNAM", name: "연남동", lat: 37.5658, lng: 126.9232, x: 23, y: 27, grade: "B+" },
  { code: "DEMO-SEONGSU", name: "성수역", lat: 37.5446, lng: 127.0559, x: 69, y: 46, grade: "A" },
  { code: "DEMO-GANGNAM", name: "강남역", lat: 37.4979, lng: 127.0276, x: 59, y: 78, grade: "A" },
  { code: "DEMO-JAMSIL", name: "잠실역", lat: 37.5133, lng: 127.1002, x: 84, y: 67, grade: "B" },
];

const CATEGORIES = ["음식점", "카페", "편의점", "약국", "병원", "대형마트"];

export function DemoMap({
  lat,
  lng,
  areaName,
  areaCode,
  enableAnalysisTools = false,
  onZoneAnalysisChange,
}: DemoMapProps) {
  const [selectedCategory, setSelectedCategory] = useState("음식점");
  const activeArea = useMemo(() => {
    const exact = DEMO_AREAS.find((area) => area.code === areaCode);
    if (exact) return exact;
    const nearby = [...DEMO_AREAS].sort(
      (a, b) =>
        Math.abs(a.lat - lat) + Math.abs(a.lng - lng)
        - (Math.abs(b.lat - lat) + Math.abs(b.lng - lng)),
    )[0];
    return areaName && areaName !== "서울 전역" ? { ...nearby, name: areaName } : null;
  }, [areaCode, areaName, lat, lng]);

  return (
    <div className="relative h-full min-h-[320px] w-full overflow-hidden bg-[#e8f1ed]">
      <div
        className="absolute inset-0 opacity-80"
        style={{
          backgroundImage:
            "linear-gradient(rgba(15,118,110,.07) 1px, transparent 1px), linear-gradient(90deg, rgba(15,118,110,.07) 1px, transparent 1px)",
          backgroundSize: "34px 34px",
        }}
      />

      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 1000 640"
        preserveAspectRatio="none"
        role="img"
        aria-label="서울 데모 상권을 표시한 합성 지도"
      >
        <path d="M-40 405 C115 326 207 476 350 403 C493 330 607 461 760 382 C861 330 941 346 1040 290" fill="none" stroke="#8fd3e8" strokeWidth="46" opacity=".78" />
        <path d="M-40 405 C115 326 207 476 350 403 C493 330 607 461 760 382 C861 330 941 346 1040 290" fill="none" stroke="#cdeef4" strokeWidth="25" opacity=".9" />
        <path d="M102 69 L216 195 L389 210 L510 325 L699 278 L944 411" fill="none" stroke="#f2b66d" strokeWidth="9" strokeLinecap="round" opacity=".75" />
        <path d="M78 560 L220 458 L378 493 L512 390 L646 459 L858 522" fill="none" stroke="#f2b66d" strokeWidth="8" strokeLinecap="round" opacity=".68" />
        <path d="M180 18 L302 154 L300 310 L390 520 L486 666" fill="none" stroke="#bfc9c5" strokeWidth="5" strokeDasharray="12 8" opacity=".9" />
        <path d="M761 -20 L692 120 L738 246 L660 415 L706 660" fill="none" stroke="#bfc9c5" strokeWidth="5" strokeDasharray="12 8" opacity=".9" />
        <path d="M460 10 L524 138 L494 242 L570 349 L546 530 L592 660" fill="none" stroke="#f2b66d" strokeWidth="7" opacity=".65" />
        <g fill="#c8ddd2" opacity=".72">
          <path d="M14 54 Q108 16 180 80 L162 202 Q60 224 0 166 Z" />
          <path d="M758 44 Q902 0 1010 76 L1004 176 Q872 152 794 205 Z" />
          <path d="M344 40 Q440 15 489 88 L430 156 Q348 142 310 93 Z" />
        </g>
        <g fill="none" stroke="#92a9a0" strokeWidth="2" opacity=".45">
          <path d="M58 254 L170 228 L246 282 L340 248 L429 300" />
          <path d="M570 158 L648 205 L750 180 L844 227 L946 190" />
          <path d="M116 505 L225 548 L324 538 L413 590" />
          <path d="M623 535 L719 500 L812 553 L918 518" />
        </g>
      </svg>

      <div className="surface-shadow absolute left-3 top-3 z-20 max-w-[calc(100%-24px)] rounded-lg border bg-card/95 p-1.5 backdrop-blur">
        <p className="px-2 pb-1 pt-0.5 text-[10px] font-black text-muted-foreground">주변 시설 · 샘플 반경 500m</p>
        <div className="flex gap-1 overflow-x-auto" role="group" aria-label="데모 주변 시설 카테고리">
          {CATEGORIES.map((category) => (
            <button
              type="button"
              key={category}
              aria-pressed={selectedCategory === category}
              onClick={() => setSelectedCategory(category)}
              className={`h-8 whitespace-nowrap rounded-md px-3 text-xs font-semibold transition-colors ${
                selectedCategory === category
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              {category}
            </button>
          ))}
        </div>
      </div>

      <div className="absolute right-3 top-[74px] z-20 flex items-center gap-1.5 rounded-full border border-[#0f766e]/25 bg-[#e6fffa]/95 px-3 py-1.5 text-[10px] font-black text-[#115e59] shadow-sm backdrop-blur sm:top-3">
        <FlaskConical className="h-3.5 w-3.5" />
        실행 데모 · 샘플 지도
      </div>

      {DEMO_AREAS.map((area) => {
        const active = activeArea?.code === area.code;
        return (
          <div
            key={area.code}
            className="absolute z-10 -translate-x-1/2 -translate-y-full"
            style={{ left: `${area.x}%`, top: `${area.y}%` }}
          >
            <div className={`mb-1 whitespace-nowrap rounded-lg border px-2.5 py-1.5 text-[11px] font-black shadow-md ${
              active
                ? "border-primary bg-primary text-primary-foreground"
                : "border-white/80 bg-white/95 text-[#17231f]"
            }`}>
              {area.name} <span className={active ? "text-white/80" : "text-primary"}>{area.grade}</span>
            </div>
            <span className={`mx-auto grid h-7 w-7 place-items-center rounded-full border-4 border-white shadow-md ${
              active ? "bg-[#ef4444] text-white" : "bg-[#0f766e] text-white"
            }`}>
              <MapPin className="h-3.5 w-3.5" />
            </span>
          </div>
        );
      })}

      {activeArea && (
        <div
          className="absolute z-[12] h-32 w-32 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dashed border-primary/45 bg-primary/10"
          style={{ left: `${activeArea.x}%`, top: `${activeArea.y + 1}%` }}
          aria-hidden="true"
        />
      )}

      {enableAnalysisTools && (
        <div className="surface-shadow absolute bottom-4 right-4 z-20 max-w-[280px] rounded-xl border bg-card/95 p-3 backdrop-blur">
          <div className="flex items-center gap-2 text-xs font-black text-primary">
            <Sparkles className="h-4 w-4" />
            공간 분석 도구 미리보기
          </div>
          <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
            자유 영역 그리기와 공식 경계 계산은 실제 지도·공간 데이터가 연결된 운영 서비스에서 제공합니다.
          </p>
          <button
            type="button"
            onClick={() => onZoneAnalysisChange?.(null)}
            className="mt-2 text-[10px] font-bold text-primary underline underline-offset-2"
          >
            샘플 지도 유지
          </button>
        </div>
      )}
    </div>
  );
}
