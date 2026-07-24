"use client";

import { apiUrl, fetchAuth } from "@/lib/api";
import { displayGradeOrPending } from "@/lib/score-grade";
import type { AreaData, DistrictSales, DistrictStoreCount } from "@/types/models";
import { BarChart3, LoaderCircle, Maximize2, Minimize2, RefreshCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

interface CompetitionDrawerProps {
  open: boolean;
  expanded: boolean;
  areaCode: string;
  areaName: string;
  industryCode: string;
  industryName: string;
  onClose: () => void;
  onExpandedChange: (expanded: boolean) => void;
}

function latestPeriod<T extends { timestamp: string }>(rows: T[] | undefined) {
  return (rows || []).reduce((latest, row) => String(row.timestamp) > latest ? String(row.timestamp) : latest, "");
}

function periodLabel(value?: string | null) {
  const normalized = String(value || "");
  return /^\d{5}$/.test(normalized)
    ? `${normalized.slice(0, 4)}년 ${normalized.slice(4)}분기`
    : normalized || "기준 분기 없음";
}

function compactWon(value?: number | null) {
  if (value == null) return "자료 없음";
  if (value >= 100_000_000) return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
  if (value >= 10_000) return `${(value / 10_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만원`;
  return `${Math.round(value).toLocaleString()}원`;
}

export function CompetitionDrawer({
  open,
  expanded,
  areaCode,
  areaName,
  industryCode,
  industryName,
  onClose,
  onExpandedChange,
}: CompetitionDrawerProps) {
  const [data, setData] = useState<AreaData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!open || !areaCode || !industryCode) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
    }, 0);
    const url = new URL(
      apiUrl(`/areas/${encodeURIComponent(areaCode)}`),
      window.location.origin,
    );
    url.searchParams.set("industry_code", industryCode);
    fetchAuth(url.toString(), { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error("경쟁 분석 데이터를 불러오지 못했습니다.");
        return response.json() as Promise<AreaData>;
      })
      .then((payload) => {
        if (!controller.signal.aborted) setData(payload);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "경쟁 분석 데이터를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [areaCode, industryCode, open, reloadKey]);

  const competitionRows = useMemo(() => {
    const stores = data?.district_store_counts || [];
    const period = latestPeriod(stores);
    const sales = data?.district_sales || [];
    const salesPeriod = latestPeriod(sales);
    const saleByCode = new Map(
      sales.filter((row: DistrictSales) => String(row.timestamp) === salesPeriod)
        .map((row) => [row.industry_code, row]),
    );
    return stores
      .filter((row: DistrictStoreCount) => String(row.timestamp) === period)
      .sort((a, b) => b.store_count - a.store_count)
      .slice(0, 12)
      .map((row) => ({
        code: row.industry_code,
        name: row.industry_name || saleByCode.get(row.industry_code)?.industry_name || row.industry_code,
        stores: row.store_count,
        sales: saleByCode.get(row.industry_code)?.sales_amount ?? null,
      }));
  }, [data]);

  if (!open) return null;

  const analysis = data?.industry_analysis;
  const maxStores = Math.max(...competitionRows.map((row) => row.stores), 1);
  const axisRows = analysis ? [
    ["매출", analysis.axes.sales.display_grade],
    ["경쟁", analysis.axes.competition.display_grade],
    ["수요", analysis.axes.demand.display_grade],
    ["접근성", analysis.axes.accessibility.display_grade],
  ] as const : [];

  return (
    <aside
      role="dialog"
      aria-modal="false"
      aria-labelledby="competition-title"
      className={`surface-shadow fixed bottom-[calc(4rem+env(safe-area-inset-bottom))] right-0 top-16 z-[65] flex w-full flex-col border-l bg-card sm:w-[480px] lg:bottom-0 ${expanded ? "lg:w-[min(880px,78vw)]" : "lg:w-[min(520px,42vw)]"}`}
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b px-4">
        <div className="min-w-0">
          <p className="text-[11px] font-black text-primary">COMPETITION</p>
          <h2 id="competition-title" className="truncate text-base font-black">{areaName} · {industryName}</h2>
        </div>
        <div className="flex gap-1">
          <button type="button" onClick={() => onExpandedChange(!expanded)} className="hidden h-9 w-9 place-items-center rounded-lg hover:bg-muted lg:grid" aria-label={expanded ? "경쟁분석 축소" : "경쟁분석 확대"}>
            {expanded ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button type="button" onClick={onClose} className="grid h-9 w-9 place-items-center rounded-lg hover:bg-muted" aria-label="경쟁분석 닫기"><X className="h-4 w-4" /></button>
        </div>
      </header>

      <div className="scrollbar-natural flex-1 overflow-y-auto">
        {loading ? (
          <div className="grid min-h-80 place-items-center text-sm text-muted-foreground"><span className="flex items-center gap-2"><LoaderCircle className="h-5 w-5 animate-spin text-primary" /> 경쟁 구조를 불러오는 중입니다.</span></div>
        ) : error ? (
          <div className="m-5 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm" role="alert">
            <p className="font-semibold text-destructive">{error}</p>
            <button type="button" onClick={() => setReloadKey((key) => key + 1)} className="mt-3 inline-flex items-center gap-2 font-bold text-primary"><RefreshCcw className="h-4 w-4" /> 다시 시도</button>
          </div>
        ) : (
          <>
            <section className="border-b p-5">
              <div className="flex items-center justify-between gap-4 rounded-2xl bg-[#18211f] px-4 py-4 text-white">
                <span><span className="block text-xs text-[#b8c7c2]">동일 업종 입지 등급</span><strong className="mt-1 block text-sm">{industryName}</strong></span>
                <strong className="text-3xl font-black text-white">{displayGradeOrPending(analysis?.display_grade)}</strong>
              </div>
              {analysis && (
                <dl className="mt-4 grid grid-cols-4 divide-x rounded-xl border py-3 text-center">
                  {axisRows.map(([label, grade]) => (
                    <div key={label} className="px-2"><dt className="text-[10px] font-semibold text-muted-foreground">{label}</dt><dd className="mt-1 text-lg font-black text-primary">{displayGradeOrPending(grade)}</dd></div>
                  ))}
                </dl>
              )}
              <p className="mt-3 text-xs leading-5 text-muted-foreground">
                {periodLabel(analysis?.reference_quarter)}
              </p>
            </section>

            <section className="border-b p-5">
              <div className="flex items-center gap-2"><BarChart3 className="h-4 w-4 text-primary" /><h3 className="text-sm font-black">상권 내 업종 구성</h3></div>
              <div className="mt-4 space-y-4">
                {competitionRows.map((row) => (
                  <div key={row.code} className={row.code === industryCode ? "rounded-xl bg-accent p-3" : ""}>
                    <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                      <span className="min-w-0 truncate font-semibold">{row.name}</span>
                      <span className="shrink-0 font-black">{row.stores.toLocaleString()}개</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(3, row.stores / maxStores * 100)}%` }} /></div>
                    {expanded && <p className="mt-1 text-[11px] text-muted-foreground">최근 매출 {compactWon(row.sales)}</p>}
                  </div>
                ))}
                {!competitionRows.length && <p className="py-8 text-center text-sm text-muted-foreground">표시할 업종 구성 자료가 없습니다.</p>}
              </div>
            </section>

            {analysis && (
              <section className="p-5">
                <h3 className="text-sm font-black">선택 업종 현황</h3>
                <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                  <div className="rounded-xl border p-3"><dt className="text-xs text-muted-foreground">점포 수</dt><dd className="mt-1 font-black">{analysis.current_store_count?.toLocaleString() ?? "자료 없음"}</dd></div>
                  <div className="rounded-xl border p-3"><dt className="text-xs text-muted-foreground">최근 매출</dt><dd className="mt-1 font-black">{compactWon(analysis.current_sales_amount)}</dd></div>
                </dl>
              </section>
            )}
          </>
        )}
      </div>
    </aside>
  );
}
