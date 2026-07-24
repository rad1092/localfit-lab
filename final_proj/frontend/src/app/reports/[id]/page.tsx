"use client";

import { apiUrl, fetchAuth } from "@/lib/api";
import { displayGradeOrPending, userFacingMetricDisplay } from "@/lib/score-grade";
import {
  TwoTierNewsEvidence,
  type NewsEvidenceItem,
} from "@/components/TwoTierNewsEvidence";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, ChevronDown, Database, Download } from "lucide-react";

const CHART_IDS = ["C1", "C2", "C3", "C4", "C5"] as const;

type RadarMetric = {
  subject: string;
  scores: Record<string, number | null>;
};

type HeaderMetric = {
  label?: string;
  display?: string;
};

type ReportHeader = {
  judgement_line?: string;
  score_label?: string;
  score?: string;
  grade?: string;
  display_grade?: string;
  percentile?: string;
  key_metrics?: HeaderMetric[];
};

type AxisInterpretation = {
  axis: string;
  score_display?: string;
  grade?: string | null;
  display_grade?: string | null;
  interpretation_level?: string;
  meaning?: string;
  evidence?: string;
};

type AlternativeComparison = {
  area_name?: string;
  score?: string | number;
  grade?: string | null;
  display_grade?: string | null;
  cost?: string | number;
  judgement?: string;
};

type AlternativeArea = {
  area_name?: string;
  reason?: string;
};

type SourceCitation = {
  title?: string;
  provider?: string;
  dataset_name?: string;
  source_url?: string;
  period?: string;
  granularity?: string;
  theme?: string;
  used_for?: string;
  caveat?: string;
};

type ReportResult = {
  narrative_title?: string;
  area_name?: string;
  user_condition?: {
    area_name?: string;
    business_type?: string | null;
    budget?: number | null;
  };
  target_area_analysis?: {
    decision_label?: string;
    score_coverage_tier?: string;
    coverage_reason?: string;
  };
  fit_score?: number | null;
  axis_interpretations?: AxisInterpretation[];
  header_block?: ReportHeader;
  thesis?: string[];
  alternatives?: AlternativeComparison[];
  radar_metrics?: RadarMetric[];
  source_citations?: SourceCitation[];
  news_evidence?: NewsEvidenceItem[];
  trend_analysis?: string;
  user_fit?: string;
  score_interpretation?: string;
  onsite_checklist?: string[];
  action_plan?: string[];
  alternative_areas?: AlternativeArea[];
  evidence_basis?: string[];
  methodology_notes?: string[];
  limitations?: string[];
  generation_mode?: "llm" | "partial_fallback" | "deterministic";
  original_validation_issues?: string[];
  quality_status?: string;
};

