"use client";

import { LoginModal } from "@/components/LoginModal";
import {
  TwoTierNewsEvidence,
  type NewsEvidenceItem,
} from "@/components/TwoTierNewsEvidence";
import { useReportJob } from "@/components/report-job-context";
import { apiUrl, DEMO_MODE, fetchAuth, logProductEvent } from "@/lib/api";
import { displayGrade, displayGradeOrPending, userFacingMetricDisplay } from "@/lib/score-grade";
import { AreaData } from "@/types/models";
import Link from "next/link";
import { Suspense, useEffect, useMemo, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronLeft, Database, Download, Save } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

type Favorite = {
  area_code: string;
  area_name: string;
};

type IndustryOption = {
  industry_code: string;
  industry_name: string;
  display_label?: string;
  selection_path?: string;
  major?: string | null;
  middle?: string | null;
  detail?: string | null;
};

type AxisInterpretation = {
    axis: string;
  score?: number | null;
  grade?: string | null;
  display_grade?: string | null;
  interpretation_level?: string;
  meaning: string;
  evidence: string;
  risk: string;
  action: string;
};

type SourceCitation = {
  title?: string;
  source_path?: string;
  provider?: string;
  dataset_name?: string;
  source_url?: string;
  period?: string;
  granularity?: string;
  theme?: string;
  used_for?: string;
  caveat?: string;
};

type NewsEvidence = NewsEvidenceItem & {
  source_grade?: string;
  region_hints?: string;
  industry_hints?: string;
  signal_types?: string;
  signal_labels?: string;
  content_sha256?: string;
  score_role?: string;
  location_scope?: string;
  matched_location?: string;
  industry_match?: boolean;
  matched_industry_terms?: string[];
  budget_relevance?: "direct" | "context" | "none" | "not_provided";
  matched_budget_terms?: string[];
  decision_area?: string;
  decision_area_label?: string;
  decision_role?: "opportunity" | "risk" | "watch";
  decision_summary?: string;
  citation_marker?: string;
  age_days?: number;
  relevance_score?: number;
};

type RadarMetric = {
  subject: string;
  scores: Record<string, number | null>;
};

type HeaderBlock = {
  judgement_line?: string;
  score_label?: string;
  score?: string;
  grade?: string;
  display_grade?: string;
  percentile?: string;
  key_metrics?: Array<{ label?: string; display?: string; note?: string }>;
};

type MetricValue = {
  label?: string;
  display?: string;
  raw?: number | string | null;
  unit?: string;
  note?: string;
};

type SalesTrendPoint = {
  timestamp?: string;
  sales_amount?: MetricValue;
};

type AreaTopIndustry = {
  rank?: number;
  industry_name?: string;
  sales_amount?: MetricValue;
};

type AlternativeFact = {
  area_name?: string;
  grade?: string | null;
  display_grade?: string | null;
  current_location_score?: MetricValue;
  cost_risk_score?: MetricValue;
  major_differential_axis?: string;
};

type ReportAlternative = {
  area_name?: string;
  score?: string | number | null;
  grade?: string | null;
  display_grade?: string | null;
  judgement?: string;
};

type IndicatorPack = {
  facts_pack?: {
    target?: {
      area_name?: string;
      industry_name?: string;
    };
    score_block?: {
      current_location_score?: MetricValue;
      axis_scores?: {
        sales?: MetricValue;
        competition?: MetricValue;
        demand?: MetricValue;
        accessibility?: MetricValue;
      };
      supporting_signals?: {
        cost_risk_score?: MetricValue;
        data_reliability_score?: MetricValue;
        growth_potential_score?: MetricValue;
      };
    };
    sales_block?: {
      sales_trend?: SalesTrendPoint[];
      area_top_industries?: AreaTopIndustry[];
    };
    demand_block?: {
      resident_population?: MetricValue;
      worker_population?: MetricValue;
      floating_population?: MetricValue;
      floating_population_daily_average?: MetricValue;
    };
    cost_block?: {
      cost_risk_score?: MetricValue;
    };
    alternatives?: AlternativeFact[];
    data_period_text?: string;
  };
};

type ChartDatum = {
  label: string;
  value: number | null;
  group?: string;
};

type BaseInterpretiveFields = {
  narrative_title?: string;
  executive_interpretation?: string;
  score_interpretation?: string;
  trend_analysis?: string;
  user_fit?: string;
  thesis?: string[];
  header_block?: HeaderBlock;
  radar_metrics?: RadarMetric[];
  evidence_basis?: string[];
  source_citations?: SourceCitation[];
  methodology_notes?: string[];
  action_plan?: string[];
  onsite_checklist?: string[];
  limitations?: string[];
  markdown_body?: string;
  ai_model?: string;
  ai_generated?: boolean;
  generation_mode?: "llm" | "partial_fallback" | "deterministic";
  original_validation_issues?: string[];
  validation_issues?: string[];
  quality_warnings?: string[];
  quality_status?: string;
  fallback_fields?: string[];
  indicator_pack?: IndicatorPack;
  news_evidence?: NewsEvidence[];
  visualization_data?: ChartDatum[];
};

type SingleReport = BaseInterpretiveFields & {
  type: "single";
  area_name?: string;
  summary: string;
  strengths: string[];
  weaknesses: string[];
  recommended_businesses: string[];
  risk_factors: string[];
  industry_code?: string;
  industry_name?: string;
  score_source?: string;
  axis_interpretations?: AxisInterpretation[];
  alternatives?: ReportAlternative[];
};

type ComparisonReport = BaseInterpretiveFields & {
  type: "comparison";
  summary: string;
  top_recommendation_name: string;
  top_recommendation_reason: string;
  comparison_matrix?: Array<{
    area_name: string;
    interpretation_level?: string;
    strong_axis?: string;
    watch_axis?: string;
    interpretation?: string;
  }>;
};

type ReportData = SingleReport | ComparisonReport;

function parseBudgetToManwon(value: string): number | null {
  const text = value.replace(/[,\s]/g, "");
  if (!text) return null;
  let total = 0;
  const eok = text.match(/(\d+(?:\.\d+)?)억/);
  const manwon = text.match(/(\d+(?:\.\d+)?)(?:만|만원)/);
  if (eok) total += Number(eok[1]) * 10000;
  if (manwon) total += Number(manwon[1]);
  if (total > 0) return Math.round(total);
  const numeric = text.match(/\d+(?:\.\d+)?/);
  return numeric ? Math.round(Number(numeric[0])) : null;
}

function unique(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.filter(Boolean) as string[]));
}

function generationModeLabel(mode?: BaseInterpretiveFields["generation_mode"]) {
  const labels: Record<string, string> = {
    llm: "AI 해석",
    partial_fallback: "AI 해석 · 일부 규칙 보정",
    deterministic: "규칙 기반 결과",
  };
  return (mode && labels[mode]) || "생성 방식 기록 없음";
}

