"use client";

import { CommentSection } from "@/components/CommentSection";
import { CompetitionDrawer } from "@/components/CompetitionDrawer";
import { IndustryCombobox, type IndustryOption } from "@/components/IndustryCombobox";
import { KakaoMap } from "@/components/KakaoMap";
import { LoginModal } from "@/components/LoginModal";
import { useSelectedArea } from "@/components/selected-area-context";
import { apiUrl, fetchAuth, logProductEvent } from "@/lib/api";
import { displayGrade, displayGradeOrPending } from "@/lib/score-grade";
import type {
  AreaData,
  AreaRoneCostReference,
  AreaSalePriceProxy,
  DistrictFloating,
  DistrictPopulation,
  DistrictSales,
  DistrictStoreCount,
  RankingData,
} from "@/types/models";
import type { SpatialMetric, SpatialZoneAnalysis } from "@/types/spatial";
import { INDUSTRY_CODE_MAP } from "@/utils/constants";
import {
  ArrowLeft,
  ArrowUpRight,
  Bookmark,
  Building2,
  ChevronRight,
  CircleDollarSign,
  FileChartColumn,
  Layers3,
  LoaderCircle,
  MapPin,
  Ruler,
  Store,
  TrainFront,
  Users,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";

type InspectorTab = "overview" | "industry" | "trend" | "comments";

function latestPeriod<T extends { timestamp: string }>(rows: T[] | undefined) {
  return (rows || []).reduce(
    (latest, row) => String(row.timestamp) > latest ? String(row.timestamp) : latest,
    "",
  );
}

function sumLatest<T extends { timestamp: string }>(
  rows: T[] | undefined,
  value: (row: T) => number,
) {
  const period = latestPeriod(rows);
  return (rows || [])
    .filter((row) => String(row.timestamp) === period)
    .reduce((sum, row) => sum + value(row), 0);
}

function periodLabel(value?: string | null) {
  const normalized = String(value || "");
  return /^\d{5}$/.test(normalized)
    ? `${normalized.slice(0, 4)}년 ${normalized.slice(4)}분기`
    : normalized || "기준 분기 없음";
}

function compactNumber(value?: number | null) {
  if (value == null) return "자료 없음";
  if (value >= 100_000_000) return `${(value / 100_000_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억`;
  if (value >= 10_000) return `${(value / 10_000).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만`;
  return Math.round(value).toLocaleString();
}

function quarterDayCount(value: string) {
  const match = value.match(/^(\d{4})([1-4])$/);
  if (!match) return null;
  const year = Number(match[1]);
  const quarter = Number(match[2]);
  return Math.round(
    (Date.UTC(year, quarter * 3, 1) - Date.UTC(year, (quarter - 1) * 3, 1)) / 86_400_000,
  );
}

function bestRoneReference(
  rows: AreaRoneCostReference[] | undefined,
  metricCode: "rent" | "vacancy",
) {
  const propertyRank: Record<string, number> = {
    "중대형 상가": 0,
    "집합 상가": 1,
    "소규모 상가": 2,
    "일반 상가": 3,
  };
  return (rows || [])
    .filter((row) => row.metric_code === metricCode && Number.isFinite(Number(row.metric_value)))
    .sort((a, b) => {
      const mappingA = a.mapping_scope === "rone_level3_name_match_candidate" ? 0 : 1;
      const mappingB = b.mapping_scope === "rone_level3_name_match_candidate" ? 0 : 1;
      return mappingA - mappingB
        || (propertyRank[a.property_type || ""] ?? 9) - (propertyRank[b.property_type || ""] ?? 9);
    })[0];
}

function TradeContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { setSelectedArea } = useSelectedArea();
  const areaCode = searchParams.get("areaCode") || searchParams.get("area") || "";
  const industryCode = searchParams.get("industryCode") || searchParams.get("industry") || "";
  const competitionOpen = searchParams.get("panel") === "competition";
  const competitionExpanded = searchParams.get("expanded") === "true";
  const [areaData, setAreaData] = useState<AreaData | null>(null);
  const [rankings, setRankings] = useState<RankingData[]>([]);
  const [selectedIndustry, setSelectedIndustry] = useState<IndustryOption | null>(null);
  const [industryError, setIndustryError] = useState("");
  const [loading, setLoading] = useState(Boolean(areaCode));
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<InspectorTab>("overview");
  const [isFavorite, setIsFavorite] = useState(false);
  const [showLogin, setShowLogin] = useState(false);
  const [spatialAnalysis, setSpatialAnalysis] = useState<SpatialZoneAnalysis | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/areas/rankings"), { signal: controller.signal })
      .then((response) => response.ok ? response.json() : [])
      .then((payload) => setRankings(Array.isArray(payload) ? payload : []))
      .catch(() => setRankings([]));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setAreaData(null);
      setSpatialAnalysis(null);
      setActiveTab("overview");
      setError("");
      setLoading(Boolean(areaCode));
    }, 0);
    if (!areaCode) return () => window.clearTimeout(timer);

    const url = new URL(
      apiUrl(`/areas/${encodeURIComponent(areaCode)}`),
      window.location.origin,
    );
    if (industryCode) url.searchParams.set("industry_code", industryCode);
    fetchAuth(url.toString(), { signal: controller.signal, cache: "no-store" })
      .then(async (response) => {
        if (!response.ok && industryCode) {
          const fallback = await fetchAuth(
            apiUrl(`/areas/${encodeURIComponent(areaCode)}`),
            { signal: controller.signal, cache: "no-store" },
          );
          if (fallback.ok) {
            setSelectedIndustry(null);
            setIndustryError(`업종 코드를 확인해 주세요: ${industryCode}`);
            return fallback.json() as Promise<AreaData>;
          }
        }
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || "상권 데이터를 불러오지 못했습니다.");
        }
        return response.json() as Promise<AreaData>;
      })
      .then((payload) => {
        if (controller.signal.aborted) return;
        setAreaData(payload);
        setSelectedArea({
          areaCode: payload.area_code,
          areaName: payload.area_name,
          latitude: payload.latitude ?? null,
          longitude: payload.longitude ?? null,
        });
        if (payload.industry_analysis) {
          setSelectedIndustry({
            industry_code: payload.industry_analysis.industry_code,
            industry_name: payload.industry_analysis.industry_name,
          });
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "상권 데이터를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    if (localStorage.getItem("token")) {
      fetchAuth(apiUrl("/favorites"), { signal: controller.signal })
        .then((response) => response.ok ? response.json() : [])
        .then((payload) => {
          if (!controller.signal.aborted) {
            setIsFavorite(Array.isArray(payload) && payload.some((item) => item.area_code === areaCode));
          }
        })
        .catch(() => undefined);
    }

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [areaCode, industryCode, setSelectedArea]);

  const handleIndustrySelect = useCallback((option: IndustryOption | null) => {
    setSelectedIndustry(option);
    setIndustryError("");
    setSpatialAnalysis(null);
  }, []);

  const applyIndustry = () => {
    if (!areaCode) {
      setIndustryError("먼저 분석할 상권을 선택해 주세요.");
      return;
    }
    if (!selectedIndustry) {
      setIndustryError("검색 결과에서 분석할 업종을 선택해 주세요.");
      return;
    }
    const area = encodeURIComponent(areaCode);
    const industry = encodeURIComponent(selectedIndustry.industry_code);
    router.push(`/trade?areaCode=${area}&area=${area}&industryCode=${industry}&industry=${industry}`);
  };

  const returnToAreaPicker = () => {
    setAreaData(null);
    setSelectedIndustry(null);
    setIndustryError("");
    setSpatialAnalysis(null);
    setActiveTab("overview");
    setIsFavorite(false);
    setSelectedArea(null);
    window.location.assign("/trade");
  };

  const setCompetition = (open: boolean, expanded = competitionExpanded) => {
    const params = new URLSearchParams(searchParams.toString());
    if (open) {
      params.set("panel", "competition");
      if (expanded) params.set("expanded", "true");
      else params.delete("expanded");
    } else {
      params.delete("panel");
      params.delete("expanded");
    }
    router.replace(`/trade?${params.toString()}`);
  };

  const toggleFavorite = async () => {
    if (!areaCode) return;
    if (!localStorage.getItem("token") || localStorage.getItem("guest_mode") === "true") {
      setShowLogin(true);
      return;
    }
    const response = await fetchAuth(apiUrl(`/favorites/${encodeURIComponent(areaCode)}`), {
      method: isFavorite ? "DELETE" : "POST",
    }).catch(() => null);
    if (response?.ok) setIsFavorite((current) => !current);
  };

  const metrics = useMemo(() => {
    const floating = sumLatest(areaData?.district_floatings, (row: DistrictFloating) => row.floating_population);
    const floatingPeriod = latestPeriod(areaData?.district_floatings);
    const days = quarterDayCount(floatingPeriod);
    const industry = areaData?.industry_analysis;
    return {
      resident: sumLatest(areaData?.district_populations, (row: DistrictPopulation) => row.resident_population),
      worker: sumLatest(areaData?.district_populations, (row: DistrictPopulation) => row.worker_population),
      floating,
      floatingDaily: days ? Math.round(floating / days) : null,
      floatingPeriod,
      stores: industry
        ? industry.current_store_count ?? null
        : sumLatest(areaData?.district_store_counts, (row: DistrictStoreCount) => row.store_count),
      sales: industry
        ? industry.current_sales_amount ?? null
        : sumLatest(areaData?.district_sales, (row: DistrictSales) => row.sales_amount),
      rent: bestRoneReference(areaData?.rone_cost_references, "rent"),
      vacancy: bestRoneReference(areaData?.rone_cost_references, "vacancy"),
      saleProxy: (areaData?.sale_price_proxies || []).find(
        (row: AreaSalePriceProxy) => Number.isFinite(Number(row.sale_price_proxy_manwon_per_m2)),
      ),
      period: industry?.reference_quarter || [
        latestPeriod(areaData?.district_sales),
        latestPeriod(areaData?.district_store_counts),
        floatingPeriod,
        latestPeriod(areaData?.district_populations),
      ].filter(Boolean).sort().at(-1) || "",
    };
  }, [areaData]);

  const industryRows = useMemo(() => {
    const rows = areaData?.district_store_counts || [];
    const period = latestPeriod(rows);
    return rows
      .filter((row) => String(row.timestamp) === period)
      .sort((a, b) => b.store_count - a.store_count)
      .slice(0, 10)
      .map((row) => ({
        ...row,
        name: row.industry_name
          || areaData?.district_sales?.find((sale) => sale.industry_code === row.industry_code)?.industry_name
          || INDUSTRY_CODE_MAP[row.industry_code]
          || row.industry_code,
      }));
  }, [areaData]);

  const salesTrend = useMemo(() => {
    if (areaData?.industry_analysis) {
      return areaData.industry_analysis.history
        .filter((row) => row.sales_amount != null)
        .map((row) => ({ period: row.quarter, value: row.sales_amount || 0 }))
        .sort((a, b) => a.period.localeCompare(b.period));
    }
    const grouped = (areaData?.district_sales || []).reduce<Record<string, number>>((result, row) => {
      result[row.timestamp] = (result[row.timestamp] || 0) + row.sales_amount;
      return result;
    }, {});
    return Object.entries(grouped).map(([period, value]) => ({ period, value })).sort((a, b) => a.period.localeCompare(b.period)).slice(-8);
  }, [areaData]);

  const analysisGrade = areaData?.industry_analysis
    ? areaData.industry_analysis.display_grade
    : areaData?.display_grade || areaData?.grade;
  const maxIndustry = Math.max(...industryRows.map((row) => row.store_count), 1);
  const maxSales = Math.max(...salesTrend.map((row) => row.value), 1);

  return (
    <div className="h-full overflow-y-auto lg:overflow-hidden">
      <div className="grid min-h-full lg:h-full lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="relative h-[52vh] min-h-[360px] border-r lg:h-full">
          <KakaoMap
            lat={areaData?.latitude ?? 37.5665}
            lng={areaData?.longitude ?? 126.978}
            areaName={areaData?.area_name || "서울 전역"}
            areaCode={areaData?.area_code}
            level={areaData ? 4 : 8}
            resolveByName={false}
            enableAnalysisTools
            industryQuery={selectedIndustry?.industry_name || industryCode || undefined}
            onZoneAnalysisChange={setSpatialAnalysis}
          />
          {areaData && !competitionOpen && (
            <div className="surface-shadow absolute bottom-4 left-4 z-30 max-w-[calc(100%-32px)] rounded-2xl border bg-card/95 p-3 backdrop-blur">
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-primary"><MapPin className="h-5 w-5" /></span>
                <span className="min-w-0"><strong className="block truncate text-sm">{areaData.area_name}</strong><span className="block text-xs text-muted-foreground">{periodLabel(metrics.period)}</span></span>
              </div>
            </div>
          )}
        </section>

        <aside className="bg-card lg:min-h-0 lg:overflow-y-auto">
          {loading ? (
            <div className="grid min-h-[480px] place-items-center text-sm text-muted-foreground"><span className="flex items-center gap-2"><LoaderCircle className="h-5 w-5 animate-spin text-primary" /> 상권 데이터를 불러오는 중입니다.</span></div>
          ) : spatialAnalysis ? (
            <SpatialZoneInspector analysis={spatialAnalysis} rankings={rankings} selectedArea={areaData} onClose={() => setSpatialAnalysis(null)} />
          ) : !areaData ? (
            <AreaPicker rankings={rankings} error={error} onSelect={(code) => {
              void logProductEvent("area_selected", { area_code: code }).catch(() => undefined);
              router.push(`/trade?areaCode=${encodeURIComponent(code)}&area=${encodeURIComponent(code)}`);
            }} />
          ) : (
            <>
              <header className="border-b p-5">
                <button
                  type="button"
                  onClick={returnToAreaPicker}
                  className="mb-4 inline-flex h-9 items-center gap-1.5 rounded-lg border px-3 text-xs font-bold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  <ArrowLeft className="h-4 w-4" />
                  다른 상권 보기
                </button>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0"><p className="text-xs font-black text-primary">OFFICIAL TRADE AREA</p><h1 className="mt-1 truncate text-2xl font-black">{areaData.area_name}</h1><p className="mt-1 text-xs text-muted-foreground">상권코드 {areaData.area_code} · {periodLabel(metrics.period)}</p></div>
                  <div className="flex shrink-0 gap-1">
                    <button type="button" onClick={() => void toggleFavorite()} aria-label={isFavorite ? "즐겨찾기 해제" : "즐겨찾기 추가"} className={`grid h-9 w-9 place-items-center rounded-lg border ${isFavorite ? "border-primary bg-accent text-primary" : "text-muted-foreground hover:bg-muted"}`}><Bookmark className={`h-4 w-4 ${isFavorite ? "fill-current" : ""}`} /></button>
                    <Link href={`/ai?areaCode=${encodeURIComponent(areaCode)}&area=${encodeURIComponent(areaCode)}${industryCode ? `&industryCode=${encodeURIComponent(industryCode)}&industry=${encodeURIComponent(industryCode)}` : ""}`} aria-label="AI 상세리포트" className="grid h-9 w-9 place-items-center rounded-lg bg-primary text-primary-foreground"><FileChartColumn className="h-4 w-4" /></Link>
                  </div>
                </div>

                <div className="mt-4 rounded-2xl border bg-background p-4">
                  <IndustryCombobox
                    selected={selectedIndustry}
                    initialCode={industryCode || null}
                    onSelect={handleIndustrySelect}
                    onInvalidInitialCode={(code) => setIndustryError(`존재하지 않는 업종 코드입니다: ${code}`)}
                  />
                  {industryError && <p className="mt-2 text-xs font-semibold text-destructive" role="alert">{industryError}</p>}
                  <button type="button" onClick={applyIndustry} disabled={!selectedIndustry} className="mt-3 h-10 w-full rounded-xl bg-primary text-sm font-bold text-primary-foreground disabled:opacity-40">선택 업종으로 분석</button>
                </div>

                <div className="mt-4 flex items-center justify-between rounded-2xl bg-[#18211f] px-4 py-3 text-white">
                  <span><span className="block text-[11px] font-semibold text-[#b8c7c2]">{areaData.industry_analysis ? `${areaData.industry_analysis.industry_name} 입지 등급` : "상권 맥락 등급"}</span><span className="mt-1 block text-sm font-bold">{areaData.industry_analysis ? "동일 업종 서울 비교" : "서울 동일 기준 비교"}</span></span>
                  <strong className="text-3xl font-black text-white">{displayGradeOrPending(analysisGrade)}</strong>
                </div>
              </header>

              <div className="grid grid-cols-4 border-b px-2 pt-2">
                {([[
                  "overview", "개요",
                ], ["industry", "업종"], ["trend", "추이"], ["comments", "의견"]] as const).map(([key, label]) => (
                  <button key={key} type="button" onClick={() => setActiveTab(key)} className={`h-10 border-b-2 text-sm font-bold ${activeTab === key ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>{label}</button>
                ))}
              </div>

              {activeTab === "overview" && (
                <div>
                  <dl className="grid grid-cols-2 border-b">
                    <Metric icon={Users} label="일평균 유동인구" value={compactNumber(metrics.floatingDaily)} unit={metrics.floatingDaily == null ? "" : "명/일"} />
                    <Metric icon={Store} label={areaData.industry_analysis ? "선택 업종 점포" : "전체 점포"} value={compactNumber(metrics.stores)} unit="개" divided />
                    <Metric icon={CircleDollarSign} label={areaData.industry_analysis ? "선택 업종 매출" : "최근 분기 매출"} value={compactNumber(metrics.sales)} unit="원" top />
                    <Metric icon={Building2} label="R-ONE 임대료 참고" value={metrics.rent?.metric_value == null ? "자료 없음" : Number(metrics.rent.metric_value).toLocaleString()} unit={metrics.rent?.unit || ""} divided top />
                  </dl>

                  <section className="border-b p-5">
                    <h2 className="text-sm font-black">수요 구성</h2>
                    <dl className="mt-3 divide-y rounded-xl border px-3">
                      <DemandRow label="상주인구" value={metrics.resident} unit="명" />
                      <DemandRow label="직장인구" value={metrics.worker} unit="명" />
                      <DemandRow label="분기 유동인구" value={metrics.floating} unit="명" />
                      <DemandRow label="일평균 유동인구" value={metrics.floatingDaily} unit="명/일" note={`${periodLabel(metrics.floatingPeriod)} 누계 ${metrics.floating.toLocaleString()}명을 분기 일수로 환산`} />
                    </dl>
                  </section>

                  {areaData.industry_analysis && (
                    <section className="border-b p-5">
                      <h2 className="text-sm font-black">등급 구성</h2>
                      <dl className="mt-3 grid grid-cols-4 divide-x rounded-xl border py-3 text-center">
                        {([[
                          "매출", areaData.industry_analysis.axes.sales.display_grade,
                        ], ["경쟁", areaData.industry_analysis.axes.competition.display_grade], ["수요", areaData.industry_analysis.axes.demand.display_grade], ["접근", areaData.industry_analysis.axes.accessibility.display_grade]] as const).map(([label, grade]) => (
                          <div key={label} className="px-1"><dt className="text-[10px] text-muted-foreground">{label}</dt><dd className="mt-1 text-lg font-black text-primary">{displayGradeOrPending(grade)}</dd></div>
                        ))}
                      </dl>
                    </section>
                  )}

                  <CostReferences rent={metrics.rent} vacancy={metrics.vacancy} saleProxy={metrics.saleProxy} />

                  <div className="grid grid-cols-2 divide-x p-4">
                    <button type="button" onClick={() => industryCode ? setCompetition(true) : setIndustryError("경쟁분석을 보려면 업종을 먼저 선택해 주세요.")} className="group px-2 py-1 text-left"><span className="text-xs font-bold text-muted-foreground">경쟁 구조</span><span className="mt-1 flex items-center gap-1 text-sm font-black group-hover:text-primary">상세 분석 <ArrowUpRight className="h-3.5 w-3.5" /></span></button>
                    <Link href={`/ai?areaCode=${encodeURIComponent(areaCode)}&area=${encodeURIComponent(areaCode)}${industryCode ? `&industryCode=${encodeURIComponent(industryCode)}&industry=${encodeURIComponent(industryCode)}` : ""}`} className="group px-4 py-1"><span className="text-xs font-bold text-muted-foreground">의사결정 문서</span><span className="mt-1 flex items-center gap-1 text-sm font-black group-hover:text-primary">AI리포트 <ArrowUpRight className="h-3.5 w-3.5" /></span></Link>
                  </div>
                </div>
              )}

              {activeTab === "industry" && <IndustryComposition rows={industryRows} max={maxIndustry} />}
              {activeTab === "trend" && <SalesTrend rows={salesTrend} max={maxSales} />}
              {activeTab === "comments" && <CommentSection key={`${areaData.area_code}:${areaData.industry_analysis?.industry_code || "area"}`} areaCode={areaData.area_code} industryCode={areaData.industry_analysis?.industry_code || null} industryName={areaData.industry_analysis?.industry_name || null} />}
            </>
          )}
        </aside>
      </div>

      <CompetitionDrawer
        open={competitionOpen && Boolean(areaCode && industryCode)}
        expanded={competitionExpanded}
        areaCode={areaCode}
        areaName={areaData?.area_name || areaCode}
        industryCode={industryCode}
        industryName={areaData?.industry_analysis?.industry_name || selectedIndustry?.industry_name || industryCode}
        onClose={() => setCompetition(false)}
        onExpandedChange={(expanded) => setCompetition(true, expanded)}
      />
      <LoginModal isOpen={showLogin} onClose={() => setShowLogin(false)} />
    </div>
  );
}

function AreaPicker({ rankings, error, onSelect }: { rankings: RankingData[]; error: string; onSelect: (code: string) => void }) {
  return (
    <div className="flex min-h-[480px] flex-col">
      <header className="border-b p-5"><p className="text-xs font-black text-primary">TRADE AREA</p><h1 className="mt-1 text-2xl font-black">분석할 상권 선택</h1><p className="mt-2 text-sm leading-6 text-muted-foreground">상단 검색이나 아래 목록에서 공식 상권을 선택하세요.</p></header>
      {error && <p className="m-4 rounded-xl bg-destructive/10 p-3 text-sm font-semibold text-destructive" role="alert">{error}</p>}
      <div className="p-3">
        {rankings.slice(0, 10).map((area, index) => (
          <button key={area.area_code} type="button" onClick={() => onSelect(area.area_code)} className="group flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left hover:bg-accent">
            <span className="grid h-8 w-8 place-items-center rounded-lg bg-muted text-xs font-black text-muted-foreground">{index + 1}</span>
            <span className="min-w-0 flex-1 truncate text-sm font-bold">{area.area_name}</span>
            <span className="rounded-full bg-primary/10 px-2 py-1 text-xs font-black text-primary">{displayGradeOrPending(area.display_grade, area.grade)}</span>
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          </button>
        ))}
      </div>
    </div>
  );
}

function Metric({ icon: Icon, label, value, unit, divided, top }: { icon: typeof Users; label: string; value: string; unit: string; divided?: boolean; top?: boolean }) {
  return <div className={`p-4 ${divided ? "border-l" : ""} ${top ? "border-t" : ""}`}><div className="flex items-center gap-2 text-muted-foreground"><Icon className="h-3.5 w-3.5" /><dt className="text-[11px] font-semibold">{label}</dt></div><dd className="mt-2 min-w-0 truncate text-lg font-black">{value} {unit && <span className="text-[11px] font-semibold text-muted-foreground">{unit}</span>}</dd></div>;
}

function DemandRow({ label, value, unit, note }: { label: string; value: number | null; unit: string; note?: string }) {
  return <div className="flex items-center justify-between gap-4 py-3 text-xs" title={note}><dt className="font-semibold text-muted-foreground">{label}</dt><dd className="text-right font-black">{value == null ? "자료 없음" : `${value.toLocaleString()}${unit}`}</dd></div>;
}

function CostReferences({ rent, vacancy, saleProxy }: { rent?: AreaRoneCostReference; vacancy?: AreaRoneCostReference; saleProxy?: AreaSalePriceProxy }) {
  return (
    <section className="border-b p-5">
      <h2 className="text-sm font-black">비용 참고 데이터</h2>
      <dl className="mt-3 space-y-2 text-xs">
        <div className="flex justify-between gap-4"><dt className="text-muted-foreground">R-ONE 공실률</dt><dd className="text-right font-bold">{vacancy?.metric_value == null ? "자료 없음" : `${Number(vacancy.metric_value).toLocaleString()}${vacancy.unit || "%"}`}</dd></div>
        <div className="flex justify-between gap-4"><dt className="text-muted-foreground">거래가격 프록시</dt><dd className="text-right font-bold">{saleProxy?.sale_price_proxy_manwon_per_m2 == null ? "자료 없음" : `${Number(saleProxy.sale_price_proxy_manwon_per_m2).toLocaleString()}만원/㎡`}</dd></div>
      </dl>
      <div className="mt-3 rounded-xl bg-muted px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        {rent ? `${rent.provider || "R-ONE"} · ${rent.period} · ${rent.source_region_name || "서울 기준"} · ${rent.property_type || "상가"} · ${rent.mapping_scope === "rone_level3_name_match_candidate" ? "지역명 후보 매핑" : "서울 기준 참고값"}` : "임대료 참고 자료가 연결된 경우에만 표시합니다."}
        <br />거래가격은 {saleProxy?.provider || "RTMS"} {saleProxy?.period || "기준 기간 없음"} · {saleProxy?.grain || "공간 단위 미상"} 프록시입니다.
      </div>
    </section>
  );
}

function IndustryComposition({ rows, max }: { rows: Array<DistrictStoreCount & { name: string }>; max: number }) {
  return <section className="p-5"><div className="flex items-center justify-between"><h2 className="text-sm font-black">점포 수 상위 업종</h2><span className="text-xs text-muted-foreground">{periodLabel(latestPeriod(rows))}</span></div><div className="mt-4 space-y-4">{rows.map((row) => <div key={row.industry_code}><div className="mb-1.5 flex justify-between gap-3 text-xs"><span className="truncate font-semibold">{row.name}</span><span className="font-black">{row.store_count.toLocaleString()}개</span></div><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${row.store_count / max * 100}%` }} /></div></div>)}{!rows.length && <p className="py-8 text-center text-sm text-muted-foreground">업종 데이터가 없습니다.</p>}</div></section>;
}

function SalesTrend({ rows, max }: { rows: Array<{ period: string; value: number }>; max: number }) {
  return <section className="p-5"><h2 className="text-sm font-black">분기별 매출 추이</h2><div className="mt-4 space-y-4">{rows.map((row) => <div key={row.period} className="grid grid-cols-[72px_1fr_72px] items-center gap-3 text-xs"><span className="font-semibold text-muted-foreground">{periodLabel(row.period).replace("년 ", ".Q").replace("분기", "")}</span><span className="h-2 overflow-hidden rounded-full bg-muted"><span className="block h-full rounded-full bg-[#2563eb]" style={{ width: `${row.value / max * 100}%` }} /></span><span className="text-right font-black">{compactNumber(row.value)}</span></div>)}{!rows.length && <p className="py-8 text-center text-sm text-muted-foreground">매출 추이 데이터가 없습니다.</p>}</div></section>;
}

function isDerivedSpatialScore(metric: SpatialMetric) {
  return metric.unit === "점" || /(?:^|_)score$/.test(metric.key) || /점수|등급/.test(metric.label);
}

function methodClass(method: SpatialMetric["method"]) {
  if (method === "direct_aggregation") return "bg-[#dff4ee] text-[#0f6b58]";
  if (method === "official_area_value") return "bg-[#dcefea] text-[#0f6b58]";
  if (method === "area_ratio_estimate") return "bg-[#fff1d6] text-[#995600]";
  return "bg-muted text-muted-foreground";
}

function SpatialZoneInspector({ analysis, rankings, selectedArea, onClose }: { analysis: SpatialZoneAnalysis; rankings: RankingData[]; selectedArea: AreaData | null; onClose: () => void }) {
  const officialCode = analysis.zone_mode === "official" && analysis.intersected_areas.length === 1 ? analysis.intersected_areas[0].area_code : null;
  const official = officialCode ? (selectedArea?.area_code === officialCode ? selectedArea : rankings.find((item) => item.area_code === officialCode)) : null;
  const officialGrade = displayGrade(official?.display_grade, official?.grade);
  const maxCategory = Math.max(...analysis.top_store_categories.map((item) => item.count), 1);
  const officialMode = analysis.zone_mode === "official";
  return (
    <div className="min-h-full">
      <header className="border-b p-5">
        <div className="flex items-start justify-between gap-3"><div className="flex items-start gap-3"><span className={`grid h-10 w-10 place-items-center rounded-xl ${officialMode ? "bg-accent text-primary" : "bg-orange-50 text-orange-700"}`}><Layers3 className="h-5 w-5" /></span><span><p className="text-xs font-black text-primary">{officialMode ? "OFFICIAL AREA" : "CUSTOM ZONE"}</p><h1 className="mt-1 text-xl font-black">{officialMode ? "공식 상권 분석 영역" : "직접 그린 분석 영역"}</h1></span></div><button type="button" onClick={onClose} className="rounded-lg border px-3 py-2 text-xs font-bold hover:bg-muted">기본 화면</button></div>
        <dl className="mt-4 grid grid-cols-2 divide-x rounded-xl bg-[#18211f] py-3 text-white"><div className="px-4"><dt className="flex items-center gap-1.5 text-[11px] text-[#b8c7c2]"><Ruler className="h-3.5 w-3.5" /> 면적</dt><dd className="mt-1 text-lg font-black">{analysis.area_m2 >= 1_000_000 ? `${(analysis.area_m2 / 1_000_000).toFixed(2)}㎢` : `${Math.round(analysis.area_m2).toLocaleString()}㎡`}</dd></div><div className="px-4"><dt className="flex items-center gap-1.5 text-[11px] text-[#b8c7c2]"><MapPin className="h-3.5 w-3.5" /> 공식 경계 포함</dt><dd className="mt-1 text-lg font-black">{analysis.coverage.official_boundary_coverage_pct.toFixed(0)}%</dd></div></dl>
      </header>
      <section className="border-b"><div className="flex items-center justify-between px-5 pb-2 pt-5"><h2 className="text-sm font-black">영역 지표</h2><span className="text-[10px] text-muted-foreground">계산 방식 표시</span></div><dl>{analysis.metrics.map((metric) => { const derived = isDerivedSpatialScore(metric); const displayValue = derived ? (officialGrade || "등급 보류") : metric.value == null ? "자료 없음" : Math.round(metric.value).toLocaleString(); return <div key={metric.key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-t px-5 py-3"><div className="min-w-0"><dt className="text-xs font-bold">{derived ? metric.label.replace(/점수/g, "등급") : metric.label}</dt><p className="mt-1 truncate text-[10px] text-muted-foreground" title={metric.source}>{metric.source}</p></div><dd className="text-right"><strong className="block text-sm font-black">{displayValue}{!derived && metric.value != null && <span className="text-[10px] text-muted-foreground"> {metric.unit}</span>}</strong><span className={`mt-1 inline-flex rounded px-1.5 py-0.5 text-[9px] font-bold ${methodClass(metric.method)}`}>{metric.method_label}</span></dd></div>; })}</dl></section>
      {analysis.top_store_categories.length > 0 && <section className="border-b p-5"><div className="flex items-center gap-2"><Store className="h-4 w-4 text-primary" /><h2 className="text-sm font-black">영역 내 점포 구성</h2></div><div className="mt-4 space-y-3">{analysis.top_store_categories.map((item) => <div key={`${item.code}-${item.name}`}><div className="mb-1.5 flex justify-between gap-3 text-xs"><span className="truncate font-semibold">{item.name}</span><span className="font-black">{item.count.toLocaleString()}개</span></div><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(item.count / maxCategory * 100, 3)}%` }} /></div></div>)}</div></section>}
      <section className="p-5"><div className="flex items-center gap-2"><TrainFront className="h-4 w-4 text-primary" /><h2 className="text-sm font-black">{officialMode ? "선택 공식 상권" : "겹치는 공식 상권"}</h2></div><div className="mt-3 divide-y">{analysis.intersected_areas.slice(0, 6).map((area) => <div key={area.area_code} className="flex justify-between gap-3 py-2.5 text-xs"><span className="truncate font-semibold">{area.area_name}</span><span className="shrink-0 font-black text-primary">영역 내 {area.zone_share_pct.toFixed(1)}%</span></div>)}{!analysis.intersected_areas.length && <p className="py-5 text-center text-xs text-muted-foreground">겹치는 공식 상권이 없습니다.</p>}</div></section>
    </div>
  );
}

export default function TradePage() {
  return <Suspense fallback={<div className="h-full animate-pulse bg-muted" />}><TradeContent /></Suspense>;
}