type ChatbotHistoryReport = {
  id: number;
  area_name: string;
  business_type?: string | null;
  budget?: number | null;
  result_data: ReportResult;
};

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  const router = useRouter();
  const { id } = use(params);
  const [report, setReport] = useState<ChatbotHistoryReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isExporting, setIsExporting] = useState(false);
  const [chartUrls, setChartUrls] = useState<Record<string, string>>({});

  useEffect(() => {
    const fetchReport = async () => {
      try {
        const res = await fetchAuth(apiUrl(`/chatbot/history/${id}`));
        if (!res.ok) throw new Error("리포트를 불러오지 못했습니다.");
        const data: ChatbotHistoryReport = await res.json();
        setReport(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "리포트를 불러오지 못했습니다.");
      } finally {
        setLoading(false);
      }
    };
    fetchReport();
  }, [id]);

  // 차트 PNG는 인증 헤더가 필요해 <img src>로 직접 접근할 수 없다 — blob으로 받아 objectURL로 표시.
  useEffect(() => {
    if (!report?.result_data) return;
    let revoked = false;
    const urls: Record<string, string> = {};
    const load = async () => {
      await Promise.all(
        CHART_IDS.map(async (chartId) => {
          try {
            const res = await fetchAuth(apiUrl(`/chatbot/history/${id}/charts/${chartId}`));
            if (!res.ok) return;
            const blob = await res.blob();
            if (!revoked) urls[chartId] = URL.createObjectURL(blob);
          } catch {
            /* 차트 없으면 해당 이미지 생략 */
          }
        })
      );
      if (!revoked) setChartUrls({ ...urls });
    };
    load();
    return () => {
      revoked = true;
      Object.values(urls).forEach((url) => URL.revokeObjectURL(url));
    };
  }, [report, id]);

  const downloadReport = async () => {
    setIsExporting(true);
    try {
      const res = await fetchAuth(apiUrl(`/chatbot/history/${id}/download?format=pdf`));
      if (!res.ok) throw new Error("PDF 다운로드 실패");
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const title = report?.result_data?.narrative_title || `${report?.area_name}_AI리포트`;
      a.download = `${String(title).replace(/[\\/:*?"<>|]+/g, "_")}.pdf`;
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

  const budgetBand = useMemo(() => {
    const value = Number(report?.budget || report?.result_data?.user_condition?.budget || 0);
    if (!value) return "예산 미입력";
    if (value < 5000) return "5천만 원 미만";
    if (value < 10000) return "5천만-1억 원대";
    if (value < 20000) return "1억-2억 원대";
    return "2억 원 이상";
  }, [report]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-10 w-10 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  if (error || !report?.result_data) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center">
        <AlertTriangle className="mb-4 h-12 w-12 text-rose-500" />
        <h2 className="mb-2 text-xl font-bold">리포트를 찾을 수 없습니다</h2>
        <p className="mb-6 text-muted-foreground">{error}</p>
        <button onClick={() => router.push("/")} className="rounded-lg bg-primary px-6 py-2.5 font-bold text-primary-foreground transition-colors hover:bg-[#115e59]">
          홈으로 돌아가기
        </button>
      </div>
    );
  }

  const data = report.result_data;
  const condition = data.user_condition || {};
  const axes = data.axis_interpretations || [];
  const header = data.header_block || {};
  const thesis: string[] = data.thesis || [];
  const alternatives = data.alternatives || [];
  const citations = data.source_citations || [];
  const newsItems: NewsEvidenceItem[] = data.news_evidence?.length
    ? data.news_evidence
    : citations
        .filter((item) => item.theme === "최근 정책·지역 이슈")
        .map((item, index) => ({
          evidence_id: `legacy-news-${index}`,
          provider: item.provider,
          title: item.dataset_name || item.title,
          original_url: item.source_url,
          published_date: item.period,
          location_scope_label: item.granularity,
          decision_use: item.used_for,
          citation_index: index + 1,
        }));
  const coverage = data.target_area_analysis;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 pb-16">
      <div className="flex flex-col gap-5 rounded-2xl border bg-card p-5 surface-shadow sm:p-7 md:flex-row md:items-start md:justify-between">
        <div>
          <button onClick={() => router.back()} className="mb-4 inline-flex items-center gap-2 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" /> 돌아가기
          </button>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">입지 판단 리포트</p>
          <h1 className="mt-1 text-3xl font-black">{data.narrative_title || `${report.area_name} 상세 리포트`}</h1>
          <span className="mt-3 inline-flex rounded-full border border-primary/25 bg-primary/5 px-2.5 py-1 text-[11px] font-bold text-primary">
            {reportGenerationModeLabel(data.generation_mode)}
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={downloadReport} disabled={isExporting} className="inline-flex items-center gap-2 rounded-lg border bg-background px-3.5 py-2.5 text-sm font-semibold transition-colors hover:bg-accent disabled:opacity-50">
            <Download className="h-4 w-4" /> {isExporting ? "PDF 생성 중..." : "PDF 다운로드"}
          </button>
        </div>
      </div>

      {/* 판단 헤더: 5초 안에 결론이 보이는 블록 */}
      <section className="rounded-2xl border border-primary/25 bg-primary/[0.06] p-5 shadow-sm sm:p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <VerdictCard label="판단" value={header.judgement_line || data.target_area_analysis?.decision_label || "-"} strong />
          <VerdictCard label="입지 등급" value={displayGradeOrPending(header.display_grade, header.grade || header.score)} strong />
        </div>
        {coverage?.coverage_reason && (
          <p className="mt-4 rounded-lg border border-primary/10 bg-card/70 px-3 py-2 text-xs leading-5 text-muted-foreground">
            등급 적용 범위{coverage.score_coverage_tier ? ` · ${coverage.score_coverage_tier}` : ""}: {coverage.coverage_reason}
          </p>
        )}
        {(header.key_metrics || []).length > 0 && (
          <div className="mt-4 grid gap-2 md:grid-cols-5">
            {(header.key_metrics || []).slice(0, 5).map((item, index) => (
              <div key={`km-${index}`} className="rounded-lg border border-primary/10 bg-card/70 p-2.5 text-center">
                <p className="text-[11px] text-muted-foreground">{(item.label || "").replace(/점수/g, "등급")}</p>
                <p className="text-sm font-bold">{userFacingMetricDisplay(item.label, item.display)}</p>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* 판단 논지 */}
      {thesis.length > 0 && (
        <section className="rounded-2xl border bg-card p-5 shadow-sm sm:p-6">
          <h2 className="mb-3 text-xl font-bold">판단 논지</h2>
          <ol className="list-inside list-decimal space-y-2 text-sm leading-7">
            {thesis.map((item, index) => (
              <li key={`thesis-${index}`}>{stripPublicMarkers(item)}</li>
            ))}
          </ol>
        </section>
      )}

      <section className="grid gap-4 md:grid-cols-3">
        <MetricCard label="상권" value={condition.area_name || report.area_name} />
        <MetricCard label="업종" value={condition.business_type || report.business_type} />
        <MetricCard label="예산대" value={budgetBand} />
      </section>

      {/* 판단 근거와 지표 구성 */}
      {axes.length > 0 && (
        <section>
          <h2 className="mb-3 text-xl font-bold">핵심 판단 근거</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {axes.map((axis, index) => (
              <article key={`${axis.axis}-${index}`} className="rounded-xl border bg-card p-4 shadow-sm sm:p-5">
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="font-bold">{axis.axis}</h3>
                  <span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-black text-primary">{displayGradeOrPending(axis.display_grade, axis.grade || axis.score_display || axis.interpretation_level)}</span>
                </div>
                <p className="mb-2 text-sm leading-6">{stripPublicMarkers(axis.meaning)}</p>
                <p className="text-xs leading-5 text-muted-foreground">{axis.evidence}</p>
              </article>
            ))}
          </div>
        </section>
      )}

      {/* 추이 분석 */}
      {(chartUrls.C2 || chartUrls.C3 || data.trend_analysis) && (
        <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
          <h2 className="mb-3 text-xl font-bold">추이 분석</h2>
          <div className="grid gap-4 md:grid-cols-2">
            <ChartImage src={chartUrls.C2} title="최근 8분기 매출 추이" />
            <ChartImage src={chartUrls.C3} title="상권 내 업종 매출 Top 8" />
          </div>
          {data.trend_analysis && <p className="mt-3 text-sm leading-7">{stripPublicMarkers(data.trend_analysis)}</p>}
        </section>
      )}

      {/* 대안 비교 */}
      {alternatives.length > 0 && (
        <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
          <h2 className="mb-3 text-xl font-bold">대안 비교</h2>
          {alternatives.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 pr-3">상권</th>
                    <th className="py-2 pr-3">입지 등급</th>
                    <th className="py-2">한 줄 판단</th>
                  </tr>
                </thead>
                <tbody>
                  {alternatives.map((item, index) => (
                    <tr key={`alt-${index}`} className="border-b last:border-0">
                      <td className="py-2 pr-3 font-semibold">{item.area_name}</td>
                      <td className="py-2 pr-3 font-black text-primary">{displayGradeOrPending(item.display_grade, item.grade || (typeof item.score === "string" ? item.score : null))}</td>
                      <td className="py-2 leading-6">{stripPublicMarkers(item.judgement)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {/* 사용자 조건 대입 */}
      {(data.user_fit || chartUrls.C5) && (
        <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
          <h2 className="mb-3 text-xl font-bold">예산·운영 조건</h2>
          <div className="grid items-start gap-4 md:grid-cols-2">
            <ChartImage src={chartUrls.C5} title="수요 인구 지표" />
            <p className="text-sm leading-7">{stripPublicMarkers(data.user_fit || data.score_interpretation || "")}</p>
          </div>
        </section>
      )}

      <section className="grid gap-4 md:grid-cols-2">
        <ListBlock title="현장 체크리스트" items={[...(data.onsite_checklist || []), ...(data.action_plan || [])]} />
        <ListBlock title="대안 상권 요약" items={(data.alternative_areas || []).map((item) => `${item.area_name} · ${item.reason || ""}`)} />
      </section>

      <TwoTierNewsEvidence items={newsItems} />

      <SourceDisclosure citations={citations} data={data} />
    </div>
  );
}

function stripPublicMarkers(text?: string | null) {
  return String(text || "")
    .replace(/\s*\[CHART:C[1-5]\]\s*/g, " ")
    .replace(/\s*\[NEWS:\d+\]\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

function reportGenerationModeLabel(mode?: ReportResult["generation_mode"]) {
  const labels: Record<string, string> = {
    llm: "AI 해석",
    partial_fallback: "AI 해석 · 일부 규칙 보정",
    deterministic: "규칙 기반 결과",
  };
  return (mode && labels[mode]) || "이전 리포트 · 생성 방식 기록 없음";
}

function ChartImage({ src, title }: { src?: string; title: string }) {
  if (!src) return null;
  return (
    <figure className="overflow-hidden rounded-xl border bg-card p-3 shadow-sm">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt={title} className="w-full rounded" />
      <figcaption className="mt-1 text-center text-xs text-muted-foreground">{title}</figcaption>
    </figure>
  );
}

function VerdictCard({ label, value, strong }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className="text-center md:text-left">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className={`mt-1 ${strong ? "text-lg font-black text-primary" : "font-bold"}`}>{value}</p>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 font-bold">{value}</p>
    </div>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-xl border bg-card p-4 shadow-sm">
      <h3 className="mb-3 font-bold">{title}</h3>
      {items.length > 0 ? (
        <ul className="list-inside list-disc space-y-1 text-sm leading-6 text-muted-foreground">
          {items.map((item, index) => (
            <li key={`${title}-${index}`}>{stripPublicMarkers(item)}</li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">표시할 항목이 없습니다.</p>
      )}
    </div>
  );
}

function SourceDisclosure({ citations, data }: { citations: SourceCitation[]; data: ReportResult }) {
  const sources = citations.filter((item) => !["해석 기준", "산정 결과", "최근 정책·지역 이슈"].includes(item.theme || ""));
  return (
    <details className="group overflow-hidden rounded-2xl border bg-card shadow-sm">
      <summary className="flex cursor-pointer list-none items-center gap-3 p-4 transition-colors hover:bg-muted/30 sm:p-5">
        <Database className="h-5 w-5 text-primary" />
        <div className="flex-1"><p className="font-bold">데이터 출처 및 산정 기준</p><p className="mt-0.5 text-xs text-muted-foreground">공공 데이터 {sources.length}개 · {sources[0]?.period || "기준시점 별도 표기"}</p></div>
        <ChevronDown className="h-5 w-5 transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t p-4 sm:p-5">
        <section className="mb-6 rounded-lg border bg-muted/20 p-4">
          <h3 className="font-bold">생성 품질 기록</h3>
          <p className="mt-2 text-sm text-muted-foreground">{reportGenerationModeLabel(data.generation_mode)}</p>
          {(data.original_validation_issues || []).length > 0 && (
            <details className="mt-3 border-t pt-3">
              <summary className="cursor-pointer text-xs font-bold text-muted-foreground">검증 추적용 원문 보기</summary>
              <ul className="mt-2 space-y-2 font-mono text-[11px] leading-5 text-muted-foreground">
                {(data.original_validation_issues || []).map((issue, index) => <li key={`issue-${index}`} className="break-words">{issue}</li>)}
              </ul>
            </details>
          )}
        </section>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left text-sm">
            <thead className="border-b text-xs text-muted-foreground"><tr><th className="py-3 pr-4">원천 기관</th><th className="py-3 pr-4">데이터셋</th><th className="py-3 pr-4">기준 단위</th><th className="py-3">사용 목적</th></tr></thead>
            <tbody className="divide-y">
              {sources.map((item, index) => <tr key={`${item.dataset_name || item.title}-${index}`}><td className="py-3 pr-4 align-top font-semibold">{item.provider || "공공 데이터 원천"}</td><td className="py-3 pr-4 align-top">{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer" className="font-semibold text-primary hover:underline">{item.dataset_name || item.title}</a> : item.dataset_name || item.title}</td><td className="py-3 pr-4 align-top text-muted-foreground">{item.granularity || "-"}</td><td className="py-3 align-top leading-6">{item.used_for}{item.caveat && <p className="mt-1 text-xs text-muted-foreground">해석 범위: {item.caveat}</p>}</td></tr>)}
            </tbody>
          </table>
        </div>
        <div className="mt-6 grid gap-8 border-t pt-5 md:grid-cols-2">
          <div><h3 className="font-bold">산정 기준</h3><ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">{[...(data.evidence_basis || []), ...(data.methodology_notes || [])].map((item: string, index: number) => <li key={`basis-${index}`} className="border-l-2 border-primary/40 pl-3">{item}</li>)}</ul></div>
          <div><h3 className="font-bold">해석 범위</h3><ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">{(data.limitations || []).map((item: string, index: number) => <li key={`limit-${index}`} className="border-l-2 border-slate-300 pl-3">{item}</li>)}</ul></div>
        </div>
      </div>
    </details>
  );
}