function publicNarrativeText(text?: string): string {
  return String(text || "")
    .replace(/\s*\[CHART:C[1-5]\]\s*/g, " ")
    .replace(/\s*\[NEWS:\d+\]\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function industryPath(option: IndustryOption) {
  return [option.major, option.middle, option.detail, option.industry_name].filter(Boolean).join(" > ");
}

type MetricChartRow = {
  label: string;
  value: number;
  display: string;
  group: string;
  fill: string;
};

type GradeRow = {
  label: string;
  grade: string;
};

const SALES_COLOR = "#0d9488";
const DEMAND_COLOR = "#059669";

function isPresent<T>(value: T | null): value is T {
  return value !== null;
}

function metricNumber(metric?: MetricValue | null): number | null {
  if (!metric) return null;
  const raw = metric.raw;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw === "string") {
    const parsed = Number(raw.replace(/,/g, ""));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function roundChartValue(value: number) {
  return Math.round(value * 10) / 10;
}

function metricDisplay(metric: MetricValue | undefined | null, fallback: string) {
  return metric?.display || fallback;
}

function compactLabel(value: string | undefined, max = 12) {
  const text = (value || "-").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function metricChartRow(
  label: string,
  metric: MetricValue | undefined | null,
  group: string,
  fill: string,
  divisor = 1
): MetricChartRow | null {
  const raw = metricNumber(metric);
  if (raw === null) return null;
  const value = roundChartValue(raw / divisor);
  return {
    label,
    value,
    display: metricDisplay(metric, `${value}`),
    group,
    fill,
  };
}

function buildReportVisuals(reportData: ReportData) {
  const facts = reportData.indicator_pack?.facts_pack;
  const scoreBlock = facts?.score_block;
  const axisScores = scoreBlock?.axis_scores;
  const salesBlock = facts?.sales_block;
  const demandBlock = facts?.demand_block;

  const axisRows: GradeRow[] = [
    ["시장성", axisScores?.sales],
    ["경쟁 구조", axisScores?.competition],
    ["수요 기반", axisScores?.demand],
    ["접근·유입", axisScores?.accessibility],
  ].flatMap(([label, metric]) => {
    const grade = displayGrade((metric as MetricValue | undefined)?.display);
    return grade ? [{ label: String(label), grade }] : [];
  });

  const salesTrend = (salesBlock?.sales_trend || [])
    .map((item) => {
      const raw = metricNumber(item.sales_amount);
      if (raw === null) return null;
      const value = roundChartValue(raw / 100_000_000);
      return {
        label: String(item.timestamp || "-"),
        value,
        display: metricDisplay(item.sales_amount, `${value}억원`),
      };
    })
    .filter(isPresent)
    .sort((a, b) => a.label.localeCompare(b.label));

  const topIndustries = (salesBlock?.area_top_industries || [])
    .map((item) => {
      const raw = metricNumber(item.sales_amount);
      if (raw === null) return null;
      const value = roundChartValue(raw / 100_000_000);
      return {
        label: compactLabel(item.industry_name, 14),
        value,
        display: metricDisplay(item.sales_amount, `${value}억원`),
        group: "매출",
        fill: "#0f766e",
      };
    })
    .filter(isPresent);

  const demandMix = [
    metricChartRow("상주", demandBlock?.resident_population, "수요", DEMAND_COLOR, 10_000),
    metricChartRow("직장", demandBlock?.worker_population, "수요", DEMAND_COLOR, 10_000),
    metricChartRow("일평균 유동", demandBlock?.floating_population_daily_average, "수요", DEMAND_COLOR, 10_000),
  ].filter(isPresent);

  const targetName = facts?.target?.area_name || (reportData.type === "single" ? reportData.area_name : "대상") || "대상";
  const targetGrade = displayGrade(scoreBlock?.current_location_score?.display);
  const alternatives: GradeRow[] = [];
  if (targetGrade) {
    alternatives.push({
      label: compactLabel(`${targetName} (대상)`, 14),
      grade: targetGrade,
    });
  }
  for (const item of (facts?.alternatives || []).slice(0, 5)) {
    const grade = displayGrade(item.display_grade, item.grade) || displayGrade(item.current_location_score?.display);
    if (!grade) continue;
    alternatives.push({
      label: compactLabel(item.area_name, 14),
      grade,
    });
  }

  return {
    axisRows,
    salesTrend,
    topIndustries,
    demandMix,
    alternatives,
  };
}

function AIInsightsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const areaCode = searchParams.get("areaCode") || searchParams.get("area");
  const initialIndustry = searchParams.get("industryCode") || searchParams.get("industry");
  const requestedReportJobId = searchParams.get("reportJob");
  const isSingleMode = Boolean(areaCode);
  const {
    job: reportJob,
    isHydrated: isReportJobHydrated,
    resumeJob,
    startJob,
  } = useReportJob();

  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [areaData, setAreaData] = useState<AreaData | null>(null);
  const [industryQuery, setIndustryQuery] = useState("");
  const [budgetText, setBudgetText] = useState("");
  const [selectedIndustry, setSelectedIndustry] = useState<IndustryOption | null>(null);
  const [industryOptions, setIndustryOptions] = useState<IndustryOption[]>([]);
  const [activePicker, setActivePicker] = useState<"industry" | null>(null);
  const [selectedMajor, setSelectedMajor] = useState("");
  const [selectedMiddle, setSelectedMiddle] = useState("");
  const [loading, setLoading] = useState(true);
  const [isReportSubmitting, setIsReportSubmitting] = useState(false);
  const [isReportSaving, setIsReportSaving] = useState(false);
  const [isExporting, setIsExporting] = useState(false);
  const [showLoginModal, setShowLoginModal] = useState(false);
  const [reportData, setReportData] = useState<ReportData | null>(null);
  const [isReportFormOpen, setIsReportFormOpen] = useState(true);
  const [savedReportId, setSavedReportId] = useState<number | null>(null);
  const [errorText, setErrorText] = useState("");
  const isReportGenerating = isReportSubmitting
    || reportJob?.status === "submitting"
    || reportJob?.status === "queued"
    || reportJob?.status === "running";

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setLoading(true);
      setReportData(null);
      setIsReportFormOpen(true);
      setErrorText("");

      if (areaCode) {
        fetchAuth(apiUrl(`/areas/${areaCode}`))
          .then((res) => {
            if (!res.ok) throw new Error("상권 정보를 불러오지 못했습니다.");
          return res.json();
        })
        .then((data: AreaData) => {
          if (!cancelled) {
            setAreaData(data);
          }
        })
          .catch((err) => {
            if (!cancelled) setErrorText(err.message);
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
        return;
      }

      setAreaData(null);
      fetchAuth(apiUrl("/favorites"))
        .then((res) => (res.ok ? res.json() : []))
        .then((data: Favorite[]) => {
          if (!cancelled) setFavorites(Array.isArray(data) ? data : []);
        })
        .catch((err) => {
          if (!cancelled) setErrorText(err.message);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [areaCode, requestedReportJobId]);

  useEffect(() => {
    if (!isReportJobHydrated || !requestedReportJobId) return;
    resumeJob(requestedReportJobId, {
      areaCode: areaCode || undefined,
    });
  }, [areaCode, isReportJobHydrated, requestedReportJobId, resumeJob]);

  useEffect(() => {
    if (!reportJob?.id || loading) return;
    const explicitlyRequested = requestedReportJobId === reportJob.id;
    const matchesCurrentPage = reportJob.reportType === "single"
      ? Boolean(areaCode && reportJob.context.areaCode === areaCode)
      : !areaCode;
    if (!explicitlyRequested && !matchesCurrentPage) return;
    if (
      reportJob.status !== "failed"
      && (reportJob.status !== "completed" || !reportJob.result)
    ) return;

    const timer = window.setTimeout(() => {
      if (reportJob.status === "failed") {
        setErrorText(reportJob.errorMessage || "AI 리포트 생성에 실패했습니다.");
        return;
      }

      if (reportJob.reportType === "single") {
        const data = reportJob.result as Omit<SingleReport, "type">;
        const explicitModeIsValid = ["llm", "partial_fallback", "deterministic"].includes(
          data.generation_mode || "",
        ) && data.quality_status === "pass";
        if (!explicitModeIsValid && data.ai_generated !== true) {
          setErrorText("AI 리포트 생성 결과를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");
          return;
        }
        setReportData({
          type: "single",
          area_name: reportJob.context.areaName || areaData?.area_name,
          ...data,
        });
      } else {
        const data = reportJob.result as Omit<ComparisonReport, "type">;
        if (data.ai_generated !== true) {
          setErrorText("AI 비교 리포트 생성 결과를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.");
          return;
        }
        setReportData({ type: "comparison", ...data });
      }

      setSavedReportId(null);
      setIsReportFormOpen(false);
      setErrorText("");
    }, 0);
    return () => window.clearTimeout(timer);
  }, [
    areaCode,
    areaData?.area_name,
    loading,
    reportJob,
    requestedReportJobId,
  ]);

  useEffect(() => {
    if (!isSingleMode || !initialIndustry) return;
    let cancelled = false;
    const query = initialIndustry.trim();

    fetchAuth(apiUrl(`/chatbot/industry-options?q=${encodeURIComponent(query)}&limit=80`))
      .then((res) => (res.ok ? res.json() : []))
      .then((items: IndustryOption[]) => {
        if (cancelled || !Array.isArray(items)) return;
        const normalizedQuery = query.replace(/\s+/g, "").toLocaleLowerCase("ko-KR");
        const match = items.find((item) => (
          item.industry_code.toLocaleLowerCase("ko-KR") === query.toLocaleLowerCase("ko-KR")
          || item.industry_name.replace(/\s+/g, "").toLocaleLowerCase("ko-KR") === normalizedQuery
        ));
        if (!match) return;
        setSelectedIndustry(match);
        setIndustryQuery(match.industry_name);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [initialIndustry, isSingleMode]);

  useEffect(() => {
    if (!isSingleMode || activePicker !== "industry") return;
    const timer = window.setTimeout(async () => {
      try {
        const query = industryQuery.trim();
        const res = await fetchAuth(apiUrl(`/chatbot/industry-options?q=${encodeURIComponent(query)}&limit=${query ? 80 : 500}`));
        if (res.ok) setIndustryOptions(await res.json());
      } catch {
        setIndustryOptions([]);
      }
    }, 160);
    return () => window.clearTimeout(timer);
  }, [activePicker, industryQuery, isSingleMode]);

  const hasAuthToken = () => {
    if (typeof window === "undefined") return false;
    return Boolean(localStorage.getItem("token"));
  };

  const generateSingleReport = async () => {
    if (!areaCode) {
      setErrorText("선택된 상권 정보를 확인하지 못했습니다.");
      return;
    }
    setIsReportSubmitting(true);
    setErrorText("");
    void logProductEvent("report_requested", { area_code: areaCode }).catch(() => undefined);
    try {
      const businessType = selectedIndustry?.industry_name || industryQuery.trim() || null;
      const budgetManwon = parseBudgetToManwon(budgetText);
      const jobId = await startJob(
        "single",
        {
          area_code: areaCode,
          business_type: businessType,
          budget: budgetManwon,
        },
        {
          areaCode,
          areaName: areaData?.area_name,
          industryName: businessType || undefined,
          budgetManwon,
          reportLabel: `${areaData?.area_name || areaCode} · ${businessType || "업종 전체"}`,
        },
      );
      const nextParams = new URLSearchParams(searchParams.toString());
      nextParams.set("areaCode", areaCode);
      nextParams.set("reportJob", jobId);
      router.replace(`/ai?${nextParams.toString()}`, { scroll: false });
      setReportData(null);
      setIsReportFormOpen(true);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "단일 리포트 생성에 실패했습니다.");
      void logProductEvent("report_failed", { area_code: areaCode }).catch(() => undefined);
    } finally {
      setIsReportSubmitting(false);
    }
  };

  const generateComparisonReport = async () => {
    const eventAreaCode = favorites[0]?.area_code;
    setIsReportSubmitting(true);
    setErrorText("");
    if (eventAreaCode) {
      void logProductEvent("report_requested", { area_code: eventAreaCode }).catch(() => undefined);
    }
    try {
      const jobId = await startJob(
        "comparison",
        { area_codes: favorites.map((fav) => fav.area_code) },
        {
          eventAreaCode,
          comparisonCount: favorites.length,
          reportLabel: `즐겨찾기 ${favorites.length}개 상권 비교`,
        },
      );
      const nextParams = new URLSearchParams(searchParams.toString());
      nextParams.delete("areaCode");
      nextParams.delete("area");
      nextParams.set("reportJob", jobId);
      router.replace(`/ai?${nextParams.toString()}`, { scroll: false });
      setReportData(null);
      setIsReportFormOpen(true);
    } catch (err) {
      setErrorText(err instanceof Error ? err.message : "비교 리포트 생성에 실패했습니다.");
      if (eventAreaCode) {
        void logProductEvent("report_failed", { area_code: eventAreaCode }).catch(() => undefined);
      }
    } finally {
      setIsReportSubmitting(false);
    }
  };

  const handleSaveReport = async () => {
    if (!reportData) return;
    if (!hasAuthToken() || (typeof window !== "undefined" && localStorage.getItem("guest_mode") === "true")) {
      setShowLoginModal(true);
      return;
    }
    setIsReportSaving(true);
    try {
      const res = await fetchAuth(apiUrl("/reports/save"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_data: reportData }),
      });
      if (!res.ok) throw new Error("저장 실패");
      const saved = await res.json();
      if (saved?.id) setSavedReportId(saved.id);
      alert("리포트를 저장했습니다.");
    } catch {
      alert("리포트 저장에 실패했습니다.");
    } finally {
      setIsReportSaving(false);
    }
  };

  const downloadReport = async () => {
    if (!reportData) return;
    setIsExporting(true);
    try {
      const title = reportData.narrative_title || (reportData.type === "single" ? reportData.area_name : "상권 비교 리포트") || "AI 리포트";
      // 저장 여부와 관계없이 차트와 출처가 포함된 PDF 산출물을 받는다.
      const res = savedReportId
        ? await fetchAuth(apiUrl(`/reports/${savedReportId}/download?format=pdf`))
        : await fetchAuth(apiUrl("/reports/export/pdf"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ report_data: reportData, filename: title }),
          });
      if (!res.ok) throw new Error("PDF 다운로드 실패");
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${title.replace(/[\\/:*?"<>|]+/g, "_")}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      alert(err instanceof Error ? err.message : "다운로드에 실패했습니다.");
    } finally {
      setIsExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center text-center text-muted-foreground">
        <div className="mb-4 h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        <p className="text-lg font-medium">AI 리포트 데이터를 불러오는 중입니다.</p>
      </div>
    );
  }

  const parsedBudget = parseBudgetToManwon(budgetText);
  const resultContext = reportJob?.status === "completed" && reportJob.result
    ? reportJob.context
    : {};
  const displayedBudget = resultContext.budgetManwon !== undefined
    ? resultContext.budgetManwon
    : parsedBudget;
  const displayedComparisonCount = resultContext.comparisonCount ?? favorites.length;

  return (
    <div className="mx-auto flex max-w-[1280px] flex-col gap-6 pb-16">
      <div className="flex flex-col items-start justify-between gap-5 rounded-2xl border bg-card p-5 surface-shadow sm:flex-row sm:items-end sm:p-7">
        <div>
          <h1 className="text-3xl font-black">AI 상세 리포트</h1>
          <p className="mt-2 text-sm text-muted-foreground">공공 상권 데이터와 동일 업종 비교를 바탕으로 출점 조건과 주요 변수를 해석합니다.</p>
        </div>
        <Link href={isSingleMode ? `/trade?area=${areaCode}` : "/trade"} className="shrink-0 whitespace-nowrap rounded-lg border bg-background px-4 py-2.5 text-sm font-bold transition-colors hover:bg-accent">
          상권 분석으로 이동
        </Link>
      </div>

      {errorText && <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">{errorText}</div>}

      {reportData && !isReportFormOpen ? (
        <div className="flex flex-col gap-3 rounded-xl border bg-card px-4 py-3.5 shadow-sm sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-bold text-muted-foreground">분석 조건</p>
            <p className="mt-1 truncate text-sm font-black">
              {reportData.type === "single"
                ? `${reportData.area_name || areaData?.area_name || "선택 상권"} · ${reportData.industry_name || resultContext.industryName || selectedIndustry?.industry_name || industryQuery || "상권 맥락"}${displayedBudget !== null ? ` · 예산 ${displayedBudget.toLocaleString()}만원` : ""}`
                : `즐겨찾기 ${displayedComparisonCount}개 상권 비교`}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setIsReportFormOpen(true)}
            className="shrink-0 rounded-lg border px-3.5 py-2.5 text-sm font-bold transition-colors hover:bg-accent"
          >
            조건 변경
          </button>
        </div>
      ) : isSingleMode ? (
        <article className="rounded-2xl border bg-card p-5 surface-shadow sm:p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-xs font-bold text-primary">단일 상권 분석</p>
              <h2 className="mt-1 text-xl font-bold">{areaData?.area_name || areaCode}</h2>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
                업종명을 입력하면 상권·업종 조합을 평가합니다. 비워두면 수요·접근성 상권 맥락만 봅니다.
              </p>
            </div>
            <div className="relative flex w-full flex-col gap-2.5 md:w-[460px]">
              <div className="relative z-20">
                <ReportInput
                  label="업종"
                  value={industryQuery}
                  active={activePicker === "industry"}
                  onFocus={() => setActivePicker("industry")}
                  onChange={(value) => {
                    setIndustryQuery(value);
                    if (selectedIndustry && selectedIndustry.industry_name !== value) setSelectedIndustry(null);
                    if (value.trim()) {
                      setSelectedMajor("");
                      setSelectedMiddle("");
                    }
                  }}
                />
                {activePicker === "industry" && (
                  <div className="absolute right-0 top-[calc(100%+6px)] z-50 w-full md:w-[min(680px,calc(100vw-32px))]">
                    <IndustryOptionPanel
                      query={industryQuery}
                      options={industryOptions}
                      selectedMajor={selectedMajor}
                      selectedMiddle={selectedMiddle}
                      onMajor={setSelectedMajor}
                      onMiddle={setSelectedMiddle}
                      onSelect={(option) => {
                        setSelectedIndustry(option);
                        setIndustryQuery(option.industry_name);
                        setActivePicker(null);
                      }}
                    />
                  </div>
                )}
              </div>

              <ReportInput label="예산(만원)" value={budgetText} onChange={setBudgetText} />
              <button
                onClick={generateSingleReport}
                disabled={isReportGenerating || !areaCode}
                className="rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-primary-foreground transition-colors hover:bg-[#115e59] disabled:opacity-50"
              >
                {isReportGenerating ? "AI 해석 중..." : "AI 상세 리포트 생성"}
              </button>
            </div>
          </div>
        </article>
      ) : favorites.length < 2 ? (
        <div className="flex min-h-80 flex-col items-center justify-center rounded-2xl border-2 border-dashed bg-card p-6 text-center text-muted-foreground sm:min-h-96">
          <p className="mb-2 text-lg font-semibold text-foreground">비교할 즐겨찾기 상권이 부족합니다.</p>
          <p className="mb-6 max-w-md text-sm">비교 리포트는 최소 두 개 이상의 즐겨찾기 상권이 필요합니다.</p>
          <Link href="/trade" className="rounded-lg bg-primary px-6 py-2 font-medium text-primary-foreground hover:bg-primary/90">
            상권 고르기
          </Link>
        </div>
      ) : (
        <article className="rounded-2xl border bg-card p-5 surface-shadow sm:p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-xs font-bold text-primary">후보 상권 비교</p>
              <h2 className="mt-1 text-xl font-bold">즐겨찾기 {favorites.length}개 상권 비교</h2>
              <p className="mt-2 text-sm text-muted-foreground">후보별로 먼저 볼 축과 현장 대조 축을 정리합니다.</p>
            </div>
            <button
              onClick={generateComparisonReport}
              disabled={isReportGenerating}
              className="rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-primary-foreground transition-colors hover:bg-[#115e59] disabled:opacity-50"
            >
              {isReportGenerating ? "비교 해석 중..." : "비교 AI 리포트 생성"}
            </button>
          </div>
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {favorites.map((fav) => (
              <div key={fav.area_code} className="rounded-xl border bg-background p-4 text-sm font-semibold">
                {fav.area_name}
              </div>
            ))}
          </div>
        </article>
      )}

      {reportData && (
        <ReportDetail
          reportData={reportData}
          areaName={areaData?.area_name}
          isReportSaving={isReportSaving}
          isExporting={isExporting}
          onSave={handleSaveReport}
          onDownload={downloadReport}
        />
      )}

      <LoginModal isOpen={showLoginModal} onClose={() => setShowLoginModal(false)} />
    </div>
  );
}

function ReportInput({
  label,
  value,
  active,
  onFocus,
  onChange,
}: {
  label: string;
  value: string;
  active?: boolean;
  onFocus?: () => void;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-bold text-muted-foreground">{label}</span>
      <input
        value={value}
        onFocus={onFocus}
        onChange={(event) => onChange(event.target.value)}
        className={`h-11 w-full rounded-lg border bg-background px-3.5 text-sm outline-none transition-shadow focus:ring-2 focus:ring-primary ${active ? "border-primary" : ""}`}
      />
    </label>
  );
}

function IndustryOptionPanel({
  query,
  options,
  selectedMajor,
  selectedMiddle,
  onMajor,
  onMiddle,
  onSelect,
}: {
  query: string;
  options: IndustryOption[];
  selectedMajor: string;
  selectedMiddle: string;
  onMajor: (value: string) => void;
  onMiddle: (value: string) => void;
  onSelect: (option: IndustryOption) => void;
}) {
  const [mobileStage, setMobileStage] = useState<"major" | "middle" | "detail">("major");
  const isSearching = query.trim().length > 0;
  const majors = useMemo(() => unique(options.map((item) => item.major)), [options]);
  const activeMajor = selectedMajor || majors[0] || "";
  const middles = useMemo(
    () => unique(options.filter((item) => item.major === activeMajor).map((item) => item.middle)),
    [options, activeMajor]
  );
  const activeMiddle = selectedMiddle || middles[0] || "";
  const leaves = useMemo(
    () => options.filter((item) => item.major === activeMajor && item.middle === activeMiddle),
    [options, activeMajor, activeMiddle]
  );

  if (isSearching) {
    return (
      <div className="scrollbar-natural max-h-[380px] overflow-y-auto overscroll-contain rounded-xl border bg-background p-2 shadow-xl">
        {options.length === 0 ? (
          <div className="px-3 py-2 text-xs text-muted-foreground">검색 결과가 없습니다.</div>
        ) : (
          <div className="grid gap-1">
            {options.slice(0, 50).map((option) => (
              <button
                type="button"
                key={option.industry_code}
                onClick={() => onSelect(option)}
                className="rounded-md px-3 py-2 text-left text-xs hover:bg-accent"
              >
                <span className="block font-bold">{option.industry_name}</span>
                <span className="mt-0.5 block text-[11px] text-muted-foreground">{industryPath(option)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      <div className="h-[340px] max-h-[58vh] overflow-hidden rounded-xl border bg-background shadow-xl md:hidden">
        <div className="flex h-11 items-center gap-2 border-b px-2">
          {mobileStage !== "major" ? (
            <button
              type="button"
              title="이전 분류"
              aria-label="이전 분류"
              onClick={() => setMobileStage(mobileStage === "detail" ? "middle" : "major")}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md hover:bg-accent"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
          ) : (
            <span className="h-8 w-8 shrink-0" aria-hidden="true" />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-bold text-muted-foreground">
              {mobileStage === "major" ? "대분류" : mobileStage === "middle" ? "중분류" : "세부업종"}
            </p>
            <p className="truncate text-xs font-bold">
              {mobileStage === "major" ? "업종 분류" : mobileStage === "middle" ? activeMajor : `${activeMajor} · ${activeMiddle}`}
            </p>
          </div>
          <span className="pr-2 text-[10px] font-bold text-muted-foreground">
            {mobileStage === "major" ? "1 / 3" : mobileStage === "middle" ? "2 / 3" : "3 / 3"}
          </span>
        </div>

        <div className="scrollbar-natural h-[calc(100%-44px)] overflow-y-auto overscroll-contain p-2">
          <div className="grid gap-1">
            {mobileStage === "major" && majors.map((item) => (
              <button
                type="button"
                key={item}
                onClick={() => {
                  onMajor(item);
                  onMiddle("");
                  setMobileStage("middle");
                }}
                className={`rounded-md px-3 py-2.5 text-left text-sm font-semibold ${selectedMajor === item ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
              >
                {item}
              </button>
            ))}

            {mobileStage === "middle" && middles.map((item) => (
              <button
                type="button"
                key={item}
                onClick={() => {
                  onMiddle(item);
                  setMobileStage("detail");
                }}
                className={`rounded-md px-3 py-2.5 text-left text-sm font-semibold ${selectedMiddle === item ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
              >
                {item}
              </button>
            ))}

            {mobileStage === "detail" && leaves.map((option) => (
              <button
                type="button"
                key={option.industry_code}
                onClick={() => onSelect(option)}
                className="rounded-md px-3 py-2.5 text-left text-sm hover:bg-accent"
              >
                <span className="block font-bold">{option.detail || option.industry_name}</span>
                <span className="mt-0.5 block text-[11px] text-muted-foreground">{option.industry_name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="hidden h-[340px] max-h-[58vh] grid-cols-3 gap-2 overflow-hidden rounded-xl border bg-background p-2 shadow-xl md:grid">
        <PickerColumn
          items={majors}
          active={activeMajor}
          onClick={(value) => {
            onMajor(value);
            onMiddle("");
          }}
        />
        <PickerColumn items={middles} active={activeMiddle} onClick={onMiddle} />
        <div className="scrollbar-natural h-full min-h-0 overflow-y-auto overscroll-contain">
          <div className="grid gap-1">
            {leaves.map((option) => (
              <button
                type="button"
                key={option.industry_code}
                onClick={() => onSelect(option)}
                className="rounded-md px-2 py-2 text-left text-[11px] hover:bg-accent"
              >
                <span className="block font-bold">{option.detail || option.industry_name}</span>
                <span className="mt-0.5 block text-[10px] text-muted-foreground">{option.industry_name}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}

function PickerColumn({ items, active, onClick }: { items: string[]; active: string; onClick: (value: string) => void }) {
  return (
    <div className="scrollbar-natural h-full min-h-0 overflow-y-auto overscroll-contain">
      <div className="grid gap-1">
        {items.map((item) => (
          <button
            type="button"
            key={item}
            onClick={() => onClick(item)}
            className={`rounded-md px-2 py-2 text-left text-[11px] font-semibold ${active === item ? "bg-primary text-primary-foreground" : "hover:bg-accent"}`}
          >
            {item}
          </button>
        ))}
      </div>
    </div>
  );
}

function ReportDetail({ reportData, areaName, isReportSaving, isExporting, onSave, onDownload }: {
  reportData: ReportData;
  areaName?: string;
  isReportSaving: boolean;
  isExporting: boolean;
  onSave: () => void;
  onDownload: () => void;
}) {
  const title = useMemo(
    () => reportData.narrative_title || (reportData.type === "single" ? `${reportData.area_name || areaName || "선택 상권"} AI 해석 리포트` : "상권 비교 AI 해석 리포트"),
    [areaName, reportData]
  );
  const header = reportData.header_block;
  const dataPeriod = reportData.type === "single" ? reportData.indicator_pack?.facts_pack?.data_period_text : undefined;
  const newsItems = reportData.type === "single" ? reportData.news_evidence || [] : [];
  const decisionRisks = reportData.type === "single"
    ? unique((reportData.risk_factors || []).length > 0 ? reportData.risk_factors : (reportData.axis_interpretations || []).map((item) => item.risk)).slice(0, 3)
    : [];
  const reportAlternatives = reportData.type === "single" ? reportData.alternatives || [] : [];
  const generatedSteps = reportData.action_plan || [];
  const verificationSteps = unique(generatedSteps.length > 0
    ? generatedSteps
    : [
        ...(reportData.onsite_checklist || []),
        ...(reportData.type === "single" ? (reportData.axis_interpretations || []).map((item) => item.action) : []),
      ]).slice(0, 4);

  return (
    <article className="overflow-hidden rounded-2xl border bg-card surface-shadow">
      <div className="flex flex-col gap-4 border-b p-4 sm:p-6 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="text-xs font-bold text-primary">입지 리서치{dataPeriod ? ` · ${dataPeriod}` : ""}</p>
          <h2 className="mt-2 text-2xl font-black md:text-3xl">{title}</h2>
          {reportData.type === "single" && <p className="mt-2 text-sm text-muted-foreground">{reportData.area_name || areaName || "선택 상권"} · {reportData.industry_name || "상권 맥락"}</p>}
          {reportData.type === "single" && (
            <span className="mt-3 inline-flex rounded-full border border-primary/25 bg-primary/5 px-2.5 py-1 text-[11px] font-bold text-primary">
              {generationModeLabel(reportData.generation_mode)}
            </span>
          )}
        </div>
        {DEMO_MODE ? (
          <div className="max-w-xs rounded-xl border border-[#0f766e]/25 bg-[#e6fffa] px-3.5 py-2.5 text-xs font-semibold leading-5 text-[#115e59]">
            실행 데모에서는 계정 저장과 PDF 내보내기를 생략합니다.
          </div>
        ) : (
          <div className="flex shrink-0 flex-wrap gap-2">
            <button onClick={onDownload} disabled={isExporting} className="inline-flex items-center gap-2 rounded-lg border px-3.5 py-2.5 text-sm font-semibold transition-colors hover:bg-accent disabled:opacity-50">
              <Download className="h-4 w-4" /> {isExporting ? "PDF 생성 중..." : "PDF 다운로드"}
            </button>
            <button onClick={onSave} disabled={isReportSaving} className="inline-flex items-center gap-2 rounded-lg bg-primary px-3.5 py-2.5 text-sm font-bold text-primary-foreground transition-colors hover:bg-[#115e59] disabled:opacity-50">
              <Save className="h-4 w-4" /> {isReportSaving ? "저장 중..." : "저장"}
            </button>
          </div>
        )}
      </div>

      {header && (
        <section className="border-b border-primary/20 bg-primary/[0.06] px-4 py-7 sm:px-6 sm:py-8">
          <div className="grid gap-7 lg:grid-cols-[1.35fr_0.65fr] lg:items-start">
            <div className="border-l-4 border-primary pl-5">
              <p className="text-xs font-bold text-primary">종합 의견</p>
              <h3 className="mt-2 text-2xl font-black leading-tight">{header.judgement_line || "조건 확인 필요"}</h3>
              <p className="mt-3 max-w-3xl text-sm leading-7 text-muted-foreground">{reportData.executive_interpretation || reportData.summary}</p>
            </div>
            <dl className="border-y py-4 text-center">
              <div className="px-3"><dt className="text-[11px] font-semibold text-muted-foreground">입지 등급</dt><dd className="mt-1 text-3xl font-black text-primary">{displayGradeOrPending(header.display_grade, header.grade || header.score)}</dd></div>
            </dl>
          </div>
          {(header.key_metrics || []).length > 0 && (
            <dl className="mt-7 grid border-t pt-4 sm:grid-cols-2 lg:grid-cols-5">
              {(header.key_metrics || []).slice(0, 5).map((item, index) => (
                <div key={`km-${index}`} className="border-b px-3 py-3 first:pl-0 lg:border-b-0 lg:border-r lg:last:border-r-0">
                  <dt className="text-[11px] font-semibold text-muted-foreground">{(item.label || "").replace(/점수/g, "등급")}</dt>
                  <dd className="mt-1 text-sm font-black">{userFacingMetricDisplay(item.label, item.display)}</dd>
                  {item.note && <p className="mt-1 text-[10px] leading-4 text-muted-foreground">{item.note}</p>}
                </div>
              ))}
            </dl>
          )}
        </section>
      )}

      {(reportData.thesis || []).length > 0 && (
        <section className="border-b px-4 py-6 sm:px-6 sm:py-7">
          <h3 className="text-xl font-black">핵심 논거</h3>
          <ol className="mt-4 divide-y border-y">
            {(reportData.thesis || []).map((item, index) => (
              <li key={`thesis-${index}`} className="grid gap-3 py-4 text-sm leading-7 md:grid-cols-[48px_1fr]">
                <span className="font-black text-primary">0{index + 1}</span><span>{item}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {reportData.type === "single" && <ReportVisualSection reportData={reportData} />}

      {reportData.type === "single" && (reportData.axis_interpretations || []).length > 0 && (
        <section className="border-b px-4 py-6 sm:px-6 sm:py-7">
          <h3 className="text-xl font-black">핵심 판단 근거</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">각 지표가 현재 결론에 어떤 영향을 주는지 비교 기준과 함께 정리했습니다.</p>
          <div className="mt-5 divide-y border-y">
            {(reportData.axis_interpretations || []).map((axis) => (
              <article key={axis.axis} className="grid gap-5 py-5 lg:grid-cols-[150px_1fr_1fr]">
                <div>
                  <p className="font-black">{axisDisplayName(axis.axis)}</p>
                  <p className="mt-1 text-lg font-black text-primary">{displayGradeOrPending(axis.display_grade, axis.grade || axis.interpretation_level)}</p>
                  {axis.interpretation_level && <p className="mt-1 text-xs font-semibold text-muted-foreground">{axis.interpretation_level}</p>}
                </div>
                <div><p className="text-[11px] font-bold uppercase text-muted-foreground">해석</p><p className="mt-2 text-sm leading-7">{axis.meaning}</p></div>
                <div>
                  <p className="text-[11px] font-bold uppercase text-muted-foreground">수치 근거</p>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">{axis.evidence}</p>
                  {axis.risk && <p className="mt-3 border-l-2 border-rose-400 pl-3 text-xs leading-5 text-rose-700 dark:text-rose-300">{axis.risk}</p>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {reportAlternatives.length > 0 && (
        <section className="border-b px-4 py-6 sm:px-6 sm:py-7">
          <h3 className="text-xl font-black">대안 상권</h3>
          <div className="mt-4 overflow-x-auto rounded-xl border">
            <table className="w-full min-w-[620px] text-left text-sm">
              <thead className="border-b bg-muted/30 text-xs text-muted-foreground">
                <tr><th className="px-4 py-3">상권</th><th className="px-4 py-3">입지 등급</th><th className="px-4 py-3">비교 판단</th></tr>
              </thead>
              <tbody className="divide-y">
                {reportAlternatives.map((item, index) => (
                  <tr key={`${item.area_name || "alternative"}-${index}`}>
                    <td className="px-4 py-3 font-bold">{item.area_name || "대안 상권"}</td>
                    <td className="px-4 py-3 font-black text-primary">{displayGradeOrPending(item.display_grade, item.grade || (typeof item.score === "string" ? item.score : null))}</td>
                    <td className="px-4 py-3 leading-6 text-muted-foreground">{item.judgement || "같은 업종 기준으로 함께 비교할 후보입니다."}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {reportData.type === "single" && (reportData.trend_analysis || reportData.user_fit || newsItems.length > 0) && (
        <section className="border-b px-4 py-6 sm:px-6 sm:py-7">
          <h3 className="text-xl font-black">사용자 조건과 최근 변화</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            정형 지표의 등급과 함께 선택한 위치·업종·예산에 적용되는 최근 변화를 함께 정리했습니다.
          </p>

          <div className="mt-5 grid border-y lg:grid-cols-2 lg:divide-x">
            <div className="py-5 lg:pr-7">
              <p className="text-[11px] font-bold text-muted-foreground">시장 변화</p>
              <p className="mt-2 text-sm leading-7">{publicNarrativeText(reportData.trend_analysis || reportData.score_interpretation)}</p>
            </div>
            <div className="border-t py-5 lg:border-t-0 lg:pl-7">
              <p className="text-[11px] font-bold text-muted-foreground">예산·운영 적합성</p>
              <p className="mt-2 text-sm leading-7">{publicNarrativeText(reportData.user_fit || "입력된 예산이 없어 비용 조건은 별도 가정하지 않았습니다.")}</p>
            </div>
          </div>

          <TwoTierNewsEvidence items={newsItems} className="mt-6 shadow-none" />
        </section>
      )}

      {reportData.type === "comparison" && (
        <section className="border-b px-4 py-6 sm:px-6 sm:py-7">
          <p className="text-xs font-bold uppercase text-teal-600">Comparison</p>
          <h3 className="mt-1 text-xl font-black">상권 비교 판단</h3>
          <p className="mt-3 text-lg font-bold">{reportData.top_recommendation_name}</p>
          <p className="mt-2 text-sm leading-7 text-muted-foreground">{reportData.top_recommendation_reason}</p>
          {reportData.comparison_matrix && reportData.comparison_matrix.length > 0 && (
            <div className="mt-5 overflow-x-auto border-y">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="text-xs text-muted-foreground"><tr><th className="py-3 pr-4">상권</th><th className="py-3 pr-4">판단</th><th className="py-3 pr-4">강점</th><th className="py-3 pr-4">확인 영역</th><th className="py-3">해석</th></tr></thead>
                <tbody className="divide-y">
                  {reportData.comparison_matrix.map((row, idx) => (
                    <tr key={`${row.area_name}-${idx}`}><td className="py-3 pr-4 font-bold">{row.area_name}</td><td className="py-3 pr-4">{row.interpretation_level}</td><td className="py-3 pr-4">{row.strong_axis}</td><td className="py-3 pr-4">{row.watch_axis}</td><td className="py-3 leading-6 text-muted-foreground">{row.interpretation}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      <section className="grid gap-8 border-b px-4 py-6 sm:px-6 sm:py-7 lg:grid-cols-2 lg:gap-0">
        {reportData.type === "single" && <DecisionList eyebrow="Downside" title="판단을 바꿀 변수" items={decisionRisks} />}
        <DecisionList eyebrow="Field work" title="현장 검증 순서" items={verificationSteps} divided={reportData.type === "single"} />
      </section>

      <EvidenceDisclosure reportData={reportData} dataPeriod={dataPeriod} />
    </article>
  );
}

function axisDisplayName(axis: string) {
  const normalized = axis.replace(/\s*축$/, "").trim();
  const labels: Record<string, string> = { 매출: "시장성", 시장성: "시장성", 경쟁: "경쟁 구조", "경쟁 구조": "경쟁 구조", 수요: "수요 기반", "수요 기반": "수요 기반", 접근성: "접근·유입", "접근·유입": "접근·유입" };
  return labels[normalized] || normalized;
}

function DecisionList({ eyebrow, title, items, divided }: { eyebrow: string; title: string; items: string[]; divided?: boolean }) {
  return (
    <div className={divided ? "lg:border-l lg:pl-8" : "lg:pr-8"}>
      <p className="text-xs font-bold text-primary">{eyebrow}</p>
      <h3 className="mt-1 text-xl font-black">{title}</h3>
      {items.length > 0 ? (
        <ol className="mt-4 divide-y border-y">
          {items.map((item, index) => <li key={`${title}-${index}`} className="grid grid-cols-[34px_1fr] gap-3 py-3 text-sm leading-6"><span className="font-black text-muted-foreground">{String(index + 1).padStart(2, "0")}</span><span>{publicNarrativeText(item)}</span></li>)}
        </ol>
      ) : <p className="mt-3 text-sm text-muted-foreground">추가 확인 항목이 없습니다.</p>}
    </div>
  );
}

function EvidenceDisclosure({ reportData, dataPeriod }: { reportData: ReportData; dataPeriod?: string }) {
  const citations = Array.from(new Map((reportData.source_citations || []).map((item) => [`${item.provider || ""}|${item.dataset_name || item.title || ""}`, item])).values());
  const dataSources = citations.filter((item) => item.theme !== "해석 기준" && item.theme !== "산정 결과" && item.theme !== "최근 정책·지역 이슈");
  const newsItems = reportData.news_evidence || [];
  const originalIssues = reportData.original_validation_issues || [];
  const basis = unique([...(reportData.evidence_basis || []), ...(reportData.methodology_notes || [])]);
  return (
    <details className="group bg-muted/20">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-5 transition-colors hover:bg-muted/40 sm:px-6">
        <Database className="h-5 w-5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1"><p className="font-bold">데이터 출처 및 산정 기준</p><p className="mt-0.5 text-xs text-muted-foreground">정량 출처 {dataSources.length}개 · 외부 자료 {newsItems.length}개 · {dataPeriod || dataSources[0]?.period || "기준시점 별도 표기"}</p></div>
        <ChevronDown className="h-5 w-5 shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t px-4 py-6 sm:px-6">
        {reportData.type === "single" && (
          <section className="mb-7 rounded-lg border bg-background p-4">
            <h4 className="font-bold">생성 품질 기록</h4>
            <p className="mt-2 text-sm text-muted-foreground">
              최종 생성 방식: <span className="font-semibold text-foreground">{generationModeLabel(reportData.generation_mode)}</span>
              {originalIssues.length > 0 ? ` · 자동 검증 ${originalIssues.length}건 교정 시도` : " · 최초 검증 통과"}
            </p>
            {originalIssues.length > 0 && (
              <details className="mt-3 border-t pt-3">
                <summary className="cursor-pointer text-xs font-bold text-muted-foreground">검증 추적용 원문 보기</summary>
                <ul className="mt-2 space-y-2 font-mono text-[11px] leading-5 text-muted-foreground">
                  {originalIssues.map((issue, index) => <li key={`original-issue-${index}`} className="break-words">{issue}</li>)}
                </ul>
              </details>
            )}
          </section>
        )}
        {dataSources.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="border-b text-xs text-muted-foreground"><tr><th className="py-3 pr-5">원천 기관</th><th className="py-3 pr-5">데이터셋</th><th className="py-3 pr-5">기준 단위</th><th className="py-3">이 보고서에서 사용한 항목</th></tr></thead>
              <tbody className="divide-y">
                {dataSources.map((item, index) => (
                  <tr key={`${item.dataset_name || item.title}-${index}`}>
                    <td className="py-4 pr-5 align-top font-semibold">{item.provider || "공공 데이터 원천"}</td>
                    <td className="py-4 pr-5 align-top">
                      {item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer" className="font-semibold text-primary hover:underline">{item.dataset_name || item.title}</a> : <span className="font-semibold">{item.dataset_name || item.title}</span>}
                      {item.period && <p className="mt-1 text-xs text-muted-foreground">기준: {item.period}</p>}
                    </td>
                    <td className="py-4 pr-5 align-top text-muted-foreground">{item.granularity || "-"}</td>
                    <td className="py-4 align-top leading-6"><p>{item.used_for || item.theme || "정량 분석"}</p>{item.caveat && <p className="mt-1 text-xs text-muted-foreground">해석 범위: {item.caveat}</p>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {newsItems.length > 0 && (
          <p className="mt-6 border-t pt-4 text-xs leading-5 text-muted-foreground">
            외부 자료의 판단 근거·참고 구분과 사용 제한은 본문의
            ‘두 단계 기사·보도자료’에서 확인할 수 있습니다.
          </p>
        )}
        <div className="mt-7 grid gap-8 border-t pt-6 md:grid-cols-2">
          <div><h4 className="font-bold">산정 기준</h4><ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">{basis.map((item, index) => <li key={`basis-${index}`} className="border-l-2 border-primary/40 pl-3">{item}</li>)}</ul></div>
          <div><h4 className="font-bold">해석 범위</h4><ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">{(reportData.limitations || []).map((item, index) => <li key={`limit-${index}`} className="border-l-2 border-slate-300 pl-3">{item}</li>)}</ul></div>
        </div>
      </div>
    </details>
  );
}

function ReportVisualSection({ reportData }: { reportData: SingleReport }) {
  const visuals = buildReportVisuals(reportData);
  const hasVisuals =
    visuals.axisRows.length > 0 ||
    visuals.salesTrend.length > 0 ||
    visuals.topIndustries.length > 0 ||
    visuals.alternatives.length > 0 ||
    visuals.demandMix.length > 0;

  if (!hasVisuals) return null;

  return (
    <section className="border-b bg-background p-4 sm:p-5">
      <div className="mb-4 flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <div>
          <h3 className="text-xl font-black">핵심 데이터 시각화</h3>
        </div>
        <p className="text-xs leading-5 text-muted-foreground">비교 기준과 원천을 차트별로 함께 표기했습니다.</p>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        {visuals.axisRows.length > 0 && (
          <GradePanel
            title="입지 판단 구성"
            rows={visuals.axisRows}
            note="시장성·경쟁 구조·수요 기반·접근 유입을 등급으로 비교합니다."
            source="입지봇 분석 모델 · 서울특별시 서울 상권분석서비스 기반"
          />
        )}

        {visuals.salesTrend.length > 0 && (
          <ChartFrame title="최근 8분기 매출 추이" note="분기별 시장 규모가 확대·축소되는 방향을 확인합니다." source="서울특별시 서울 상권분석서비스 추정매출-상권">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={visuals.salesTrend} margin={{ top: 8, right: 18, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                <YAxis tick={{ fontSize: 12 }} width={50} />
                <Tooltip />
                <Line type="monotone" dataKey="value" name="매출액(억원)" stroke={SALES_COLOR} strokeWidth={2.5} dot={{ r: 3 }} activeDot={{ r: 5 }} />
              </LineChart>
            </ResponsiveContainer>
          </ChartFrame>
        )}

        {visuals.topIndustries.length > 0 && (
          <ChartFrame title="상권 내 업종 매출 상위 목록" note="같은 상권 안에서 업종별 시장 규모를 비교합니다." source="서울특별시 서울 상권분석서비스 추정매출-상권">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={visuals.topIndustries} layout="vertical" margin={{ top: 6, right: 20, left: 12, bottom: 6 }}>
                <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 12 }} />
                <YAxis type="category" dataKey="label" width={92} tick={{ fontSize: 11 }} />
                <Tooltip />
                <Bar dataKey="value" name="매출액(억원)" fill="#0f766e" radius={[0, 6, 6, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        )}

        {visuals.alternatives.length > 0 && (
          <GradePanel
            title="동일 업종 대안 상권 비교"
            rows={visuals.alternatives}
            note="대상 상권과 대안 후보의 입지 등급을 같은 기준으로 비교합니다."
            source="입지봇 분석 모델 · 동일 업종 입지 평가"
          />
        )}

        {visuals.demandMix.length > 0 && (
          <ChartFrame title="수요 인구 지표" note="단위: 만 명 · 유동인구는 분기 누계를 일수로 나눈 일평균" source="서울특별시 서울 상권분석서비스 상주·직장·길단위인구-상권">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={visuals.demandMix} margin={{ top: 8, right: 18, left: 0, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} />
                <Tooltip />
                <Bar dataKey="value" name="인구(만 명)" fill={DEMAND_COLOR} radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </ChartFrame>
        )}
      </div>
    </section>
  );
}

function GradePanel({ title, rows, note, source }: { title: string; rows: GradeRow[]; note: string; source: string }) {
  return (
    <section className="rounded-xl border bg-card p-5">
      <h4 className="font-black">{title}</h4>
      <p className="mt-1 text-xs leading-5 text-muted-foreground">{note}</p>
      <dl className="mt-4 divide-y border-y">
        {rows.map((row) => (
          <div key={`${title}-${row.label}`} className="flex items-center justify-between gap-4 py-3">
            <dt className="text-sm font-semibold">{row.label}</dt>
            <dd className="rounded-full bg-primary/10 px-3 py-1 text-lg font-black text-primary">{row.grade}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-[10px] leading-4 text-muted-foreground">출처: {source}</p>
    </section>
  );
}

function ChartFrame({ title, note, source, children }: { title: string; note?: string; source: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="mb-3">
        <h4 className="font-bold">{title}</h4>
        {note && <p className="mt-1 text-xs leading-5 text-muted-foreground">{note}</p>}
      </div>
      <div className="h-[260px] min-w-0">{children}</div>
      <p className="mt-2 border-t pt-2 text-[10px] leading-4 text-muted-foreground">자료: {source}</p>
    </div>
  );
}

export default function AIInsights() {
  return (
    <Suspense fallback={<div className="p-10 text-center">Loading...</div>}>
      <AIInsightsContent />
    </Suspense>
  );
}
