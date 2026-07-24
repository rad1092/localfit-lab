"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Eye,
  EyeOff,
  FileSearch,
  LoaderCircle,
  MessageSquareText,
  Play,
  RotateCcw,
  ShieldCheck,
  Trash2,
  Users,
} from "lucide-react";
import { apiUrl, fetchAuth } from "@/lib/api";

interface PanelProps {
  refreshToken: number;
}

interface QualityPanelProps extends PanelProps {
  snapshot?: {
    generated_at: string | null;
    summary: {
      source_count: number;
      healthy_source_count: number;
      product_quarter: string | null;
    };
    layers: QualityCheck[];
  };
}

interface AnalyticsOverview {
  period_days: number;
  total_events: number;
  unique_sessions: number;
  event_counts: Record<string, number>;
}

interface FunnelStage {
  event_type: string;
  unique_sessions: number;
  conversion_from_previous: number | null;
  conversion_from_search: number;
}

interface FunnelResponse {
  period_days: number;
  stages: FunnelStage[];
}

interface PopularArea {
  area_code: string;
  area_name: string | null;
  event_count: number;
  unique_sessions: number;
}

interface PopularAreasResponse {
  period_days: number;
  items: PopularArea[];
}

type Health = "healthy" | "advisory" | "warning" | "error" | "unknown" | "missing";

interface QualitySummary {
  generated_at: string | null;
  overall_status: Health;
  status_counts: Partial<Record<Health, number>>;
  source_count: number;
  healthy_source_count: number;
  product_quarter: string | null;
}

interface QualityCheck {
  key: string;
  label: string;
  status: Health;
  count: number | null;
  unit: string | null;
  updated_at: string | null;
  note: string | null;
}

interface QualityChecksResponse {
  generated_at: string | null;
  items: QualityCheck[];
}

interface ErrorLog {
  id: string;
  source: "external_api" | "pipeline" | string;
  level: string;
  title: string;
  message: string;
  status_code: number | null;
  occurred_at: string | null;
}

interface ErrorLogsResponse {
  generated_at: string;
  items: ErrorLog[];
  count: number;
  limit: number;
}

type CommentStatus = "visible" | "hidden" | "deleted";

interface AdminComment {
  id: number;
  area_code: string;
  area_name: string | null;
  industry_code: string | null;
  parent_id: number | null;
  body: string;
  status: CommentStatus;
  author: { id: number; nickname: string } | null;
  reply_count: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

interface AdminCommentsResponse {
  items: AdminComment[];
  page: number;
  page_size: number;
  total: number;
}

type ReportEvaluationStatus = "queued" | "running" | "completed" | "failed";

interface ReportEvaluationContext {
  area_code: string | null;
  area_name: string | null;
  industry_code: string | null;
  industry_name: string | null;
  quarter: string | null;
  budget_manwon: number | null;
  ai_model: string | null;
  generation_mode: string | null;
  quality_status: string | null;
  evaluable: boolean;
  not_evaluable_reason: string | null;
}

interface ReportEvaluationSummary {
  question_count?: number;
  pass_count?: number;
  fail_count?: number;
  hard_fail_count?: number;
  overall_status?: "PASS" | "FAIL";
  automatic_status?: "PASS" | "FAIL";
  automatic_failed_question_ids?: string[];
  manual_review_status?: "PENDING" | "COMPLETE" | "NOT_REQUIRED";
  manual_review_question_ids?: string[];
  manual_review?: {
    reviewer?: string | null;
    reviewed_at?: string | null;
  };
  manual_review_history?: Array<{
    reviewer?: string | null;
    reviewed_at?: string | null;
    artifact_sha256?: Record<string, string | null>;
    questions?: Record<string, {
      decision?: "PASS" | "FAIL";
      actual?: string;
      rationale_ko?: string;
    }>;
  }>;
  db_grounding_status?: "PASS" | "FAIL";
  semantic_quality_status?: "PASS" | "FAIL";
  artifact_quality_status?: "PASS" | "FAIL";
  negative_control_status?: "PASS" | "FAIL";
  decision_rule_ko?: string;
  evaluated_at?: string;
}

interface ReportEvaluationRun {
  id: string;
  report_job_id: string;
  status: ReportEvaluationStatus;
  progress_message: string;
  protocol_version: string | null;
  overall_status: "PASS" | "FAIL" | null;
  automatic_status: "PASS" | "FAIL" | null;
  summary: ReportEvaluationSummary | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

interface ReportEvaluationQuestion {
  id: string;
  category: string;
  question_ko: string;
  severity: string;
  gate: boolean;
  method: string;
  report_path?: string | null;
  actual: unknown;
  expected: unknown;
  decision: "PASS" | "FAIL";
  rationale_ko: string;
  source_queries?: Array<{
    id: string;
    sql: string;
    params: Record<string, unknown>;
  }>;
  source_tables?: string[];
  source_artifacts?: string[];
}

interface ReportEvaluationRunDetail extends ReportEvaluationRun {
  questions: ReportEvaluationQuestion[];
}

interface ReportEvaluationCandidate {
  job_id: string;
  report_type: "single" | "comparison";
  title: string;
  owner_email: string | null;
  created_at: string;
  completed_at: string | null;
  context: ReportEvaluationContext;
  latest_evaluation: ReportEvaluationRun | null;
}

interface ReportEvaluationReportsResponse {
  items: ReportEvaluationCandidate[];
  count: number;
  total: number;
  retention_days: number;
  evaluated_reports_retained: boolean;
  evaluator: {
    protocol: string;
    external_llm_called: boolean;
    manual_review_question_ids: string[];
    decision_rule: string;
  };
}

type ManualReviewDecision = "" | "PASS" | "FAIL";
type EvaluationReferenceTab = "guide" | "questions" | "document";

interface ManualReviewDraft {
  q050: {
    decision: ManualReviewDecision;
    actual: string;
    rationale_ko: string;
  };
  q051: {
    decision: ManualReviewDecision;
    actual: string;
    rationale_ko: string;
  };
}

const emptyManualReviewDraft: ManualReviewDraft = {
  q050: { decision: "", actual: "", rationale_ko: "" },
  q051: { decision: "", actual: "", rationale_ko: "" },
};

const eventLabels: Record<string, string> = {
  page_view: "방문",
  search_submitted: "검색",
  area_selected: "상권 선택",
  report_requested: "리포트 요청",
  report_completed: "리포트 완료",
  report_failed: "리포트 실패",
};

const healthMeta: Record<Health, { label: string; className: string }> = {
  healthy: { label: "정상", className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" },
  advisory: { label: "참고", className: "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300" },
  warning: { label: "확인 필요", className: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
  error: { label: "오류", className: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" },
  unknown: { label: "미확인", className: "bg-muted text-muted-foreground" },
  missing: { label: "자료 없음", className: "bg-muted text-muted-foreground" },
};

const commentStatusMeta: Record<CommentStatus, { label: string; className: string }> = {
  visible: { label: "공개", className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" },
  hidden: { label: "숨김", className: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" },
  deleted: { label: "삭제", className: "bg-muted text-muted-foreground" },
};

async function readJson<T>(path: string): Promise<T> {
  const response = await fetchAuth(apiUrl(path), { cache: "no-store" });
  if (!response.ok) throw new Error(`관리자 데이터를 불러오지 못했습니다. (${response.status})`);
  return (await response.json()) as T;
}

function formatNumber(value: number | null | undefined) {
  return Number(value || 0).toLocaleString("ko-KR");
}

function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("ko-KR");
}

function PanelState({ loading, error }: { loading: boolean; error: string | null }) {
  if (loading) {
    return (
      <div className="flex min-h-48 items-center justify-center rounded-lg border bg-card">
        <LoaderCircle className="h-5 w-5 animate-spin text-muted-foreground" aria-label="불러오는 중" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm font-semibold text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
        {error}
      </div>
    );
  }
  return null;
}

export function AdminAnalyticsPanel({ refreshToken }: PanelProps) {
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [funnel, setFunnel] = useState<FunnelResponse | null>(null);
  const [popular, setPopular] = useState<PopularAreasResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextOverview, nextFunnel, nextPopular] = await Promise.all([
        readJson<AnalyticsOverview>("/admin/analytics/overview?days=30"),
        readJson<FunnelResponse>("/admin/analytics/funnel?days=30"),
        readJson<PopularAreasResponse>("/admin/analytics/popular-areas?days=30&limit=10"),
      ]);
      setOverview(nextOverview);
      setFunnel(nextFunnel);
      setPopular(nextPopular);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "이용 현황을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshToken]);

  const state = <PanelState loading={loading} error={error} />;
  if (loading || error || !overview || !funnel || !popular) return state;

  const cards = [
    { label: "익명 방문 세션", value: overview.unique_sessions, icon: Users },
    { label: "검색", value: overview.event_counts.search_submitted, icon: BarChart3 },
    { label: "상권 선택", value: overview.event_counts.area_selected, icon: Eye },
    { label: "리포트 완료", value: overview.event_counts.report_completed, icon: CheckCircle2 },
  ];

  return (
    <section className="space-y-4" aria-label="익명 이용 현황">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {cards.map(({ label, value, icon: Icon }) => (
          <article key={label} className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between gap-3 text-xs font-semibold text-muted-foreground">
              <span>{label}</span><Icon className="h-4 w-4" />
            </div>
            <p className="mt-3 text-xl font-black">{formatNumber(value)}</p>
            <p className="mt-1 text-xs text-muted-foreground">최근 {overview.period_days}일</p>
          </article>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_1fr]">
        <section className="rounded-lg border bg-card p-4">
          <h2 className="text-sm font-bold">검색부터 리포트까지</h2>
          <div className="mt-4 grid gap-2 sm:grid-cols-4">
            {funnel.stages.map((stage, index) => (
              <article key={stage.event_type} className="rounded-md border bg-muted/20 p-3">
                <p className="text-xs font-semibold text-muted-foreground">{index + 1}. {eventLabels[stage.event_type] || stage.event_type}</p>
                <p className="mt-2 text-lg font-black">{formatNumber(stage.unique_sessions)}</p>
                {stage.conversion_from_previous !== null && (
                  <p className="mt-1 text-[11px] text-muted-foreground">이전 단계의 {stage.conversion_from_previous.toFixed(1)}%</p>
                )}
              </article>
            ))}
          </div>
        </section>

        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b px-4 py-3">
            <h2 className="text-sm font-bold">인기 상권</h2>
          </div>
          <div className="max-h-72 overflow-y-auto">
            {popular.items.length === 0 ? (
              <p className="p-5 text-sm text-muted-foreground">아직 집계된 상권 선택이 없습니다.</p>
            ) : popular.items.map((item, index) => (
              <Link
                key={item.area_code}
                href={`/trade?area=${encodeURIComponent(item.area_code)}`}
                className="flex items-center gap-3 border-b px-4 py-3 text-sm last:border-b-0 hover:bg-muted/50"
              >
                <span className="w-5 text-xs font-bold text-muted-foreground">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate font-semibold">{item.area_name || item.area_code}</span>
                <span className="shrink-0 text-xs text-muted-foreground">{formatNumber(item.unique_sessions)} 세션</span>
              </Link>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}

export function AdminQualityPanel({ refreshToken, snapshot }: QualityPanelProps) {
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [checks, setChecks] = useState<QualityCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextSummary, nextChecks] = await Promise.all([
        readJson<QualitySummary>("/admin/data-quality/summary"),
        readJson<QualityChecksResponse>("/admin/data-quality/checks"),
      ]);
      setSummary(nextSummary);
      setChecks(nextChecks.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "데이터 품질을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (snapshot) return;
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshToken, snapshot]);

  const resolvedSummary: QualitySummary | null = snapshot ? {
    generated_at: snapshot.generated_at,
    overall_status: snapshot.layers.reduce<Health>((worst, item) => {
      const order: Record<Health, number> = { healthy: 0, advisory: 1, unknown: 2, missing: 2, warning: 3, error: 4 };
      return order[item.status] > order[worst] ? item.status : worst;
    }, "healthy"),
    status_counts: snapshot.layers.reduce<Partial<Record<Health, number>>>((counts, item) => {
      counts[item.status] = (counts[item.status] || 0) + 1;
      return counts;
    }, {}),
    source_count: snapshot.summary.source_count,
    healthy_source_count: snapshot.summary.healthy_source_count,
    product_quarter: snapshot.summary.product_quarter,
  } : summary;
  const resolvedChecks = snapshot?.layers || checks;
  const state = <PanelState loading={loading} error={error} />;
  if ((!snapshot && loading) || error || !resolvedSummary) return state;
  const overall = healthMeta[resolvedSummary.overall_status] || healthMeta.unknown;

  return (
    <section className="space-y-4" aria-label="데이터 품질">
      <div className="grid gap-3 md:grid-cols-3">
        <article className="rounded-lg border bg-card p-4">
          <p className="text-xs font-semibold text-muted-foreground">전체 상태</p>
          <span className={`mt-3 inline-flex rounded-md px-2.5 py-1 text-sm font-bold ${overall.className}`}>{overall.label}</span>
        </article>
        <article className="rounded-lg border bg-card p-4">
          <p className="text-xs font-semibold text-muted-foreground">원천 정상</p>
          <p className="mt-3 text-xl font-black">{formatNumber(resolvedSummary.healthy_source_count)} / {formatNumber(resolvedSummary.source_count)}</p>
        </article>
        <article className="rounded-lg border bg-card p-4">
          <p className="text-xs font-semibold text-muted-foreground">제품 기준 분기</p>
          <p className="mt-3 text-xl font-black">{resolvedSummary.product_quarter || "-"}</p>
        </article>
      </div>

      <section className="overflow-hidden rounded-lg border bg-card">
        <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <h2 className="text-sm font-bold">처리 계층 상태</h2>
          <p className="text-xs text-muted-foreground">{formatDate(resolvedSummary.generated_at)}</p>
        </div>
        <div className="divide-y">
          {resolvedChecks.map((check) => {
            const meta = healthMeta[check.status] || healthMeta.unknown;
            return (
              <article key={check.key} className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">{check.label}</p>
                  {check.note && <p className="mt-1 truncate text-xs text-muted-foreground" title={check.note}>{check.note}</p>}
                </div>
                <div className="flex items-center gap-3">
                  {check.count !== null && <span className="text-xs text-muted-foreground">{formatNumber(check.count)} {check.unit || ""}</span>}
                  <span className={`rounded-md px-2 py-1 text-xs font-bold ${meta.className}`}>{meta.label}</span>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </section>
  );
}

export function AdminErrorLogsPanel({ refreshToken }: PanelProps) {
  const [payload, setPayload] = useState<ErrorLogsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPayload(await readJson<ErrorLogsResponse>("/admin/error-logs?limit=100"));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "오류 기록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshToken]);

  const state = <PanelState loading={loading} error={error} />;
  if (loading || error || !payload) return state;

  return (
    <section className="overflow-hidden rounded-lg border bg-card" aria-label="오류 기록">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <div>
          <h2 className="text-sm font-bold">최근 오류</h2>
          <p className="mt-1 text-xs text-muted-foreground">비밀값과 로컬 경로를 가린 최대 {payload.limit}건</p>
        </div>
        <span className="rounded-md bg-red-50 px-2 py-1 text-xs font-bold text-red-700 dark:bg-red-950/40 dark:text-red-300">{payload.count}건</span>
      </div>
      <div className="max-h-[480px] overflow-y-auto">
        {payload.items.length === 0 ? (
          <div className="flex min-h-44 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
            <ShieldCheck className="h-6 w-6 text-emerald-600" />최근 오류가 없습니다.
          </div>
        ) : payload.items.map((item) => (
          <article key={item.id} className="border-b px-4 py-3 last:border-b-0">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="font-semibold">{item.title}</p>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{item.source === "pipeline" ? "파이프라인" : "외부 API"}</span>
                  {item.status_code && <span className="text-[11px] font-bold text-red-700 dark:text-red-300">HTTP {item.status_code}</span>}
                </div>
                <p className="mt-1 max-h-20 overflow-y-auto whitespace-pre-wrap break-words text-xs leading-5 text-muted-foreground">{item.message}</p>
                <p className="mt-1 text-[11px] text-muted-foreground">{formatDate(item.occurred_at)}</p>
              </div>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

export function AdminCommentsPanel({ refreshToken }: PanelProps) {
  const [statusFilter, setStatusFilter] = useState<"all" | CommentStatus>("all");
  const [page, setPage] = useState(1);
  const [payload, setPayload] = useState<AdminCommentsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setPayload(await readJson<AdminCommentsResponse>(`/admin/comments?status=${statusFilter}&page=${page}&page_size=50`));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load, refreshToken]);

  const updateStatus = useCallback(async (commentId: number, nextStatus: CommentStatus) => {
    setUpdatingId(commentId);
    try {
      const response = await fetchAuth(apiUrl(`/admin/comments/${commentId}/status`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: nextStatus }),
      });
      if (!response.ok) throw new Error(`댓글 상태를 바꾸지 못했습니다. (${response.status})`);
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글 상태를 바꾸지 못했습니다.");
    } finally {
      setUpdatingId(null);
    }
  }, [load]);

  const state = <PanelState loading={loading} error={error} />;
  if (loading || (error && !payload) || !payload) return state;

  return (
    <section className="overflow-hidden rounded-lg border bg-card" aria-label="댓글 관리">
      <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-sm font-bold"><MessageSquareText className="h-4 w-4" />댓글 관리</h2>
          <p className="mt-1 text-xs text-muted-foreground">전체 {formatNumber(payload.total)}건 · 한 번에 최대 50건</p>
        </div>
        <select
          value={statusFilter}
          onChange={(event) => {
            setPage(1);
            setStatusFilter(event.target.value as "all" | CommentStatus);
          }}
          className="h-9 rounded-md border bg-background px-3 text-sm"
          aria-label="댓글 상태 필터"
        >
          <option value="all">전체 상태</option>
          <option value="visible">공개</option>
          <option value="hidden">숨김</option>
          <option value="deleted">삭제</option>
        </select>
      </div>
      {error && <p className="border-b bg-red-50 px-4 py-2 text-xs font-semibold text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
      <div className="max-h-[540px] overflow-y-auto">
        {payload.items.length === 0 ? (
          <p className="p-6 text-center text-sm text-muted-foreground">해당 상태의 댓글이 없습니다.</p>
        ) : payload.items.map((comment) => {
          const meta = commentStatusMeta[comment.status];
          const busy = updatingId === comment.id;
          return (
            <article key={comment.id} className="border-b px-4 py-4 last:border-b-0">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <Link href={`/trade?area=${encodeURIComponent(comment.area_code)}`} className="font-bold hover:underline">
                      {comment.area_name || comment.area_code}
                    </Link>
                    <span className="text-muted-foreground">{comment.industry_code || "전체 업종"}</span>
                    {comment.parent_id && <span className="rounded bg-muted px-1.5 py-0.5 text-[11px]">답글</span>}
                    <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${meta.className}`}>{meta.label}</span>
                  </div>
                  <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6">{comment.body}</p>
                  <p className="mt-2 text-[11px] text-muted-foreground">
                    {comment.author?.nickname || "탈퇴한 사용자"} · {formatDate(comment.created_at)}
                    {comment.reply_count > 0 ? ` · 답글 ${comment.reply_count}` : ""}
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  {comment.status === "visible" ? (
                    <button type="button" disabled={busy} onClick={() => void updateStatus(comment.id, "hidden")} className="inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs font-semibold hover:bg-muted disabled:opacity-50">
                      {busy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <EyeOff className="h-3.5 w-3.5" />}숨김
                    </button>
                  ) : (
                    <button type="button" disabled={busy} onClick={() => void updateStatus(comment.id, "visible")} className="inline-flex h-8 items-center gap-1 rounded-md border px-2 text-xs font-semibold hover:bg-muted disabled:opacity-50">
                      {busy ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}복구
                    </button>
                  )}
                  {comment.status !== "deleted" && (
                    <button type="button" disabled={busy} onClick={() => void updateStatus(comment.id, "deleted")} className="inline-flex h-8 items-center gap-1 rounded-md border border-red-200 px-2 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30">
                      <Trash2 className="h-3.5 w-3.5" />삭제
                    </button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
      {payload.total > payload.page_size && (
        <div className="flex items-center justify-between gap-3 border-t px-4 py-3">
          <span className="text-xs text-muted-foreground">
            {payload.page} / {Math.ceil(payload.total / payload.page_size)} 페이지
          </span>
          <div className="flex gap-2">
            <button type="button" disabled={payload.page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))} className="h-8 rounded-md border px-3 text-xs font-semibold hover:bg-muted disabled:opacity-40">이전</button>
            <button type="button" disabled={payload.page * payload.page_size >= payload.total || loading} onClick={() => setPage((current) => current + 1)} className="h-8 rounded-md border px-3 text-xs font-semibold hover:bg-muted disabled:opacity-40">다음</button>
          </div>
        </div>
      )}
    </section>
  );
}

const manualEvaluationMethods = new Set([
  "independent_visual_review",
  "independent_pdf_page_review",
]);

function evaluationStatusClass(value: string | null | undefined) {
  if (value === "PASS" || value === "completed") {
    return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
  }
  if (value === "FAIL" || value === "failed") {
    return "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300";
  }
  return "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
}

function evaluationStatusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    PASS: "PASS",
    FAIL: "FAIL",
    queued: "대기",
    running: "평가 중",
    completed: "완료",
    failed: "실행 실패",
  };
  return (value && labels[value]) || "미평가";
}

function displayEvaluationValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "-";
  return JSON.stringify(value, null, 2);
}

export function AdminReportEvaluationPanel({ refreshToken }: PanelProps) {
  const [payload, setPayload] = useState<ReportEvaluationReportsResponse | null>(null);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<ReportEvaluationRunDetail | null>(null);
  const [query, setQuery] = useState("");
  const [failOnly, setFailOnly] = useState(true);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [manualSubmitting, setManualSubmitting] = useState(false);
  const [artifactOpening, setArtifactOpening] = useState<string | null>(null);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [referenceTab, setReferenceTab] = useState<EvaluationReferenceTab>("guide");
  const [evaluationDocument, setEvaluationDocument] = useState<string | null>(null);
  const [manualDraft, setManualDraft] = useState<ManualReviewDraft>(
    emptyManualReviewDraft,
  );
  const [error, setError] = useState<string | null>(null);

  const loadReports = useCallback(async () => {
    setLoading(true);
    try {
      const next = await readJson<ReportEvaluationReportsResponse>(
        "/admin/report-evaluations/reports?limit=100",
      );
      setPayload(next);
      setSelectedJobId((current) => (
        current && next.items.some((item) => item.job_id === current)
          ? current
          : next.items[0]?.job_id || null
      ));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "리포트 평가 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRun = useCallback(async (runId: string) => {
    setDetailLoading(true);
    try {
      const next = await readJson<ReportEvaluationRunDetail>(
        `/admin/report-evaluations/runs/${encodeURIComponent(runId)}`,
      );
      setSelectedRun(next);
      setError(null);
      return next;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "평가 결과를 불러오지 못했습니다.");
      return null;
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadReports(), 0);
    return () => window.clearTimeout(timer);
  }, [loadReports, refreshToken]);

  const selectedReport = payload?.items.find((item) => item.job_id === selectedJobId) || null;
  const latestRunId = selectedReport?.latest_evaluation?.id || null;

  useEffect(() => {
    const timer = window.setTimeout(() => {
      if (latestRunId) {
        void loadRun(latestRunId);
      } else {
        setSelectedRun(null);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [latestRunId, loadRun]);

  useEffect(() => {
    if (!selectedRun || !["queued", "running"].includes(selectedRun.status)) return;
    const timer = window.setInterval(async () => {
      const next = await loadRun(selectedRun.id);
      if (next && !["queued", "running"].includes(next.status)) {
        await loadReports();
      }
    }, 1_500);
    return () => window.clearInterval(timer);
  }, [loadReports, loadRun, selectedRun]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setManualDraft(emptyManualReviewDraft);
      setReferenceOpen(false);
      setReferenceTab("guide");
      setEvaluationDocument(null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [selectedRun?.id]);

  const startEvaluation = async () => {
    if (!selectedReport) return;
    setStarting(true);
    setError(null);
    try {
      const response = await fetchAuth(
        apiUrl(`/admin/report-evaluations/reports/${encodeURIComponent(selectedReport.job_id)}/run`),
        { method: "POST" },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof body?.detail === "string"
            ? body.detail
            : `평가 작업을 시작하지 못했습니다. (${response.status})`,
        );
      }
      setSelectedRun({ ...(body as ReportEvaluationRun), questions: [] });
      await loadReports();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "평가 작업을 시작하지 못했습니다.");
    } finally {
      setStarting(false);
    }
  };

  const openEvaluationArtifact = async (artifactName: string) => {
    if (!selectedRun) return;
    if (artifactName === "evaluation-report.md" && evaluationDocument !== null) {
      setReferenceTab("document");
      setReferenceOpen(true);
      return;
    }
    const popup = artifactName === "evaluation-report.md"
      ? null
      : window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    setArtifactOpening(artifactName);
    setError(null);
    try {
      const response = await fetchAuth(
        apiUrl(
          `/admin/report-evaluations/runs/${encodeURIComponent(selectedRun.id)}/artifacts/${encodeURIComponent(artifactName)}`,
        ),
      );
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string"
            ? body.detail
            : `평가 산출물을 열지 못했습니다. (${response.status})`,
        );
      }
      if (artifactName === "evaluation-report.md") {
        const bytes = await response.arrayBuffer();
        let markdown: string;
        try {
          markdown = new TextDecoder("utf-8", { fatal: true })
            .decode(bytes)
            .replace(/^\uFEFF/, "");
        } catch {
          throw new Error("평가문서가 올바른 UTF-8 파일이 아닙니다.");
        }
        setEvaluationDocument(markdown);
        setReferenceTab("document");
        setReferenceOpen(true);
        return;
      }
      const objectUrl = URL.createObjectURL(await response.blob());
      if (popup) {
        popup.location.href = objectUrl;
      } else {
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        anchor.click();
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
    } catch (reason) {
      popup?.close();
      setError(reason instanceof Error ? reason.message : "평가 산출물을 열지 못했습니다.");
    } finally {
      setArtifactOpening(null);
    }
  };

  const submitManualReview = async (
    reviewDraft: ManualReviewDraft = manualDraft,
  ) => {
    if (!selectedRun) return;
    const rows = [reviewDraft.q050, reviewDraft.q051];
    if (
      rows.some(
        (row) => !row.decision
          || row.actual.trim().length < 3
          || row.rationale_ko.trim().length < 3,
      )
    ) {
      setError("Q050·Q051의 PASS/FAIL, 관찰 내용, 판정 이유를 모두 입력해주세요.");
      return;
    }
    setManualSubmitting(true);
    setError(null);
    try {
      const response = await fetchAuth(
        apiUrl(
          `/admin/report-evaluations/runs/${encodeURIComponent(selectedRun.id)}/manual-review`,
        ),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            q050: {
              ...reviewDraft.q050,
              actual: reviewDraft.q050.actual.trim(),
              rationale_ko: reviewDraft.q050.rationale_ko.trim(),
            },
            q051: {
              ...reviewDraft.q051,
              actual: reviewDraft.q051.actual.trim(),
              rationale_ko: reviewDraft.q051.rationale_ko.trim(),
            },
          }),
        },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof body?.detail === "string"
            ? body.detail
            : `수동 검수 결과를 반영하지 못했습니다. (${response.status})`,
        );
      }
      setSelectedRun({
        ...(body as ReportEvaluationRun),
        questions: selectedRun.questions,
      });
      await loadReports();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "수동 검수 결과를 반영하지 못했습니다.");
    } finally {
      setManualSubmitting(false);
    }
  };

  const approveManualReview = () => {
    const approved: ManualReviewDraft = {
      q050: {
        decision: "PASS",
        actual: "C3·C5 차트의 축과 값 라벨 단위를 직접 확인함",
        rationale_ko: "억원·만명 등 필요한 단위가 차트에 정상적으로 표시됨",
      },
      q051: {
        decision: "PASS",
        actual: "PDF의 외부자료 배치와 페이지 경계를 직접 확인함",
        rationale_ko: "제목·분리 설명·표 헤더와 내용이 페이지 경계에서 고립되지 않음",
      },
    };
    setManualDraft(approved);
    void submitManualReview(approved);
  };

  const showReferenceTab = (tab: EvaluationReferenceTab) => {
    setReferenceOpen(true);
    setReferenceTab(tab);
    if (tab === "document" && evaluationDocument === null) {
      void openEvaluationArtifact("evaluation-report.md");
    }
  };

  const filteredReports = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase("ko-KR");
    if (!normalized) return payload?.items || [];
    return (payload?.items || []).filter((item) => (
      [
        item.title,
        item.context.area_name,
        item.context.area_code,
        item.context.industry_name,
        item.context.industry_code,
        item.owner_email,
      ].some((value) => String(value || "").toLocaleLowerCase("ko-KR").includes(normalized))
    ));
  }, [payload?.items, query]);

  const allQuestions = useMemo(
    () => [...(selectedRun?.questions || [])].sort(
      (left, right) => left.id.localeCompare(right.id),
    ),
    [selectedRun?.questions],
  );

  const questions = useMemo(() => {
    const sorted = [...allQuestions].sort((left, right) => {
      if (left.decision !== right.decision) return left.decision === "FAIL" ? -1 : 1;
      return left.id.localeCompare(right.id);
    });
    return failOnly ? sorted.filter((item) => item.decision === "FAIL") : sorted;
  }, [allQuestions, failOnly]);

  const counts = useMemo(() => {
    const reports = payload?.items || [];
    return {
      automaticPass: reports.filter((item) => item.latest_evaluation?.automatic_status === "PASS").length,
      automaticFail: reports.filter((item) => item.latest_evaluation?.automatic_status === "FAIL").length,
      unevaluated: reports.filter((item) => !item.latest_evaluation).length,
      notEvaluable: reports.filter((item) => !item.context.evaluable).length,
    };
  }, [payload?.items]);

  const state = <PanelState loading={loading} error={error && !payload ? error : null} />;
  if (loading && !payload) return state;
  if (!payload) return state;

  return (
    <section className="space-y-4" aria-label="AI 상세 리포트 평가">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ["평가 가능 생성 건", payload.items.length - counts.notEvaluable],
          ["자동 PASS", counts.automaticPass],
          ["자동 FAIL", counts.automaticFail],
          ["아직 미평가", counts.unevaluated],
        ].map(([label, value]) => (
          <article key={String(label)} className="rounded-lg border bg-card p-4">
            <p className="text-xs font-semibold text-muted-foreground">{label}</p>
            <p className="mt-2 text-xl font-black">{formatNumber(Number(value))}</p>
          </article>
        ))}
      </div>

      <section className="rounded-lg border border-amber-200 bg-amber-50/70 p-4 text-xs leading-5 text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
        <p className="font-bold">기존 56문항 평가 프로토콜을 그대로 사용합니다.</p>
        <p className="mt-1">
          자동 문항은 현재 운영 DB 원문·critic·PDF 구조와 대조합니다. Q050·Q051은 현재 차트/PDF 해시에 묶인 수동 시각검수가 필요하므로 자동 PASS로 처리하지 않고, 산출물을 확인한 관리자가 판정을 제출합니다.
          미평가 생성 작업 원문은 {payload.retention_days}일 보관되고, 평가한 원문은 평가 이력과 함께 유지됩니다.
          평가 시점 DB가 생성 시점과 달라졌다면 그 차이도 판정에 반영될 수 있습니다.
        </p>
      </section>

      {error && (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs font-semibold text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
          {error}
        </p>
      )}

      <div className="grid min-h-[620px] gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <section className="overflow-hidden rounded-lg border bg-card">
          <div className="border-b p-3">
            <div className="flex items-center justify-between gap-3">
              <h2 className="flex items-center gap-2 text-sm font-bold">
                <FileSearch className="h-4 w-4" />생성 리포트
              </h2>
              <button
                type="button"
                onClick={() => void loadReports()}
                disabled={loading}
                className="rounded-md p-1.5 hover:bg-muted disabled:opacity-50"
                aria-label="리포트 평가 목록 새로고침"
              >
                <RotateCcw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              </button>
            </div>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="상권·업종·모델 검색"
              className="mt-3 h-9 w-full rounded-md border bg-background px-3 text-sm"
              aria-label="평가할 리포트 검색"
            />
          </div>
          <div className="max-h-[680px] overflow-y-auto">
            {filteredReports.length === 0 ? (
              <p className="p-6 text-center text-sm text-muted-foreground">평가할 생성 리포트가 없습니다.</p>
            ) : filteredReports.map((item) => {
              const latest = item.latest_evaluation;
              const selected = item.job_id === selectedJobId;
              return (
                <button
                  key={item.job_id}
                  type="button"
                  onClick={() => setSelectedJobId(item.job_id)}
                  className={`w-full border-b p-4 text-left last:border-b-0 hover:bg-muted/50 ${selected ? "bg-primary/5" : ""}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <p className="line-clamp-2 min-w-0 text-sm font-bold">
                      {item.context.area_name || item.context.area_code || "상권 미확인"} · {item.context.industry_name || "업종 미지정"}
                    </p>
                    <span className={`shrink-0 rounded px-1.5 py-0.5 text-[11px] font-bold ${evaluationStatusClass(latest?.automatic_status || latest?.status)}`}>
                      {latest?.automatic_status
                        ? `자동 ${latest.automatic_status}`
                        : evaluationStatusLabel(latest?.status)}
                    </span>
                  </div>
                  <p className="mt-2 truncate text-xs text-muted-foreground">{item.context.ai_model || "생성 모델 미기록"} · {formatDate(item.completed_at)}</p>
                  {!item.context.evaluable && (
                    <p className="mt-2 text-[11px] font-semibold text-amber-700 dark:text-amber-300">{item.context.not_evaluable_reason}</p>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        <section className="min-w-0 overflow-hidden rounded-lg border bg-card">
          {!selectedReport ? (
            <div className="flex min-h-[620px] items-center justify-center p-6 text-sm text-muted-foreground">리포트를 선택하세요.</div>
          ) : (
            <>
              <header className="border-b p-5">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-primary">생성 작업 #{selectedReport.job_id}</p>
                    <h2 className="mt-1 text-lg font-black">
                      {selectedReport.context.area_name || selectedReport.context.area_code || "상권 미확인"} · {selectedReport.context.industry_name || "업종 미지정"}
                    </h2>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      {selectedReport.context.ai_model || "모델 미기록"} · {selectedReport.context.generation_mode || "생성 방식 미기록"} · 생성 {formatDate(selectedReport.completed_at)}
                      {selectedReport.context.budget_manwon !== null ? ` · 예산 ${formatNumber(selectedReport.context.budget_manwon)}만원` : ""}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void startEvaluation()}
                    disabled={
                      starting
                      || !selectedReport.context.evaluable
                      || ["queued", "running"].includes(selectedRun?.status || "")
                    }
                    className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-bold text-primary-foreground disabled:opacity-50"
                  >
                    {starting || ["queued", "running"].includes(selectedRun?.status || "")
                      ? <LoaderCircle className="h-4 w-4 animate-spin" />
                      : <Play className="h-4 w-4" />}
                    {selectedReport.latest_evaluation ? "다시 평가" : "평가기 실행"}
                  </button>
                </div>
                {!selectedReport.context.evaluable && (
                  <p className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                    {selectedReport.context.not_evaluable_reason}
                  </p>
                )}
              </header>

              <div className="p-5">
                {detailLoading && !selectedRun ? (
                  <div className="flex min-h-72 items-center justify-center"><LoaderCircle className="h-5 w-5 animate-spin" /></div>
                ) : !selectedRun ? (
                  <div className="flex min-h-72 flex-col items-center justify-center text-center">
                    <FileSearch className="h-8 w-8 text-muted-foreground" />
                    <p className="mt-3 text-sm font-bold">아직 평가 기록이 없습니다.</p>
                    <p className="mt-1 text-xs text-muted-foreground">평가기 실행을 누르면 기존 질문셋으로 DB 원문과 리포트를 대조합니다.</p>
                  </div>
                ) : (
                  <div className="space-y-5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`rounded px-2 py-1 text-xs font-bold ${evaluationStatusClass(selectedRun.status)}`}>
                        {evaluationStatusLabel(selectedRun.status)}
                      </span>
                      {selectedRun.automatic_status && (
                        <span className={`rounded px-2 py-1 text-xs font-bold ${evaluationStatusClass(selectedRun.automatic_status)}`}>
                          자동 {selectedRun.automatic_status}
                        </span>
                      )}
                      {selectedRun.overall_status && (
                        <span className={`rounded px-2 py-1 text-xs font-bold ${evaluationStatusClass(selectedRun.overall_status)}`}>
                          전체 {selectedRun.overall_status}
                        </span>
                      )}
                      <span className="text-xs text-muted-foreground">{selectedRun.progress_message}</span>
                    </div>

                    {selectedRun.error_message && (
                      <p className="rounded-md border border-red-200 bg-red-50 p-3 text-xs font-semibold text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
                        {selectedRun.error_message}
                      </p>
                    )}

                    {selectedRun.summary && (
                      <>
                        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                          {[
                            ["전체 문항", selectedRun.summary.question_count || 0],
                            ["PASS", selectedRun.summary.pass_count || 0],
                            ["FAIL", selectedRun.summary.fail_count || 0],
                            ["Hard FAIL", selectedRun.summary.hard_fail_count || 0],
                          ].map(([label, value]) => (
                            <article key={String(label)} className="rounded-md border bg-muted/20 p-3">
                              <p className="text-[11px] font-semibold text-muted-foreground">{label}</p>
                              <p className="mt-1 text-lg font-black">{formatNumber(Number(value))}</p>
                            </article>
                          ))}
                        </div>
                        <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
                          {[
                            ["DB 원문", selectedRun.summary.db_grounding_status],
                            ["서사 품질", selectedRun.summary.semantic_quality_status],
                            ["산출물", selectedRun.summary.artifact_quality_status],
                            ["Negative control", selectedRun.summary.negative_control_status],
                          ].map(([label, value]) => (
                            <div key={String(label)} className="flex items-center justify-between rounded-md border px-3 py-2">
                              <span className="font-semibold text-muted-foreground">{label}</span>
                              <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${evaluationStatusClass(String(value || ""))}`}>{value || "-"}</span>
                            </div>
                          ))}
                        </div>
                        <section className="overflow-hidden rounded-md border bg-background">
                          <button
                            type="button"
                            onClick={() => setReferenceOpen((current) => !current)}
                            className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-muted/40"
                            aria-expanded={referenceOpen}
                          >
                            <span>
                              <span className="block text-sm font-black">평가기 작동 방식·전체 평가문항·평가문서</span>
                              <span className="mt-1 block text-xs text-muted-foreground">
                                평가 기준과 {formatNumber(allQuestions.length)}개 문항을 확인하고 평가문서를 이 화면 안에서 엽니다.
                              </span>
                            </span>
                            <span className="shrink-0 rounded-md border px-2.5 py-1 text-xs font-bold">
                              {referenceOpen ? "닫기" : "열기"}
                            </span>
                          </button>
                          {referenceOpen && (
                            <div className="border-t">
                              <div
                                className="flex flex-wrap gap-2 border-b bg-muted/20 p-3"
                                role="tablist"
                                aria-label="리포트 평가 참고 정보"
                              >
                                {([
                                  ["guide", "작동 방식"],
                                  ["questions", `전체 평가문항 ${allQuestions.length}`],
                                  ["document", "평가문서"],
                                ] as const).map(([tab, label]) => (
                                  <button
                                    key={tab}
                                    type="button"
                                    role="tab"
                                    aria-selected={referenceTab === tab}
                                    onClick={() => showReferenceTab(tab)}
                                    className={`h-8 rounded-md px-3 text-xs font-bold ${
                                      referenceTab === tab
                                        ? "bg-primary text-primary-foreground"
                                        : "border bg-background hover:bg-muted"
                                    }`}
                                  >
                                    {label}
                                  </button>
                                ))}
                              </div>
                              <div className="p-4">
                                {referenceTab === "guide" && (
                                  <div className="space-y-4 text-xs leading-5">
                                    <div className="grid gap-3 md:grid-cols-2">
                                      <article className="rounded-md border p-3">
                                        <p className="font-black">1. 평가 입력 고정</p>
                                        <p className="mt-1 text-muted-foreground">
                                          선택한 생성 리포트의 JSON, 현재 운영 DB 기준값, 생성된 PDF와 차트 파일을 한 평가 실행에 묶습니다.
                                        </p>
                                      </article>
                                      <article className="rounded-md border p-3">
                                        <p className="font-black">2. 자동 문항 실행</p>
                                        <p className="mt-1 text-muted-foreground">
                                          DB 원값·서사 계약·출처 표현·PDF 구조를 고정 질문셋으로 비교합니다. 평가 실행 중 추가 외부 LLM 호출은 없습니다.
                                        </p>
                                      </article>
                                      <article className="rounded-md border p-3">
                                        <p className="font-black">3. 수동 시각검수</p>
                                        <p className="mt-1 text-muted-foreground">
                                          Q050·Q051만 관리자가 PDF·C3·C5를 직접 확인합니다. 제출 내용은 당시 파일 해시와 묶이고, 한 번 완료되면 같은 평가 실행에는 다시 제출할 수 없습니다.
                                        </p>
                                      </article>
                                      <article className="rounded-md border p-3">
                                        <p className="font-black">4. 최종 판정</p>
                                        <p className="mt-1 text-muted-foreground">
                                          {payload.evaluator.decision_rule}. 수동 PASS는 자동 FAIL을 덮어쓰지 않습니다.
                                        </p>
                                      </article>
                                    </div>
                                    <div className="rounded-md bg-muted/40 p-3 text-muted-foreground">
                                      <p><strong className="text-foreground">평가기:</strong> {payload.evaluator.protocol}</p>
                                      <p><strong className="text-foreground">프로토콜:</strong> {selectedRun.protocol_version || "미기록"}</p>
                                      <p><strong className="text-foreground">다시 검수하려면:</strong> 상단의 다시 평가를 눌러 새 평가 실행을 만든 뒤 새 산출물을 검수합니다.</p>
                                    </div>
                                  </div>
                                )}
                                {referenceTab === "questions" && (
                                  <div>
                                    <p className="mb-3 text-xs leading-5 text-muted-foreground">
                                      문항을 누르면 질문, 판정 방식, 현재 결과와 이유를 열고 닫아 확인할 수 있습니다.
                                    </p>
                                    <div className="max-h-[620px] space-y-2 overflow-y-auto pr-1">
                                      {allQuestions.map((question) => {
                                        const manual = manualEvaluationMethods.has(question.method);
                                        return (
                                          <details key={`reference-${question.id}`} className="overflow-hidden rounded-md border">
                                            <summary className="flex cursor-pointer list-none items-start gap-3 p-3 hover:bg-muted/40">
                                              <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[11px] font-black ${evaluationStatusClass(question.decision)}`}>
                                                {question.decision}
                                              </span>
                                              <span className="min-w-0 flex-1">
                                                <span className="block text-xs font-black">
                                                  {question.id} · {question.category}
                                                  {question.gate ? " · HARD GATE" : ""}
                                                </span>
                                                <span className="mt-1 block text-sm font-semibold leading-5">{question.question_ko}</span>
                                              </span>
                                              {manual && (
                                                <span className="shrink-0 rounded bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                                                  수동
                                                </span>
                                              )}
                                            </summary>
                                            <div className="space-y-2 border-t bg-muted/10 p-3 text-xs leading-5">
                                              <p><strong>판정 방식:</strong> {question.method}</p>
                                              <p><strong>현재 판정 이유:</strong> {question.rationale_ko || "-"}</p>
                                              <p><strong>리포트 위치:</strong> {question.report_path || "-"}</p>
                                              <p><strong>기준 원천:</strong> {(question.source_tables || []).join(", ") || (question.source_artifacts || []).join(", ") || "-"}</p>
                                            </div>
                                          </details>
                                        );
                                      })}
                                    </div>
                                  </div>
                                )}
                                {referenceTab === "document" && (
                                  <div>
                                    {artifactOpening === "evaluation-report.md" ? (
                                      <div className="flex min-h-40 items-center justify-center gap-2 text-sm font-semibold text-muted-foreground">
                                        <LoaderCircle className="h-4 w-4 animate-spin" />
                                        평가문서를 불러오는 중입니다.
                                      </div>
                                    ) : evaluationDocument !== null ? (
                                      <pre className="max-h-[680px] overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#101715] p-4 text-xs leading-6 text-[#d8e5e1]">
                                        {evaluationDocument}
                                      </pre>
                                    ) : (
                                      <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-center">
                                        <p className="text-sm font-semibold">평가문서를 아직 불러오지 않았습니다.</p>
                                        <button
                                          type="button"
                                          onClick={() => void openEvaluationArtifact("evaluation-report.md")}
                                          className="h-9 rounded-md bg-primary px-4 text-xs font-bold text-primary-foreground"
                                        >
                                          평가문서 불러오기
                                        </button>
                                      </div>
                                    )}
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </section>
                        {selectedRun.summary.manual_review_status === "COMPLETE" && (
                          <div className="space-y-2">
                            <p className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
                              Q050·Q051 수동 시각검수가 현재 산출물 해시와 함께 반영됐습니다.
                              {selectedRun.summary.manual_review?.reviewer
                                ? ` 검수자 ${selectedRun.summary.manual_review.reviewer}`
                                : ""}
                              {selectedRun.summary.manual_review?.reviewed_at
                                ? ` · ${formatDate(selectedRun.summary.manual_review.reviewed_at)}`
                                : ""}
                            </p>
                            {(selectedRun.summary.manual_review_history?.length || 0) > 0 && (
                              <details className="rounded-md border bg-background">
                                <summary className="cursor-pointer px-3 py-2 text-xs font-bold">
                                  수동 판정 변경 이력 {selectedRun.summary.manual_review_history?.length}건
                                </summary>
                                <div className="space-y-2 border-t p-3">
                                  {selectedRun.summary.manual_review_history?.map((history, index) => (
                                    <article key={`${history.reviewed_at || "review"}-${index}`} className="rounded-md border p-3 text-xs">
                                      <p className="font-bold">
                                        {history.reviewer || "검수자 미기록"} · {formatDate(history.reviewed_at || null)}
                                      </p>
                                      {["Q050", "Q051"].map((questionId) => {
                                        const question = history.questions?.[questionId];
                                        return (
                                          <p key={questionId} className="mt-2 leading-5 text-muted-foreground">
                                            <strong className="text-foreground">{questionId} {question?.decision || "-"}</strong>
                                            {question?.actual ? ` · ${question.actual}` : ""}
                                            {question?.rationale_ko ? ` · ${question.rationale_ko}` : ""}
                                          </p>
                                        );
                                      })}
                                    </article>
                                  ))}
                                </div>
                              </details>
                            )}
                          </div>
                        )}
                        {selectedRun.status === "completed"
                          && selectedRun.summary.manual_review_status === "PENDING" && (
                          <section className="rounded-md border border-amber-200 bg-amber-50/60 p-4 dark:border-amber-900 dark:bg-amber-950/20">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                              <div>
                                <h3 className="text-sm font-black text-amber-900 dark:text-amber-100">Q050·Q051 수동 시각검수</h3>
                                <p className="mt-1 text-xs leading-5 text-amber-800 dark:text-amber-200">
                                  현재 평가에 사용된 PDF와 차트만 확인하세요. 둘 다 정상이면 바로 PASS 반영하고, 문제가 있으면 아래에서 항목별 판정과 근거를 입력하세요.
                                </p>
                              </div>
                              <div className="flex flex-wrap gap-2">
                                {[
                                  ["report.pdf", "PDF 열기"],
                                  ["C3.png", "C3 열기"],
                                  ["C5.png", "C5 열기"],
                                ].map(([artifactName, label]) => (
                                  <button
                                    key={artifactName}
                                    type="button"
                                    onClick={() => void openEvaluationArtifact(artifactName)}
                                    disabled={artifactOpening !== null}
                                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-amber-300 bg-background px-3 text-xs font-bold text-amber-900 hover:bg-amber-100 disabled:opacity-50 dark:border-amber-800 dark:text-amber-100 dark:hover:bg-amber-950"
                                  >
                                    {artifactOpening === artifactName && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                                    {label}
                                  </button>
                                ))}
                                <button
                                  type="button"
                                  onClick={approveManualReview}
                                  disabled={manualSubmitting || artifactOpening !== null}
                                  className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-700 px-3 text-xs font-black text-white hover:bg-emerald-800 disabled:opacity-50"
                                  title="PDF와 C3·C5 차트가 모두 정상일 때만 사용하세요."
                                >
                                  {manualSubmitting
                                    ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                                    : <CheckCircle2 className="h-3.5 w-3.5" />}
                                  두 항목 정상 확인 · PASS 반영
                                </button>
                              </div>
                            </div>
                            <div className="mt-4 grid gap-4 lg:grid-cols-2">
                              {([
                                [
                                  "q050",
                                  "Q050 · C3/C5 단위 표시",
                                  "차트 안의 축·값 라벨에서 억원·만명 등 단위를 직접 확인합니다.",
                                ],
                                [
                                  "q051",
                                  "Q051 · PDF 외부자료 배치",
                                  "제목·분리 설명·표 헤더와 내용이 페이지 경계에서 고립되지 않았는지 확인합니다.",
                                ],
                              ] as const).map(([key, title, description]) => (
                                <fieldset key={key} className="rounded-md border bg-background p-3">
                                  <legend className="px-1 text-xs font-black">{title}</legend>
                                  <p className="mb-3 text-[11px] leading-5 text-muted-foreground">{description}</p>
                                  <label className="block text-[11px] font-bold text-muted-foreground">
                                    판정
                                    <select
                                      value={manualDraft[key].decision}
                                      onChange={(event) => setManualDraft((current) => ({
                                        ...current,
                                        [key]: {
                                          ...current[key],
                                          decision: event.target.value as ManualReviewDecision,
                                        },
                                      }))}
                                      className="mt-1 h-9 w-full rounded-md border bg-background px-2 text-sm text-foreground"
                                    >
                                      <option value="">선택</option>
                                      <option value="PASS">PASS</option>
                                      <option value="FAIL">FAIL</option>
                                    </select>
                                  </label>
                                  <label className="mt-3 block text-[11px] font-bold text-muted-foreground">
                                    실제 관찰 내용
                                    <textarea
                                      value={manualDraft[key].actual}
                                      onChange={(event) => setManualDraft((current) => ({
                                        ...current,
                                        [key]: { ...current[key], actual: event.target.value },
                                      }))}
                                      rows={3}
                                      placeholder="어디를 확인했고 무엇이 보였는지 기록"
                                      className="mt-1 w-full rounded-md border bg-background p-2 text-xs leading-5 text-foreground"
                                    />
                                  </label>
                                  <label className="mt-3 block text-[11px] font-bold text-muted-foreground">
                                    판정 이유
                                    <textarea
                                      value={manualDraft[key].rationale_ko}
                                      onChange={(event) => setManualDraft((current) => ({
                                        ...current,
                                        [key]: { ...current[key], rationale_ko: event.target.value },
                                      }))}
                                      rows={3}
                                      placeholder="PASS 또는 FAIL로 판단한 구체적 이유"
                                      className="mt-1 w-full rounded-md border bg-background p-2 text-xs leading-5 text-foreground"
                                    />
                                  </label>
                                </fieldset>
                              ))}
                            </div>
                            <div className="mt-4 flex justify-end">
                              <button
                                type="button"
                                onClick={() => void submitManualReview()}
                                disabled={manualSubmitting}
                                className="inline-flex h-9 items-center gap-2 rounded-md bg-amber-700 px-4 text-xs font-black text-white hover:bg-amber-800 disabled:opacity-50"
                              >
                                {manualSubmitting && <LoaderCircle className="h-4 w-4 animate-spin" />}
                                검수 완료 · 평가 결과에 반영
                              </button>
                            </div>
                          </section>
                        )}
                      </>
                    )}

                    {selectedRun.questions.length > 0 && (
                      <section>
                        <div className="flex flex-col gap-3 border-b pb-3 sm:flex-row sm:items-center sm:justify-between">
                          <div>
                            <h3 className="text-sm font-bold">문항별 원본 결과</h3>
                            <p className="mt-1 text-xs text-muted-foreground">실패 문항을 먼저 표시하며 실제값·기준값·근거 SQL을 그대로 확인할 수 있습니다.</p>
                          </div>
                          <label className="flex items-center gap-2 text-xs font-semibold">
                            <input type="checkbox" checked={failOnly} onChange={(event) => setFailOnly(event.target.checked)} />
                            FAIL만 보기
                          </label>
                        </div>
                        <div className="mt-3 space-y-2">
                          {questions.map((question) => {
                            const manual = manualEvaluationMethods.has(question.method);
                            return (
                              <details key={question.id} className="overflow-hidden rounded-md border">
                                <summary className="flex cursor-pointer list-none items-start gap-3 p-3 hover:bg-muted/40">
                                  <span className={`mt-0.5 rounded px-1.5 py-0.5 text-[11px] font-black ${evaluationStatusClass(question.decision)}`}>{question.decision}</span>
                                  <span className="min-w-0 flex-1">
                                    <span className="block text-xs font-black">{question.id} · {question.category}{question.gate ? " · HARD GATE" : ""}</span>
                                    <span className="mt-1 block text-sm font-semibold leading-5">{question.question_ko}</span>
                                  </span>
                                  {manual && <span className="shrink-0 rounded bg-amber-100 px-2 py-1 text-[10px] font-bold text-amber-800 dark:bg-amber-950 dark:text-amber-200">수동 검수 필요</span>}
                                </summary>
                                <div className="space-y-4 border-t bg-muted/10 p-4">
                                  <p className="text-sm font-semibold leading-6">{question.rationale_ko}</p>
                                  <div className="grid gap-3 lg:grid-cols-2">
                                    <div>
                                      <p className="mb-1 text-[11px] font-bold text-muted-foreground">리포트 실제값</p>
                                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-[#101715] p-3 text-xs leading-5 text-[#d8e5e1]">{displayEvaluationValue(question.actual)}</pre>
                                    </div>
                                    <div>
                                      <p className="mb-1 text-[11px] font-bold text-muted-foreground">판정 기준값</p>
                                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 text-xs leading-5">{displayEvaluationValue(question.expected)}</pre>
                                    </div>
                                  </div>
                                  <div className="text-xs leading-5 text-muted-foreground">
                                    <p><strong>리포트 위치:</strong> {question.report_path || "-"}</p>
                                    <p><strong>DB/원천 표:</strong> {(question.source_tables || []).join(", ") || "-"}</p>
                                    <p><strong>산출물:</strong> {(question.source_artifacts || []).join(", ") || "-"}</p>
                                  </div>
                                  {(question.source_queries || []).map((sourceQuery) => (
                                    <details key={`${question.id}-${sourceQuery.id}`} className="rounded-md border bg-background">
                                      <summary className="cursor-pointer px-3 py-2 text-xs font-bold">기준 SQL · {sourceQuery.id}</summary>
                                      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words border-t p-3 text-xs leading-5">{sourceQuery.sql}{"\n\n"}params = {JSON.stringify(sourceQuery.params, null, 2)}</pre>
                                    </details>
                                  ))}
                                </div>
                              </details>
                            );
                          })}
                          {questions.length === 0 && (
                            <p className="rounded-md border p-4 text-center text-sm text-muted-foreground">선택한 조건에 해당하는 문항이 없습니다.</p>
                          )}
                        </div>
                      </section>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </section>
  );
}
