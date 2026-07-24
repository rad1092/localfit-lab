"use client";

import clsx from "clsx";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  CircleStop,
  Clock3,
  Database,
  FileClock,
  FileSearch,
  FileText,
  HardDrive,
  Layers3,
  LoaderCircle,
  Play,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  TerminalSquare,
  X,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useAdminAccess } from "@/lib/admin-auth";
import { apiUrl, fetchAuth } from "@/lib/api";
import {
  AdminAnalyticsPanel,
  AdminCommentsPanel,
  AdminErrorLogsPanel,
  AdminQualityPanel,
  AdminReportEvaluationPanel,
} from "@/components/AdminExtensions";

type Health = "healthy" | "advisory" | "warning" | "error" | "unknown" | "missing";
type JobStatus = "queued" | "running" | "cancelling" | "cancelled" | "success" | "failed" | "interrupted";
type ReasoningEffort = "none" | "low" | "medium" | "high" | "xhigh";
type FreshnessStatus = "up_to_date" | "refresh_needed" | "reconnect_needed";
type AdminTab =
  | "sources"
  | "integrations"
  | "pipeline"
  | "runs"
  | "monitoring"
  | "report-evaluation"
  | "analytics"
  | "quality"
  | "errors"
  | "comments";

interface AdminSummary {
  source_count: number;
  healthy_source_count: number;
  warning_source_count: number;
  error_source_count: number;
  credential_configured: number;
  credential_required: number;
  raw_manifest_rows: number;
  gold_file_count: number;
  news_rows: number;
  product_quarter: string | null;
  raw_data_period_start: string | null;
  raw_data_period_end: string | null;
}

interface PipelineLayer {
  key: string;
  label: string;
  status: Health;
  count: number;
  unit: string;
  updated_at: string | null;
  data_period_start?: string | null;
  data_period_end?: string | null;
  note?: string | null;
  job_key: string | null;
}

interface DataSource {
  source_id: string;
  provider: string;
  dataset_name: string;
  priority: string;
  registry_status: string;
  collection_method: string;
  credential_status: "configured" | "missing" | "not_required" | "optional" | "unknown";
  engine_role: string;
  preprocessing_status: string;
  health: Health;
  manifest_rows: number;
  failure_rows: number;
  last_status: string | null;
  last_collected_at: string | null;
  last_http_status: string | null;
  last_data_period_start: string | null;
  last_data_period_end: string | null;
  retained_data_period_start: string | null;
  retained_data_period_end: string | null;
  content_version_date: string | null;
  latest_snapshot_date: string | null;
  last_checked_at: string | null;
  last_full_collection_at: string | null;
  last_full_collection_age_hours: number | null;
  sampled_skip_ttl_hours: number | null;
  sample_count: number | null;
  probe_status: string | null;
  last_change_status: string | null;
  last_content_fingerprint: string | null;
  refresh_job_key: string | null;
  refresh_available: boolean;
  product_role?: string | null;
  product_lineage_status?: Health | null;
  product_artifact_updated_at?: string | null;
  product_artifact_oldest_updated_at?: string | null;
  product_data_period_start?: string | null;
  product_data_period_end?: string | null;
  included_in_product_refresh?: boolean;
  product_refresh_note?: string | null;
  product_artifacts?: string[];
}

interface JobDefinition {
  key: string;
  label: string;
  description: string;
  group: "collection" | "pipeline" | "system";
  estimate: string;
  risk: "normal" | "caution" | "high";
  requires_confirmation: boolean;
  step_count: number;
  source_ids: string[];
  enabled: boolean;
  output_scope: "raw_only" | "core_product_chain" | "pipeline_stage" | "status_only";
  updates_product: boolean;
  scope_note: string;
}

interface PipelineStep {
  step_index: number;
  label: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled" | "skipped_checkpoint" | "skipped_dependency";
  current_units: number;
  total_units: number;
  unit: string | null;
  eta_seconds: number | null;
  message: string | null;
  reused_from_job_id: number | null;
}

interface FreshnessServiceResult {
  source_id: string;
  source_label: string;
  service: string;
  dataset_name: string;
  status: FreshnessStatus;
  reason: string;
  http_status: number | null;
  provider_result_code: string | null;
  probe_status: string | null;
  samples_match: boolean;
  sample_count: number;
  total_count: number | null;
  data_period_start: string | null;
  data_period_end: string | null;
  last_full_collection_at: string | null;
  response_time_ms: number;
}

interface FreshnessSourceResult {
  source_id: string;
  label: string;
  status: FreshnessStatus;
  data_period_end: string | null;
  services: FreshnessServiceResult[];
}

interface CoreSourceFreshnessSummary {
  schema_version: "core_source_freshness.v1";
  job_id: number | null;
  checked_at: string;
  duration_seconds: number;
  overall_status: FreshnessStatus;
  decision: string;
  source_count: number;
  service_count: number;
  connected_service_count: number;
  up_to_date_source_count: number;
  refresh_needed_sources: string[];
  reconnect_needed_sources: string[];
  latest_provider_period: string | null;
  product_refresh_needed: boolean;
  database: {
    status: "healthy" | "refresh_needed" | "missing";
    quick_check: string | null;
    quarter: string | null;
    score_version: string | null;
    table_counts: Record<string, number>;
    reason: string;
  };
  sources: FreshnessSourceResult[];
}

interface PipelineJob {
  id: number;
  job_key: string;
  label: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  pid: number | null;
  current_step: string | null;
  step_count: number;
  completed_steps: number;
  skipped_steps: number;
  current_units: number;
  total_units: number;
  current_unit: string | null;
  eta_seconds: number | null;
  data_period_start: string | null;
  data_period_end: string | null;
  resumed_from_job_id: number | null;
  steps?: PipelineStep[];
  message: string | null;
  duration_seconds: number | null;
  is_active: boolean;
  change_summary?: CoreSourceFreshnessSummary | Record<string, unknown> | null;
}

interface JobDetail extends PipelineJob {
  log: string;
}

interface UsageBreakdown {
  calls: number;
  total_tokens: number;
  estimated_cost: number;
}

interface TokenUsageLog {
  id: number;
  user_id: number | null;
  model_name: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  feature_name: string;
  status: "success" | "degraded" | "failed";
  reasoning_effort: ReasoningEffort | null;
  generation_mode: "llm" | "partial_fallback" | "deterministic" | null;
  quality_status: string | null;
  original_validation_issues: string[];
  error_type: string | null;
  error_message: string | null;
  created_at: string | null;
}

interface ExternalApiCall {
  id: string;
  api_name: string;
  endpoint: string;
  status_code: number;
  response_time_ms: number | null;
  call_type: string;
  created_at: string | null;
  origin?: "runtime" | "pipeline" | "client_observation";
}

interface ReportAIConfig {
  configured_model: string;
  reasoning_effort: ReasoningEffort;
  supported_reasoning_efforts: ReasoningEffort[];
  source: "admin" | "environment" | "default";
  updated_at: string | null;
}

interface TokenUsage {
  report_ai_config: ReportAIConfig;
  summary: {
    total_calls: number;
    successful_calls: number;
    degraded_calls: number;
    failed_calls: number;
    total_prompt_tokens: number;
    total_completion_tokens: number;
    total_tokens: number;
    total_cost: number;
    chatbot_cost: number;
    report_cost: number;
  };
  model_breakdown: Record<string, UsageBreakdown>;
  feature_breakdown: Record<string, UsageBreakdown>;
  logs: TokenUsageLog[];
  external_api: {
    summary: {
      total_calls: number;
      kakao_calls: number;
      naver_calls: number;
      open_data_calls: number;
      success_rate: number;
      error_count: number;
    };
    logs: ExternalApiCall[];
  };
}

interface ObservedModel {
  model_name: string;
  call_count: number;
  last_used_at: string | null;
}

interface RuntimeIntegration {
  integration_id: string;
  label: string;
  status: Health;
  configured: boolean;
  configured_model: string | null;
  report_reasoning_effort?: ReasoningEffort;
  observed_models: ObservedModel[];
  call_count: number;
  last_activity_at: string | null;
  status_note: string;
  client_observation?: {
    status: "success_observed" | "failure_observed" | "none";
    success_count: number;
    failure_count: number;
    last_success_at: string | null;
    last_event_at: string | null;
  };
}

interface ProviderIntegration {
  provider_id: string;
  provider: string;
  source_count: number;
  credential_status: DataSource["credential_status"];
  credential_configured: number;
  credential_required: number;
  health: Health;
  failure_rows: number;
  last_collected_at: string | null;
  refresh_available_count: number;
}

interface ExternalIntegrations {
  generated_at: string;
  summary: {
    provider_count: number;
    source_count: number;
    credential_configured: number;
    credential_required: number;
    warning_provider_count: number;
    error_provider_count: number;
    total_call_count: number;
    success_rate: number;
  };
  report_ai_config: ReportAIConfig;
  runtime_integrations: RuntimeIntegration[];
  providers: ProviderIntegration[];
  recent_calls: ExternalApiCall[];
}

interface AdminDashboard {
  generated_at: string;
  summary: AdminSummary;
  layers: PipelineLayer[];
  sources: DataSource[];
  database: {
    exists: boolean;
    bytes: number;
    updated_at: string | null;
    quarter: string | null;
    table_counts: Record<string, number>;
    status: Health;
    contract_note?: string | null;
    postcondition?: Record<string, unknown> | null;
  };
  score_batch: {
    exists: boolean;
    selected: boolean;
    status: Health;
    name: string | null;
    updated_at: string | null;
    bytes: number;
    score_version: string | null;
    gold_release_id: string | null;
    analysis_quarter?: string | null;
    reason: string;
  };
  news: {
    rows: number;
    updated_at: string | null;
    status: Health;
  };
  job_definitions: JobDefinition[];
  active_job: PipelineJob | null;
  recent_jobs: PipelineJob[];
  latest_data_check?: PipelineJob | null;
}

const healthMeta: Record<Health, { label: string; className: string; dot: string }> = {
  healthy: {
    label: "정상",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  advisory: {
    label: "별도 관리",
    className: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-300",
    dot: "bg-sky-500",
  },
  warning: {
    label: "확인 필요",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  error: {
    label: "오류",
    className: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    dot: "bg-red-500",
  },
  unknown: {
    label: "미확인",
    className: "border-border bg-muted text-muted-foreground",
    dot: "bg-muted-foreground",
  },
  missing: {
    label: "없음",
    className: "border-border bg-muted text-muted-foreground",
    dot: "bg-muted-foreground",
  },
};

const freshnessStatusMeta: Record<FreshnessStatus, { label: string; className: string; dot: string }> = {
  up_to_date: {
    label: "그대로 사용",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    dot: "bg-emerald-500",
  },
  refresh_needed: {
    label: "재수집 필요",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    dot: "bg-amber-500",
  },
  reconnect_needed: {
    label: "재연결 필요",
    className: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    dot: "bg-red-500",
  },
};

const jobMeta: Record<JobStatus, { label: string; className: string; icon: typeof CheckCircle2 }> = {
  queued: {
    label: "대기",
    className: "border-sky-200 bg-sky-50 text-sky-700 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-300",
    icon: Clock3,
  },
  running: {
    label: "실행 중",
    className: "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-800 dark:bg-blue-950/40 dark:text-blue-300",
    icon: LoaderCircle,
  },
  cancelling: {
    label: "중지 중",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    icon: LoaderCircle,
  },
  cancelled: {
    label: "중지됨",
    className: "border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900/50 dark:text-slate-300",
    icon: CircleStop,
  },
  success: {
    label: "완료",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300",
    icon: CheckCircle2,
  },
  failed: {
    label: "실패",
    className: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
    icon: XCircle,
  },
  interrupted: {
    label: "중단",
    className: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
    icon: AlertTriangle,
  },
};

const roleLabels: Record<string, string> = {
  direct_score_input: "점수 입력",
  evidence_candidate: "후보 근거",
  evidence_only: "정성 근거",
  input_resolver: "입력 연결",
  docs_only: "문서 근거",
  blocked: "보류",
  unclassified: "미분류",
};

const credentialLabels: Record<DataSource["credential_status"], string> = {
  configured: "설정됨",
  missing: "누락",
  not_required: "불필요",
  optional: "선택",
  unknown: "미확인",
};

function formatNumber(value: number | null | undefined) {
  return new Intl.NumberFormat("ko-KR").format(value || 0);
}

function formatDate(value: string | null | undefined, compact = false) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    year: compact ? undefined : "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function formatDuration(seconds: number | null) {
  if (seconds === null) return "-";
  if (seconds < 60) return `${Math.round(seconds)}초`;
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.round(seconds % 60);
  return `${minutes}분 ${remainder}초`;
}

function formatEta(seconds: number | null) {
  if (seconds === null || seconds <= 0) return null;
  if (seconds < 60) return `약 ${Math.ceil(seconds)}초 남음`;
  const minutes = Math.ceil(seconds / 60);
  return `약 ${minutes}분 남음`;
}

function formatDataPeriod(value: string | null | undefined) {
  if (!value) return "-";
  if (/^\d{5}$/.test(value)) return `${value.slice(0, 4)}년 ${value.slice(4)}분기`;
  if (/^\d{6}$/.test(value)) return `${value.slice(0, 4)}-${value.slice(4)}`;
  return value;
}

function reportGenerationModeLabel(mode: TokenUsageLog["generation_mode"]) {
  const labels: Record<string, string> = {
    llm: "AI 해석",
    partial_fallback: "AI + 일부 보정",
    deterministic: "규칙 기반",
  };
  return (mode && labels[mode]) || "-";
}

function jobProgress(job: PipelineJob) {
  if (job.status === "success") return 100;
  const unitFraction = job.total_units > 0
    ? Math.min(1, Math.max(0, job.current_units / job.total_units))
    : 0;
  const currentFraction = job.completed_steps < job.step_count ? unitFraction : 0;
  return Math.min(100, Math.max(0, ((job.completed_steps + currentFraction) / Math.max(1, job.step_count)) * 100));
}

function productQuarter(value: string | null) {
  if (!value || value.length < 5) return value || "-";
  return `${value.slice(0, 4)}년 ${value.slice(4)}분기`;
}

function jobActionLabel(job: PipelineJob | undefined) {
  if (!job) return "처음 실행";
  if (job.status === "success") return "다시 실행";
  if (["failed", "interrupted", "cancelled"].includes(job.status)) return "재시도";
  return "실행";
}

function formatExitCode(exitCode: number | null) {
  if (exitCode === null) return "-";
  if (exitCode === 0) return "0 · 정상 종료";
  return `${exitCode} · 해당 단계 실패`;
}

function sourceNeedsAttention(source: DataSource) {
  return ["warning", "error", "unknown", "missing"].includes(source.health)
    || source.credential_status === "missing"
    || Boolean(source.product_lineage_status && ["warning", "error", "missing"].includes(source.product_lineage_status));
}

function productLineageLabel(source: DataSource) {
  if (source.included_in_product_refresh === true) return "제품 갱신 포함";
  if (source.included_in_product_refresh === false) return "별도 반영";
  return "제품 연결";
}

function externalCallOriginLabel(origin: ExternalApiCall["origin"]) {
  if (origin === "runtime") return "런타임";
  if (origin === "client_observation") return "브라우저 관측";
  return "수집";
}

const reasoningEffortMeta: Record<ReasoningEffort, { label: string; description: string }> = {
  none: { label: "없음", description: "가장 빠르고 저렴" },
  low: { label: "낮음", description: "상세 리포트 기본값" },
  medium: { label: "중간", description: "품질 우선" },
  high: { label: "높음", description: "시간·비용 증가" },
  xhigh: { label: "매우 높음", description: "최대 추론" },
};

function HealthBadge({ health }: { health: Health }) {
  const meta = healthMeta[health];
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold", meta.className)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </span>
  );
}

function JobBadge({ status }: { status: JobStatus }) {
  const meta = jobMeta[status];
  const Icon = meta.icon;
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold", meta.className)}>
      <Icon
        className={clsx(
          "h-3.5 w-3.5",
          (status === "running" || status === "cancelling") && "animate-spin"
        )}
      />
      {meta.label}
    </span>
  );
}

function asCoreSourceFreshnessSummary(value: unknown): CoreSourceFreshnessSummary | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Partial<CoreSourceFreshnessSummary>;
  if (
    candidate.schema_version !== "core_source_freshness.v1"
    || !Array.isArray(candidate.sources)
    || !candidate.database
  ) return null;
  return candidate as CoreSourceFreshnessSummary;
}

function FreshnessBadge({ status }: { status: FreshnessStatus }) {
  const meta = freshnessStatusMeta[status];
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-semibold", meta.className)}>
      <span className={clsx("h-1.5 w-1.5 rounded-full", meta.dot)} />
      {meta.label}
    </span>
  );
}

function sourceFreshnessReason(source: FreshnessSourceResult) {
  const reasons = source.services
    .filter((service) => service.status === source.status)
    .map((service) => service.reason)
    .filter(Boolean);
  return Array.from(new Set(reasons)).join(" · ") || "점검 결과 상세 사유가 없습니다.";
}

function FreshnessResultPanel({
  summary,
  title,
  onOpenLog,
}: {
  summary: CoreSourceFreshnessSummary;
  title: string;
  onOpenLog?: () => void;
}) {
  const databaseHealthy = summary.database.status === "healthy";
  const refreshNeededCount = summary.refresh_needed_sources.length;
  const reconnectNeededCount = summary.reconnect_needed_sources.length;

  return (
    <section className="overflow-hidden rounded-lg border bg-card">
      <header className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-muted-foreground">{title}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <FreshnessBadge status={summary.overall_status} />
            <p className="text-sm font-bold">{summary.decision}</p>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            점검 {formatDate(summary.checked_at)} · {formatDuration(summary.duration_seconds)} · 외부 최신 {formatDataPeriod(summary.latest_provider_period)}
          </p>
        </div>
        {onOpenLog && (
          <button
            type="button"
            onClick={onOpenLog}
            className="inline-flex h-8 shrink-0 items-center justify-center gap-1.5 rounded-md border px-2.5 text-xs font-semibold hover:bg-muted"
          >
            <TerminalSquare className="h-3.5 w-3.5" />
            실행 기록
          </button>
        )}
      </header>

      <div className="grid grid-cols-2 gap-px bg-border sm:grid-cols-4">
        {[
          { label: "그대로 사용", value: summary.up_to_date_source_count, className: "text-emerald-700 dark:text-emerald-300" },
          { label: "재수집 필요", value: refreshNeededCount, className: "text-amber-700 dark:text-amber-300" },
          { label: "재연결 필요", value: reconnectNeededCount, className: "text-red-700 dark:text-red-300" },
        ].map((item) => (
          <div key={item.label} className="bg-card px-4 py-3">
            <p className="text-[11px] font-semibold text-muted-foreground">{item.label}</p>
            <p className={clsx("mt-1 text-lg font-black", item.className)}>{formatNumber(item.value)}개</p>
          </div>
        ))}
        <div className="bg-card px-4 py-3">
          <p className="text-[11px] font-semibold text-muted-foreground">제품 DB</p>
          <p className={clsx("mt-1 text-sm font-black", databaseHealthy ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300")}>
            {databaseHealthy ? "정상" : "확인 필요"} · {formatDataPeriod(summary.database.quarter)}
          </p>
          <p className="mt-1 truncate text-[11px] text-muted-foreground" title={summary.database.reason}>
            quick_check {summary.database.quick_check || "-"}
          </p>
        </div>
      </div>

      <details className="border-t">
        <summary className="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 text-sm font-bold hover:bg-muted/50">
          핵심 {summary.source_count}개 원천 결과
          <span className="text-xs font-normal text-muted-foreground">접어서 보기</span>
        </summary>
        <div className="divide-y border-t">
          {summary.sources.map((source) => (
            <div key={source.source_id} className="grid gap-2 px-4 py-3 text-xs sm:grid-cols-[150px_110px_minmax(0,1fr)] sm:items-start">
              <div className="min-w-0">
                <p className="font-bold">{source.label}</p>
                <p className="mt-0.5 truncate text-[11px] text-muted-foreground" title={source.source_id}>{source.source_id}</p>
              </div>
              <div><FreshnessBadge status={source.status} /></div>
              <div className="min-w-0 text-muted-foreground">
                <p className="font-semibold text-foreground">제공 {formatDataPeriod(source.data_period_end)}</p>
                <p className="mt-1 leading-5">{sourceFreshnessReason(source)}</p>
                <p className="mt-1 text-[11px]">외부 서비스 {source.services.length}개 확인</p>
              </div>
            </div>
          ))}
        </div>
      </details>
    </section>
  );
}

export default function AdminDataPage() {
  const router = useRouter();
  const adminAccess = useAdminAccess();
  const [dashboard, setDashboard] = useState<AdminDashboard | null>(null);
  const [tab, setTab] = useState<AdminTab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollingError, setPollingError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [healthFilter, setHealthFilter] = useState("all");
  const [providerFilter, setProviderFilter] = useState("all");
  const [pendingJob, setPendingJob] = useState<JobDefinition | null>(null);
  const [startingKey, setStartingKey] = useState<string | null>(null);
  const [cancellingJobId, setCancellingJobId] = useState<number | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const selectedJobId = selectedJob?.id ?? null;
  const [jobDetailLoading, setJobDetailLoading] = useState(false);
  const [tokenUsage, setTokenUsage] = useState<TokenUsage | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [integrations, setIntegrations] = useState<ExternalIntegrations | null>(null);
  const [integrationLoading, setIntegrationLoading] = useState(false);
  const [checkingNaverConnection, setCheckingNaverConnection] = useState(false);
  const [reasoningEffortDraft, setReasoningEffortDraft] = useState<ReasoningEffort>("low");
  const [savingReasoningEffort, setSavingReasoningEffort] = useState(false);
  const [extensionRefreshToken, setExtensionRefreshToken] = useState(0);

  useEffect(() => {
    if (adminAccess === "anonymous") router.replace("/login");
    if (adminAccess === "denied") router.replace("/");
  }, [adminAccess, router]);

  const loadDashboard = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const response = await fetchAuth(apiUrl("/admin/dashboard"), { cache: "no-store" });
      if (!response.ok) throw new Error(`관리자 상태를 불러오지 못했습니다. (${response.status})`);
      const data = (await response.json()) as AdminDashboard;
      setDashboard(data);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "관리자 상태를 불러오지 못했습니다.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  const loadJobDetail = useCallback(async (jobId: number) => {
    setJobDetailLoading(true);
    try {
      const response = await fetchAuth(apiUrl(`/admin/jobs/${jobId}`), { cache: "no-store" });
      if (!response.ok) throw new Error("실행 로그를 불러오지 못했습니다.");
      setSelectedJob((await response.json()) as JobDetail);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "실행 로그를 불러오지 못했습니다.");
    } finally {
      setJobDetailLoading(false);
    }
  }, []);

  const loadJobStatus = useCallback(async (jobId: number) => {
    try {
      const response = await fetchAuth(apiUrl(`/admin/jobs/${jobId}/status`), { cache: "no-store" });
      if (!response.ok) throw new Error("실행 상태를 불러오지 못했습니다.");
      const job = (await response.json()) as PipelineJob;
      setPollingError(null);
      setDashboard((current) => {
        if (!current) return current;
        const recentJobs = current.recent_jobs.some((item) => item.id === job.id)
          ? current.recent_jobs.map((item) => item.id === job.id ? job : item)
          : [job, ...current.recent_jobs].slice(0, 20);
        return {
          ...current,
          active_job: job.is_active ? job : null,
          recent_jobs: recentJobs,
        };
      });
      setSelectedJob((current) => current?.id === job.id ? { ...current, ...job } : current);
      if (!job.is_active) {
        setNotice(
          job.status === "success"
            ? `${job.label} 작업이 완료되었습니다.`
            : `${job.label} 작업이 ${jobMeta[job.status].label} 상태로 종료되었습니다.`
        );
        await loadDashboard(true);
        if (selectedJobId === job.id) await loadJobDetail(job.id);
      }
    } catch (reason) {
      setPollingError(reason instanceof Error ? reason.message : "실행 상태를 불러오지 못했습니다.");
    }
  }, [loadDashboard, loadJobDetail, selectedJobId]);

  const loadTokenUsage = useCallback(async (silent = false) => {
    if (!silent) setTokenLoading(true);
    try {
      const response = await fetchAuth(apiUrl("/admin/token-usage"), { cache: "no-store" });
      if (!response.ok) throw new Error("비용 모니터링 데이터를 불러오지 못했습니다.");
      const data = (await response.json()) as TokenUsage;
      setTokenUsage(data);
      if (!silent) setReasoningEffortDraft(data.report_ai_config.reasoning_effort);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "비용 모니터링 데이터를 불러오지 못했습니다.");
    } finally {
      if (!silent) setTokenLoading(false);
    }
  }, []);

  const loadIntegrations = useCallback(async () => {
    setIntegrationLoading(true);
    try {
      const response = await fetchAuth(apiUrl("/admin/integrations"), { cache: "no-store" });
      if (!response.ok) throw new Error("외부 연결 상태를 불러오지 못했습니다.");
      const data = (await response.json()) as ExternalIntegrations;
      setIntegrations(data);
      setReasoningEffortDraft(data.report_ai_config.reasoning_effort);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "외부 연결 상태를 불러오지 못했습니다.");
    } finally {
      setIntegrationLoading(false);
    }
  }, []);

  const checkNaverConnection = useCallback(async () => {
    setCheckingNaverConnection(true);
    setError(null);
    try {
      const response = await fetchAuth(apiUrl("/admin/integrations/naver-news/check"), {
        method: "POST",
      });
      const payload = (await response.json().catch(() => ({}))) as {
        detail?: string;
        response_time_ms?: number;
      };
      if (!response.ok) {
        throw new Error(payload.detail || "NAVER 뉴스 연결을 확인하지 못했습니다.");
      }
      setNotice(`NAVER 뉴스 연결이 정상입니다${payload.response_time_ms ? ` · ${payload.response_time_ms}ms` : ""}.`);
      await loadIntegrations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "NAVER 뉴스 연결을 확인하지 못했습니다.");
      await loadIntegrations();
    } finally {
      setCheckingNaverConnection(false);
    }
  }, [loadIntegrations]);

  const saveReportReasoningEffort = useCallback(async () => {
    setSavingReasoningEffort(true);
    setError(null);
    try {
      const response = await fetchAuth(apiUrl("/admin/integrations/openai/report-settings"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reasoning_effort: reasoningEffortDraft }),
      });
      const rawPayload = (await response.json().catch(() => ({}))) as {
        detail?: unknown;
      } & Partial<ReportAIConfig>;
      if (!response.ok) {
        throw new Error(
          typeof rawPayload.detail === "string"
            ? rawPayload.detail
            : "상세 리포트 추론 설정을 저장하지 못했습니다.",
        );
      }
      const payload = rawPayload as ReportAIConfig;
      setReasoningEffortDraft(payload.reasoning_effort);
      setTokenUsage((current) => current ? { ...current, report_ai_config: payload } : current);
      setIntegrations((current) => current ? {
        ...current,
        report_ai_config: payload,
        runtime_integrations: current.runtime_integrations.map((integration) => (
          integration.integration_id === "openai"
            ? { ...integration, report_reasoning_effort: payload.reasoning_effort }
            : integration
        )),
      } : current);
      setNotice(`상세 리포트 추론 강도를 ${reasoningEffortMeta[payload.reasoning_effort].label}(으)로 변경했습니다.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "상세 리포트 추론 설정을 저장하지 못했습니다.");
    } finally {
      setSavingReasoningEffort(false);
    }
  }, [reasoningEffortDraft]);

  const refreshCurrentView = useCallback(async () => {
    if (tab === "integrations") {
      await Promise.all([loadDashboard(true), loadIntegrations()]);
      return;
    }
    if (tab === "monitoring") {
      await Promise.all([loadDashboard(true), loadTokenUsage()]);
      return;
    }
    if (["report-evaluation", "analytics", "quality", "errors", "comments"].includes(tab)) {
      setExtensionRefreshToken((current) => current + 1);
      await loadDashboard(true);
      return;
    }
    await loadDashboard(true);
  }, [loadDashboard, loadIntegrations, loadTokenUsage, tab]);

  const selectTab = useCallback((nextTab: AdminTab) => {
    setTab(nextTab);
    router.replace(`/admin?tab=${nextTab}`, { scroll: false });
    if (nextTab === "monitoring") void loadTokenUsage();
    if (nextTab === "integrations") void loadIntegrations();
  }, [loadIntegrations, loadTokenUsage, router]);

  useEffect(() => {
    if (adminAccess !== "allowed") return;
    const requestedTab = new URLSearchParams(window.location.search).get("tab");
    if (!requestedTab || !["sources", "integrations", "pipeline", "runs", "monitoring", "report-evaluation", "analytics", "quality", "errors", "comments"].includes(requestedTab)) return;
    const timer = window.setTimeout(() => selectTab(requestedTab as AdminTab), 0);
    return () => window.clearTimeout(timer);
  }, [adminAccess, selectTab]);

  useEffect(() => {
    if (adminAccess !== "allowed") return;
    const timer = window.setTimeout(() => void loadDashboard(), 0);
    return () => window.clearTimeout(timer);
  }, [adminAccess, loadDashboard]);

  useEffect(() => {
    const activeJobId = dashboard?.active_job?.id;
    if (!activeJobId) return;
    const timer = window.setInterval(() => {
      void loadJobStatus(activeJobId);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [dashboard?.active_job?.id, loadJobStatus]);

  useEffect(() => {
    if (adminAccess !== "allowed" || tab !== "monitoring") return;
    const timer = window.setInterval(() => {
      if (!document.hidden) void loadTokenUsage(true);
    }, 10_000);
    return () => window.clearInterval(timer);
  }, [adminAccess, loadTokenUsage, tab]);

  const definitions = useMemo(
    () => new Map((dashboard?.job_definitions || []).map((item) => [item.key, item])),
    [dashboard?.job_definitions]
  );

  const visibleRecentJobs = useMemo(
    () => (dashboard?.recent_jobs || []).filter((job) => definitions.has(job.job_key)),
    [dashboard?.recent_jobs, definitions]
  );

  const providers = useMemo(
    () => Array.from(new Set((dashboard?.sources || []).map((source) => source.provider))).sort((a, b) => a.localeCompare(b, "ko")),
    [dashboard?.sources]
  );

  const filteredSources = useMemo(() => {
    const keyword = query.trim().toLocaleLowerCase("ko-KR");
    return (dashboard?.sources || []).filter((source) => {
      const matchesQuery =
        !keyword ||
        source.dataset_name.toLocaleLowerCase("ko-KR").includes(keyword) ||
        source.provider.toLocaleLowerCase("ko-KR").includes(keyword) ||
        source.source_id.toLowerCase().includes(keyword);
      const matchesHealth =
        healthFilter === "all" ||
        (healthFilter === "attention"
          ? sourceNeedsAttention(source)
          : source.health === healthFilter);
      const matchesProvider = providerFilter === "all" || source.provider === providerFilter;
      return matchesQuery && matchesHealth && matchesProvider;
    });
  }, [dashboard?.sources, healthFilter, providerFilter, query]);

  const attentionSources = useMemo(() => {
    const priority = (source: DataSource) => {
      if (source.health === "error" || source.credential_status === "missing" || source.product_lineage_status === "error" || source.product_lineage_status === "missing") return 0;
      if (source.health === "warning" || source.product_lineage_status === "warning") return 1;
      return 2;
    };
    return (dashboard?.sources || [])
      .filter(sourceNeedsAttention)
      .sort((left, right) => priority(left) - priority(right))
      .slice(0, 3);
  }, [dashboard?.sources]);

  const recentProblemJob = useMemo(
    () => {
      const seenJobKeys = new Set<string>();
      return visibleRecentJobs.find((job) => {
        if (seenJobKeys.has(job.job_key)) return false;
        seenJobKeys.add(job.job_key);
        return ["failed", "interrupted", "cancelled"].includes(job.status);
      });
    },
    [visibleRecentJobs]
  );

  const latestJobByKey = useMemo(() => {
    const jobs = new Map<string, PipelineJob>();
    for (const job of visibleRecentJobs) {
      if (!jobs.has(job.job_key)) jobs.set(job.job_key, job);
    }
    return jobs;
  }, [visibleRecentJobs]);

  const recoveredByJobId = useMemo(() => {
    const latestSuccessByKey = new Map<string, PipelineJob>();
    const recovered = new Map<number, PipelineJob>();
    for (const job of visibleRecentJobs) {
      if (job.status === "success" && !latestSuccessByKey.has(job.job_key)) {
        latestSuccessByKey.set(job.job_key, job);
        continue;
      }
      if (["failed", "interrupted", "cancelled"].includes(job.status)) {
        const laterSuccess = latestSuccessByKey.get(job.job_key);
        if (laterSuccess) recovered.set(job.id, laterSuccess);
      }
    }
    return recovered;
  }, [visibleRecentJobs]);

  const latestPipelineJob = useMemo(
    () => visibleRecentJobs.find((job) => job.job_key === "refresh_product_data"),
    [visibleRecentJobs]
  );

  const executeJob = useCallback(
    async (definition: JobDefinition, confirmed: boolean) => {
      setStartingKey(definition.key);
      setNotice(null);
      try {
        const response = await fetchAuth(apiUrl(`/admin/jobs/${definition.key}`), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirmed }),
        });
        const payload = (await response.json()) as PipelineJob | { detail?: string | { message?: string } };
        if (!response.ok) {
          const detail = "detail" in payload ? payload.detail : null;
          const message = typeof detail === "string" ? detail : detail?.message;
          throw new Error(message || "작업을 시작하지 못했습니다.");
        }
        const job = payload as PipelineJob;
        setNotice(`${job.label} 작업을 시작했습니다.`);
        selectTab("runs");
        await loadDashboard(true);
        await loadJobDetail(job.id);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "작업을 시작하지 못했습니다.");
      } finally {
        setPendingJob(null);
        setStartingKey(null);
      }
    },
    [loadDashboard, loadJobDetail, selectTab]
  );

  const cancelRunningJob = useCallback(async (jobId: number) => {
    setCancellingJobId(jobId);
    try {
      const response = await fetchAuth(apiUrl(`/admin/jobs/${jobId}/cancel`), { method: "POST" });
      const payload = (await response.json()) as PipelineJob | { detail?: string };
      if (!response.ok) {
        throw new Error("detail" in payload && payload.detail ? payload.detail : "작업을 중지하지 못했습니다.");
      }
      setNotice("작업 중지를 요청했습니다.");
      await loadDashboard(true);
      await loadJobDetail(jobId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "작업을 중지하지 못했습니다.");
    } finally {
      setCancellingJobId(null);
    }
  }, [loadDashboard, loadJobDetail]);

  const requestJob = useCallback(
    (jobKey: string | null) => {
      if (!jobKey) return;
      const definition = definitions.get(jobKey);
      if (!definition || !definition.enabled) {
        setError("현재 실행할 수 없는 작업입니다.");
        return;
      }
      if (definition.requires_confirmation) setPendingJob(definition);
      else void executeJob(definition, false);
    },
    [definitions, executeJob]
  );

  const openSource = useCallback((source: DataSource) => {
    setQuery(source.source_id);
    setHealthFilter("all");
    setProviderFilter(source.provider);
    selectTab("sources");
  }, [selectTab]);

  const openAttentionSources = useCallback(() => {
    setQuery("");
    setHealthFilter("attention");
    setProviderFilter("all");
    selectTab("sources");
  }, [selectTab]);

  const openSourcesForProvider = useCallback((provider: string) => {
    setQuery("");
    setHealthFilter("all");
    setProviderFilter(provider);
    selectTab("sources");
  }, [selectTab]);

  const openJobDetail = useCallback((jobId: number) => {
    selectTab("runs");
    void loadJobDetail(jobId);
  }, [loadJobDetail, selectTab]);

  if (adminAccess !== "allowed" || (loading && !dashboard)) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center" role="status">
        <LoaderCircle className="h-6 w-6 animate-spin text-primary" />
      </div>
    );
  }

  const summary = dashboard?.summary;
  const activeJob = dashboard?.active_job;
  const pipelineDefinitions = (dashboard?.job_definitions || []).filter((item) => item.group === "pipeline");
  const layerJobKeys = new Set((dashboard?.layers || []).flatMap((layer) => layer.job_key ? [layer.job_key] : []));
  const supplementalPipelineDefinitions = pipelineDefinitions.filter((item) => !layerJobKeys.has(item.key));
  const activePipelineJob = activeJob && definitions.get(activeJob.job_key)?.group === "pipeline" ? activeJob : null;
  const pipelineStatusJob = activePipelineJob || latestPipelineJob;
  const latestDataCheck = dashboard?.latest_data_check
    || visibleRecentJobs.find((job) => job.job_key === "status_check")
    || null;
  const latestDataCheckSummary = asCoreSourceFreshnessSummary(latestDataCheck?.change_summary);
  const selectedDataCheckSummary = asCoreSourceFreshnessSummary(selectedJob?.change_summary);
  const readyLayerCount = (dashboard?.layers || []).filter((layer) => ["healthy", "advisory"].includes(layer.status)).length;
  const pipelineRunnable = pipelineDefinitions.length > 0 && pipelineDefinitions.every((definition) => definition.enabled);
  const currentViewRefreshing = refreshing || (tab === "integrations" && integrationLoading) || (tab === "monitoring" && tokenLoading);

  return (
    <div className="mx-auto w-full max-w-[1580px] space-y-6">
      <header className="flex flex-col gap-4 border-b pb-5 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">데이터 파이프라인</h1>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void refreshCurrentView()}
            disabled={currentViewRefreshing}
            title="현재 화면 새로고침"
            aria-label="현재 화면 새로고침"
            className="inline-flex h-10 items-center gap-2 rounded-md border bg-card px-3 text-sm font-semibold transition-colors hover:bg-muted disabled:opacity-50"
          >
            <RefreshCw className={clsx("h-4 w-4", currentViewRefreshing && "animate-spin")} />
            현재 화면 새로고침
          </button>
        </div>
      </header>

      {error && (
        <div className="flex items-start justify-between gap-3 border-l-4 border-red-500 bg-red-50 px-4 py-3 text-sm text-red-800 dark:bg-red-950/30 dark:text-red-200" role="alert">
          <div className="flex items-start gap-2">
            <CircleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button type="button" onClick={() => setError(null)} title="닫기" aria-label="오류 닫기">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {pollingError && !error && (
        <div className="flex items-start justify-between gap-3 border-l-4 border-amber-500 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:bg-amber-950/30 dark:text-amber-200" role="status">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{pollingError} 다음 상태 확인에서 자동으로 다시 시도합니다.</span>
          </div>
          <button type="button" onClick={() => setPollingError(null)} title="닫기" aria-label="상태 확인 오류 닫기">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {notice && (
        <div className="flex items-center justify-between gap-3 border-l-4 border-primary bg-accent px-4 py-3 text-sm text-accent-foreground" aria-live="polite">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="h-4 w-4" />
            <span>{notice}</span>
          </div>
          <button type="button" onClick={() => setNotice(null)} title="닫기" aria-label="알림 닫기">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {activeJob && (
        <section className="border-y bg-card px-4 py-4" aria-live="polite">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 items-center gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300">
                <LoaderCircle className="h-4 w-4 animate-spin" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-sm font-bold">{activeJob.label}</p>
                <p className="truncate text-xs text-muted-foreground">{activeJob.current_step || activeJob.message}</p>
                {activeJob.total_units > 0 && (
                  <p className="mt-1 text-xs font-medium text-blue-700 dark:text-blue-300">
                    {formatNumber(activeJob.current_units)}/{formatNumber(activeJob.total_units)} {activeJob.current_unit || "건"}
                    {formatEta(activeJob.eta_seconds) ? ` · ${formatEta(activeJob.eta_seconds)}` : ""}
                  </p>
                )}
                {activeJob.data_period_end && (
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    원천 기준기간 {formatDataPeriod(activeJob.data_period_start)}~{formatDataPeriod(activeJob.data_period_end)}
                  </p>
                )}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold text-muted-foreground">
                {activeJob.completed_steps}/{activeJob.step_count} 단계 · {jobProgress(activeJob).toFixed(1)}%
              </span>
              <button
                type="button"
                onClick={() => openJobDetail(activeJob.id)}
                className="inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-xs font-semibold hover:bg-muted"
              >
                <TerminalSquare className="h-3.5 w-3.5" />
                로그
              </button>
              <button
                type="button"
                onClick={() => void cancelRunningJob(activeJob.id)}
                disabled={activeJob.status === "cancelling" || cancellingJobId === activeJob.id}
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-red-200 px-3 text-xs font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
              >
                {cancellingJobId === activeJob.id ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <CircleStop className="h-3.5 w-3.5" />}
                중지
              </button>
            </div>
          </div>
          <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full bg-blue-500 transition-all"
              style={{ width: `${jobProgress(activeJob)}%` }}
            />
          </div>
        </section>
      )}

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" aria-label="파이프라인 요약">
        {[
          { label: "등록 원천", value: formatNumber(summary?.source_count), note: `${summary?.healthy_source_count || 0}개 정상`, icon: Database, color: "text-teal-700 dark:text-teal-300" },
          { label: "인증 설정", value: `${summary?.credential_configured || 0}/${summary?.credential_required || 0}`, note: "필수 원천 기준", icon: ShieldCheck, color: "text-emerald-700 dark:text-emerald-300" },
          { label: "수집 이력", value: formatNumber(summary?.raw_manifest_rows), note: "매니페스트 행", icon: FileText, color: "text-sky-700 dark:text-sky-300" },
          { label: "Gold", value: formatNumber(summary?.gold_file_count), note: "현재 파일", icon: Layers3, color: "text-indigo-700 dark:text-indigo-300" },
          { label: "제품 기준", value: productQuarter(summary?.product_quarter || null), note: formatDate(dashboard?.database.updated_at, true), icon: Server, color: "text-rose-700 dark:text-rose-300" },
        ].map((metric) => {
          const Icon = metric.icon;
          return (
            <article key={metric.label} className="min-w-0 rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold text-muted-foreground">{metric.label}</span>
                <Icon className={clsx("h-4 w-4 shrink-0", metric.color)} />
              </div>
              <p className="mt-3 truncate text-lg font-bold" title={metric.value}>{metric.value}</p>
              <p className="mt-1 truncate text-xs text-muted-foreground">{metric.note}</p>
            </article>
          );
        })}
      </section>

      <div className="scrollbar-natural flex w-full gap-1 overflow-x-auto border-b" role="tablist" aria-label="관리자 데이터 보기">
        {([ 
          ["sources", "원천 관리", Database],
          ["integrations", "외부 연결", Server],
          ["pipeline", "파이프라인", Layers3],
          ["runs", "실행 기록", FileClock],
          ["monitoring", "비용 모니터링", Activity],
          ["report-evaluation", "리포트 평가", FileSearch],
          ["analytics", "이용 현황", Activity],
          ["quality", "데이터 품질", ShieldCheck],
          ["errors", "오류 기록", AlertTriangle],
          ["comments", "댓글 관리", FileText],
        ] as const).map(([value, label, Icon]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            onClick={() => selectTab(value)}
            className={clsx(
              "relative inline-flex h-11 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap px-3 text-xs font-semibold transition-colors sm:justify-start sm:gap-2 sm:px-4 sm:text-sm",
              tab === value ? "text-primary" : "text-muted-foreground hover:text-foreground"
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
            {tab === value && <span className="absolute inset-x-2 bottom-0 h-0.5 bg-primary" />}
          </button>
        ))}
      </div>

      {tab === "sources" && (
        <section className="space-y-4">
          <section aria-labelledby="today-action-title" className="overflow-hidden rounded-lg border bg-card">
            <div className="flex flex-col gap-3 border-b px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 id="today-action-title" className="text-base font-bold">오늘 처리할 일</h2>
              </div>
              {attentionSources.length > 0 && (
                <button
                  type="button"
                  onClick={openAttentionSources}
                  className="inline-flex h-9 shrink-0 items-center justify-center gap-1 rounded-md border px-3 text-xs font-semibold hover:bg-muted"
                >
                  확인 필요 원천 보기
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
            <div className="grid gap-px bg-border [grid-template-columns:repeat(auto-fit,minmax(260px,1fr))]">
              {activeJob && (
                <button
                  type="button"
                  onClick={() => openJobDetail(activeJob.id)}
                  className="flex min-h-24 items-start gap-3 bg-card p-4 text-left transition-colors hover:bg-muted/60"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-300">
                    <LoaderCircle className="h-4 w-4 animate-spin" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-semibold text-muted-foreground">실행 중인 작업</span>
                    <span className="mt-1 block truncate text-sm font-bold">{activeJob.label}</span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">{activeJob.completed_steps}/{activeJob.step_count} 단계 · 로그 보기</span>
                  </span>
                  <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              )}
              {recentProblemJob && (
                <button
                  type="button"
                  onClick={() => openJobDetail(recentProblemJob.id)}
                  className="flex min-h-24 items-start gap-3 bg-card p-4 text-left transition-colors hover:bg-muted/60"
                >
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-red-50 text-red-700 dark:bg-red-950/50 dark:text-red-300">
                    <XCircle className="h-4 w-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-xs font-semibold text-muted-foreground">최근 확인이 필요한 실행</span>
                    <span className="mt-1 block truncate text-sm font-bold">{recentProblemJob.label}</span>
                    <span className="mt-1 block truncate text-xs text-muted-foreground">{jobMeta[recentProblemJob.status].label} · 실행 결과 보기</span>
                  </span>
                  <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                </button>
              )}
              {attentionSources.map((source) => {
                const attentionLabel = source.credential_status === "missing"
                  ? "필수 인증 확인"
                  : source.health === "healthy" && source.product_lineage_status && source.product_lineage_status !== "healthy"
                    ? "제품 반영 확인"
                    : healthMeta[source.health].label;
                return (
                  <button
                    key={source.source_id}
                    type="button"
                    onClick={() => openSource(source)}
                    className="flex min-h-24 items-start gap-3 bg-card p-4 text-left transition-colors hover:bg-muted/60"
                  >
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-amber-50 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300">
                      <CircleAlert className="h-4 w-4" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-xs font-semibold text-muted-foreground">{attentionLabel}</span>
                      <span className="mt-1 block truncate text-sm font-bold">{source.dataset_name}</span>
                      <span className="mt-1 block truncate text-xs text-muted-foreground">{source.provider} · 원천 상세 보기</span>
                    </span>
                    <ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />
                  </button>
                );
              })}
              {!activeJob && !recentProblemJob && attentionSources.length === 0 && (
                <div className="flex min-h-24 items-center gap-3 bg-card p-4 text-sm text-muted-foreground">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-emerald-50 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
                    <CheckCircle2 className="h-4 w-4" />
                  </span>
                  확인이 필요한 원천이 없습니다.
                </div>
              )}
            </div>
          </section>

          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-base font-bold">원천 연결 상태</h2>
              <p className="mt-1 text-xs text-muted-foreground">{filteredSources.length}개 원천 표시</p>
            </div>
            <div className="grid gap-2 sm:grid-cols-[minmax(220px,1fr)_150px_210px]">
              <label className="relative block">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="원천 검색"
                  className="h-10 w-full rounded-md border bg-card pl-9 pr-3 text-sm"
                />
              </label>
              <select
                value={healthFilter}
                onChange={(event) => setHealthFilter(event.target.value)}
                aria-label="상태 필터"
                className="h-10 rounded-md border bg-card px-3 text-sm"
              >
                <option value="all">전체 상태</option>
                <option value="attention">조치 필요</option>
                <option value="healthy">정상</option>
                <option value="warning">확인 필요</option>
                <option value="error">오류</option>
              </select>
              <select
                value={providerFilter}
                onChange={(event) => setProviderFilter(event.target.value)}
                aria-label="제공기관 필터"
                className="h-10 min-w-0 rounded-md border bg-card px-3 text-sm"
              >
                <option value="all">전체 제공기관</option>
                {providers.map((provider) => <option key={provider} value={provider}>{provider}</option>)}
              </select>
            </div>
          </div>

          <div className="hidden overflow-hidden rounded-lg border bg-card lg:block">
            <div className="scrollbar-natural overflow-x-auto">
              <table className="w-full min-w-[1080px] border-collapse text-left text-sm">
                <thead className="bg-muted/60 text-xs text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3 font-semibold">원천</th>
                    <th className="px-4 py-3 font-semibold">역할</th>
                    <th className="px-4 py-3 font-semibold">상태</th>
                    <th className="px-4 py-3 font-semibold">인증</th>
                    <th className="px-4 py-3 text-right font-semibold">수집 이력</th>
                    <th className="px-4 py-3 font-semibold">수집일 / 기준기간</th>
                    <th className="w-28 px-4 py-3 text-right font-semibold">작업</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {filteredSources.map((source) => {
                    const definition = source.refresh_job_key ? definitions.get(source.refresh_job_key) : null;
                    return (
                      <tr key={source.source_id} className="transition-colors hover:bg-muted/30">
                        <td className="max-w-[360px] px-4 py-3.5">
                          <p className="truncate font-semibold" title={source.dataset_name}>{source.dataset_name}</p>
                          <p className="mt-1 truncate text-xs text-muted-foreground" title={source.source_id}>{source.provider} · {source.source_id}</p>
                        </td>
                        <td className="px-4 py-3.5">
                          <span className="rounded-md bg-muted px-2 py-1 text-xs font-semibold text-muted-foreground">
                            {roleLabels[source.engine_role] || source.engine_role}
                          </span>
                          {source.product_role && <p className="mt-1.5 max-w-44 text-[11px] leading-4 text-muted-foreground">제품: {source.product_role}</p>}
                          {source.product_lineage_status && (
                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                              <span className="text-[11px] font-semibold text-muted-foreground">{productLineageLabel(source)}</span>
                              <HealthBadge health={source.product_lineage_status} />
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-3.5"><HealthBadge health={source.health} /></td>
                        <td className="px-4 py-3.5 text-xs font-semibold text-muted-foreground">
                          <span className={clsx(source.credential_status === "missing" && "text-red-600 dark:text-red-300")}>{credentialLabels[source.credential_status]}</span>
                        </td>
                        <td className="px-4 py-3.5 text-right font-mono text-xs">{formatNumber(source.manifest_rows)}</td>
                        <td className="px-4 py-3.5 text-xs text-muted-foreground">
                          <p>최근 확인 {formatDate(source.last_checked_at || source.last_collected_at)}</p>
                          {source.content_version_date && (
                            <p className="mt-0.5 text-[11px]">내용 버전 {formatDate(source.content_version_date)}</p>
                          )}
                          <p className="mt-1 font-semibold text-foreground">제공 {formatDataPeriod(source.last_data_period_end)}</p>
                          {source.retained_data_period_end && (
                            <p className="mt-0.5 text-[11px]">누적 {formatDataPeriod(source.retained_data_period_start)}~{formatDataPeriod(source.retained_data_period_end)}</p>
                          )}
                          {source.last_change_status && <p className="mt-0.5 text-[11px]">{source.last_change_status}</p>}
                          {source.product_lineage_status && (
                            <p className="mt-1 max-w-64 truncate text-[11px] font-semibold text-muted-foreground" title={source.product_refresh_note || undefined}>
                              제품 기준 {formatDataPeriod(source.product_data_period_start)}~{formatDataPeriod(source.product_data_period_end)}
                            </p>
                          )}
                        </td>
                        <td className="px-4 py-3.5 text-right">
                          {definition && source.refresh_available ? (
                            <button
                              type="button"
                              onClick={() => requestJob(source.refresh_job_key)}
                              disabled={Boolean(activeJob) || startingKey === source.refresh_job_key}
                              title={`${definition.label} 실행`}
                              className="inline-flex h-8 items-center gap-1.5 rounded-md border px-2 text-xs font-semibold transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                            >
                              <RefreshCw className={clsx("h-3.5 w-3.5", startingKey === source.refresh_job_key && "animate-spin")} />
                              {definition.output_scope === "raw_only" ? "원천 수집" : "갱신"}
                            </button>
                          ) : (
                            <span className="text-[11px] font-medium text-muted-foreground">
                              {definition ? "현재 실행 불가" : "자동 작업 없음"}
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div className="divide-y rounded-lg border bg-card lg:hidden">
            {filteredSources.map((source) => {
              const definition = source.refresh_job_key ? definitions.get(source.refresh_job_key) : null;
              return (
                <article key={source.source_id} className="p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-semibold">{source.dataset_name}</p>
                      <p className="mt-1 truncate text-xs text-muted-foreground">{source.provider}</p>
                    </div>
                    <HealthBadge health={source.health} />
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                    <div><p className="text-muted-foreground">역할</p><p className="mt-1 font-semibold">{roleLabels[source.engine_role] || source.engine_role}</p></div>
                    <div><p className="text-muted-foreground">인증</p><p className="mt-1 font-semibold">{credentialLabels[source.credential_status]}</p></div>
                    <div><p className="text-muted-foreground">수집 이력</p><p className="mt-1 font-semibold">{formatNumber(source.manifest_rows)}건</p></div>
                    <div><p className="text-muted-foreground">최근 확인</p><p className="mt-1 font-semibold">{formatDate(source.last_checked_at || source.last_collected_at, true)}</p></div>
                    {source.content_version_date && <div><p className="text-muted-foreground">내용 버전</p><p className="mt-1 font-semibold">{formatDate(source.content_version_date, true)}</p></div>}
                    <div><p className="text-muted-foreground">제공 최신 구간</p><p className="mt-1 font-semibold">{formatDataPeriod(source.last_data_period_start)}~{formatDataPeriod(source.last_data_period_end)}</p></div>
                    {source.retained_data_period_end && <div><p className="text-muted-foreground">제품 누적 구간</p><p className="mt-1 font-semibold">{formatDataPeriod(source.retained_data_period_start)}~{formatDataPeriod(source.retained_data_period_end)}</p></div>}
                  </div>
                  {source.product_lineage_status && (
                    <div className="mt-4 rounded-md border bg-muted/30 p-3 text-xs">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-semibold">{productLineageLabel(source)} · {source.product_role || "제품 입력"}</p>
                        <HealthBadge health={source.product_lineage_status} />
                      </div>
                      {source.product_refresh_note && <p className="mt-2 leading-5 text-muted-foreground">{source.product_refresh_note}</p>}
                    </div>
                  )}
                  {definition && source.refresh_available ? (
                    <button
                      type="button"
                      onClick={() => requestJob(source.refresh_job_key)}
                      disabled={Boolean(activeJob) || startingKey === source.refresh_job_key}
                      className="mt-4 inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border text-xs font-semibold hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <RefreshCw className={clsx("h-3.5 w-3.5", startingKey === source.refresh_job_key && "animate-spin")} />
                      {definition.label}
                    </button>
                  ) : (
                    <p className="mt-4 text-center text-xs font-medium text-muted-foreground">
                      {definition ? "현재 실행할 수 없는 작업입니다." : "자동 실행 작업이 없는 원천입니다."}
                    </p>
                  )}
                </article>
              );
            })}
          </div>

          {filteredSources.length === 0 && (
            <div className="flex min-h-40 items-center justify-center border-y text-sm text-muted-foreground">조건에 맞는 원천이 없습니다.</div>
          )}
        </section>
      )}

      {tab === "integrations" && (
        <section className="space-y-6">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-base font-bold">외부 연결 API</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void checkNaverConnection()}
                disabled={checkingNaverConnection}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border bg-card px-4 text-xs font-semibold hover:bg-muted disabled:opacity-50"
              >
                {checkingNaverConnection ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
                NAVER 연결 확인
              </button>
              <button
                type="button"
                onClick={() => void loadIntegrations()}
                disabled={integrationLoading}
                className="inline-flex h-10 items-center justify-center gap-2 rounded-md border bg-card px-4 text-xs font-semibold hover:bg-muted disabled:opacity-50"
              >
                <RefreshCw className={clsx("h-3.5 w-3.5", integrationLoading && "animate-spin")} />
                상태 새로고침
              </button>
            </div>
          </div>

          {integrationLoading && !integrations ? (
            <div className="flex min-h-[30vh] items-center justify-center">
              <LoaderCircle className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : integrations ? (
            <div className="space-y-6">
              <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
                {[
                  { label: "연결 제공기관", value: `${formatNumber(integrations.summary.provider_count)}곳` },
                  { label: "등록 원천", value: `${formatNumber(integrations.summary.source_count)}개` },
                  { label: "필수 인증", value: `${integrations.summary.credential_configured}/${integrations.summary.credential_required}` },
                  { label: "확인 필요 제공기관", value: `${formatNumber(integrations.summary.warning_provider_count + integrations.summary.error_provider_count)}곳` },
                  { label: "기록된 호출 성공률", value: `${integrations.summary.success_rate}%` },
                ].map((card) => (
                  <div key={card.label} className="rounded-lg border bg-card p-4">
                    <p className="text-xs font-semibold text-muted-foreground">{card.label}</p>
                    <p className="mt-2 text-lg font-bold">{card.value}</p>
                  </div>
                ))}
              </div>

              <div>
                <div className="mb-3">
                  <h3 className="text-sm font-bold">런타임 연결</h3>
                </div>
                <div className="grid gap-4 lg:grid-cols-2">
                  {integrations.runtime_integrations.map((integration) => (
                    <article key={integration.integration_id} className="rounded-lg border bg-card p-5">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h4 className="font-bold">{integration.label}</h4>
                          <p className="mt-1 text-xs text-muted-foreground">
                            {integration.configured ? "설정 또는 최근 활동 확인됨" : "설정 또는 활동 미확인"}
                          </p>
                        </div>
                        <HealthBadge health={integration.status} />
                      </div>
                      <dl className="mt-5 grid gap-3 text-xs sm:grid-cols-2">
                        <div>
                          <dt className="text-muted-foreground">설정 모델</dt>
                          <dd className="mt-1 break-all font-mono font-semibold">{integration.configured_model || "-"}</dd>
                        </div>
                        {integration.report_reasoning_effort && (
                          <div>
                            <dt className="text-muted-foreground">상세 리포트 추론</dt>
                            <dd className="mt-1 font-semibold">
                              {reasoningEffortMeta[integration.report_reasoning_effort].label}
                              <span className="ml-1 font-normal text-muted-foreground">
                                ({integration.report_reasoning_effort})
                              </span>
                            </dd>
                          </div>
                        )}
                        <div>
                          <dt className="text-muted-foreground">최근 활동</dt>
                          <dd className="mt-1 font-semibold">{formatDate(integration.last_activity_at)}</dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">누적 이벤트</dt>
                          <dd className="mt-1 font-semibold">{formatNumber(integration.call_count)}회</dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">최근 응답 모델</dt>
                          <dd className="mt-1 space-y-1 font-mono text-[11px] text-muted-foreground">
                            {integration.observed_models.length > 0 ? integration.observed_models.slice(0, 3).map((model) => (
                              <p key={model.model_name} className="truncate" title={model.model_name}>
                                {model.model_name} · {formatNumber(model.call_count)}회
                              </p>
                            )) : "-"}
                          </dd>
                        </div>
                      </dl>
                      {integration.client_observation && (
                        <div className="mt-4 rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                          <p className="font-semibold text-foreground">브라우저 관측값 · 상태 판정 제외</p>
                          <p className="mt-1">
                            성공 {formatNumber(integration.client_observation.success_count)}회 · 실패 {formatNumber(integration.client_observation.failure_count)}회
                            {integration.client_observation.last_event_at
                              ? ` · 최근 ${formatDate(integration.client_observation.last_event_at)}`
                              : ""}
                          </p>
                        </div>
                      )}
                      <p className="mt-4 border-t pt-3 text-xs leading-5 text-muted-foreground">{integration.status_note}</p>
                    </article>
                  ))}
                </div>
              </div>

              <div className="overflow-hidden rounded-lg border bg-card">
                <div className="border-b px-4 py-4">
                  <h3 className="text-sm font-bold">데이터 제공기관</h3>
                </div>
                <div className="scrollbar-natural overflow-x-auto">
                  <table className="w-full min-w-[860px] text-left text-sm">
                    <thead className="bg-muted/60 text-xs text-muted-foreground">
                      <tr>
                        <th className="px-4 py-3 font-semibold">제공기관</th>
                        <th className="px-4 py-3 text-right font-semibold">원천</th>
                        <th className="px-4 py-3 font-semibold">인증</th>
                        <th className="px-4 py-3 font-semibold">상태</th>
                        <th className="px-4 py-3 text-right font-semibold">실패 이력</th>
                        <th className="px-4 py-3 font-semibold">최근 갱신</th>
                        <th className="px-4 py-3 text-right font-semibold">갱신 가능</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {integrations.providers.map((provider) => (
                        <tr key={provider.provider_id} className="hover:bg-muted/30">
                          <td className="px-4 py-3 font-semibold">
                            <button
                              type="button"
                              onClick={() => openSourcesForProvider(provider.provider)}
                              aria-label={`${provider.provider} 원천 ${provider.source_count}개 보기`}
                              className="inline-flex max-w-full items-center gap-1 rounded-md text-left hover:text-primary"
                            >
                              <span className="truncate">{provider.provider}</span>
                              <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                            </button>
                          </td>
                          <td className="px-4 py-3 text-right font-mono text-xs">{formatNumber(provider.source_count)}</td>
                          <td className="px-4 py-3 text-xs font-semibold">
                            <span className={clsx(provider.credential_status === "missing" && "text-red-600 dark:text-red-300")}>
                              {credentialLabels[provider.credential_status]} ({provider.credential_configured}/{provider.credential_required})
                            </span>
                          </td>
                          <td className="px-4 py-3"><HealthBadge health={provider.health} /></td>
                          <td className="px-4 py-3 text-right font-mono text-xs">{formatNumber(provider.failure_rows)}</td>
                          <td className="px-4 py-3 text-xs text-muted-foreground">{formatDate(provider.last_collected_at)}</td>
                          <td className="px-4 py-3 text-right text-xs font-semibold">
                            {provider.refresh_available_count > 0 ? (
                              <button
                                type="button"
                                onClick={() => openSourcesForProvider(provider.provider)}
                                className="inline-flex items-center gap-1 rounded-md border px-2 py-1 hover:bg-muted"
                              >
                                {formatNumber(provider.refresh_available_count)}개 작업
                                <ChevronRight className="h-3 w-3" />
                              </button>
                            ) : "없음"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <details className="overflow-hidden rounded-lg border bg-card">
                <summary className="flex cursor-pointer items-center justify-between gap-3 px-4 py-4 hover:bg-muted/40">
                  <span>
                    <span className="block text-sm font-bold">최근 외부 요청</span>
                    <span className="mt-1 block text-xs text-muted-foreground">원본 키를 제외한 요청 이력입니다.</span>
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatNumber(integrations.recent_calls.length)}건</span>
                </summary>
                <div className="scrollbar-natural max-h-[480px] overflow-auto">
                  <table className="w-full min-w-[920px] text-left text-sm">
                    <thead className="sticky top-0 z-10 bg-muted text-xs text-muted-foreground">
                      <tr>
                        <th className="p-3">시각</th>
                        <th className="p-3">구분</th>
                        <th className="p-3">API / 제공기관</th>
                        <th className="p-3">요청</th>
                        <th className="p-3 text-right">상태</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {integrations.recent_calls.map((call) => (
                        <tr key={call.id} className="hover:bg-muted/30">
                          <td className="p-3 text-muted-foreground">{formatDate(call.created_at)}</td>
                          <td className="p-3 text-xs font-semibold text-muted-foreground">{externalCallOriginLabel(call.origin)}</td>
                          <td className="p-3 font-semibold">{call.api_name}</td>
                          <td className="max-w-md truncate p-3 font-mono text-xs text-muted-foreground" title={call.endpoint}>{call.endpoint}</td>
                          <td className="p-3 text-right">
                            <span className={clsx(
                              "rounded-md px-2 py-1 text-xs font-bold",
                              call.status_code < 400 ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300"
                            )}>
                              {call.status_code}
                            </span>
                          </td>
                        </tr>
                      ))}
                      {integrations.recent_calls.length === 0 && (
                        <tr><td colSpan={5} className="py-6 text-center text-muted-foreground">최근 외부 요청 이력이 없습니다.</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </details>
            </div>
          ) : (
            <div className="flex min-h-40 items-center justify-center border-y text-sm text-muted-foreground">외부 연결 상태를 불러오지 못했습니다.</div>
          )}
        </section>
      )}

      {tab === "pipeline" && (
        <section className="space-y-4">
          <div className="flex flex-col gap-3 rounded-lg border bg-card p-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              <span className={clsx(
                "inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-bold",
                readyLayerCount === (dashboard?.layers.length || 0)
                  ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
                  : "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-300"
              )}>
                {readyLayerCount === (dashboard?.layers.length || 0)
                  ? <CheckCircle2 className="h-3.5 w-3.5" />
                  : <AlertTriangle className="h-3.5 w-3.5" />}
                현재 게시본 {readyLayerCount}/{dashboard?.layers.length || 0} 사용 가능
              </span>
              <span className={clsx(
                "rounded-md border px-2.5 py-1.5 text-xs font-bold",
                pipelineRunnable
                  ? "border-blue-200 bg-blue-50 text-blue-700 dark:border-blue-900 dark:bg-blue-950/40 dark:text-blue-300"
                  : "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300"
              )}>
                {pipelineRunnable ? `${pipelineDefinitions.length}개 작업 실행 가능` : "실행 불가 작업 있음"}
              </span>
              {pipelineStatusJob ? (
                <>
                  <JobBadge status={pipelineStatusJob.status} />
                  <span className="text-xs text-muted-foreground">
                    {pipelineStatusJob.label} · {formatDate(pipelineStatusJob.finished_at || pipelineStatusJob.started_at || pipelineStatusJob.created_at, true)}
                  </span>
                </>
              ) : (
                <span className="rounded-md border px-2.5 py-1.5 text-xs font-semibold text-muted-foreground">관리자 실행 기록 없음</span>
              )}
            </div>
            {pipelineStatusJob && (
              <button
                type="button"
                onClick={() => void loadJobDetail(pipelineStatusJob.id)}
                className="inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-md border px-3 text-xs font-semibold hover:bg-muted"
              >
                <TerminalSquare className="h-3.5 w-3.5" />
                {pipelineStatusJob.is_active ? "진행 보기" : "최근 기록"}
              </button>
            )}
          </div>

          {latestDataCheckSummary ? (
            <FreshnessResultPanel
              summary={latestDataCheckSummary}
              title="마지막 데이터 최신 상태 점검"
              onOpenLog={latestDataCheck ? () => void loadJobDetail(latestDataCheck.id) : undefined}
            />
          ) : (
            <div className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm text-muted-foreground">
              <HardDrive className="h-4 w-4 shrink-0" />
              <span>아직 구조화된 데이터 최신 상태 점검 기록이 없습니다.</span>
            </div>
          )}

          <div className="grid gap-8 xl:grid-cols-[minmax(0,1fr)_360px]">
            <div>
              <div className="mb-4">
                <h2 className="text-base font-bold">제품 데이터 단계</h2>
              </div>
              <div className="rounded-lg border bg-card">
                {dashboard?.layers.map((layer, index) => {
                  const definition = layer.job_key ? definitions.get(layer.job_key) : null;
                  const latestJob = layer.job_key ? latestJobByKey.get(layer.job_key) : undefined;
                  const runningJob = definition && activeJob?.job_key === definition.key ? activeJob : null;
                  return (
                    <article key={layer.key} className="relative grid grid-cols-[36px_minmax(0,1fr)] gap-3 border-b p-4 last:border-b-0 sm:grid-cols-[44px_minmax(0,1fr)_auto] sm:items-center sm:gap-4">
                      <div className="relative flex h-9 w-9 items-center justify-center rounded-md border bg-background text-xs font-bold">
                        {index + 1}
                        {index < (dashboard.layers.length - 1) && <span className="absolute left-1/2 top-9 h-[calc(100%+17px)] w-px -translate-x-1/2 bg-border sm:hidden" />}
                      </div>
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-bold">{layer.label}</h3>
                          <HealthBadge health={layer.status} />
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {formatNumber(layer.count)} {layer.unit} · {formatDate(layer.updated_at)}
                        </p>
                        {layer.note && <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground" title={layer.note}>{layer.note}</p>}
                        {definition && (
                          <p className="mt-1 text-xs text-muted-foreground">
                            {runningJob
                              ? `실행 중 · ${runningJob.completed_steps}/${runningJob.step_count} 단계`
                              : latestJob
                                ? `최근 실행 ${jobMeta[latestJob.status].label} · ${formatDate(latestJob.finished_at || latestJob.started_at || latestJob.created_at, true)}`
                                : "관리자 실행 기록 없음"}
                          </p>
                        )}
                      </div>
                      <div className="col-start-2 flex items-center justify-between gap-3 sm:col-start-3">
                        {runningJob ? (
                          <button
                            type="button"
                            onClick={() => void loadJobDetail(runningJob.id)}
                            className="inline-flex h-9 items-center gap-2 rounded-md border px-3 text-xs font-semibold hover:bg-muted"
                          >
                            <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                            진행 보기
                          </button>
                        ) : definition ? (
                          <button
                            type="button"
                            onClick={() => requestJob(definition.key)}
                            disabled={!definition.enabled || Boolean(activeJob) || startingKey === definition.key}
                            className="inline-flex h-9 items-center gap-2 rounded-md border px-3 text-xs font-semibold transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            {startingKey === definition.key ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                            {definition.enabled ? jobActionLabel(latestJob) : "실행 불가"}
                          </button>
                        ) : (
                          <span className="text-xs text-muted-foreground">{layer.key === "external_lineage" ? "별도 갱신 계약" : "원천 집계"}</span>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>

            <aside>
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-base font-bold">주요 작업</h2>
                <button
                  type="button"
                  onClick={() => requestJob("status_check")}
                  disabled={Boolean(activeJob)}
                  className="inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-xs font-semibold hover:bg-muted disabled:opacity-40"
                >
                  <HardDrive className="h-3.5 w-3.5" />
                  데이터 최신 상태 점검
                </button>
              </div>
              <div className="divide-y rounded-lg border bg-card">
                {supplementalPipelineDefinitions.map((definition) => {
                  const latestJob = latestJobByKey.get(definition.key);
                  const runningJob = activeJob?.job_key === definition.key ? activeJob : null;
                  return (
                    <button
                      key={definition.key}
                      type="button"
                      onClick={() => runningJob ? void loadJobDetail(runningJob.id) : requestJob(definition.key)}
                      disabled={!runningJob && (!definition.enabled || Boolean(activeJob))}
                      className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-muted/40 disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      <span className={clsx(
                        "flex h-9 w-9 shrink-0 items-center justify-center rounded-md",
                        definition.risk === "high" ? "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" : "bg-muted text-muted-foreground"
                      )}>
                        {runningJob ? <LoaderCircle className="h-4 w-4 animate-spin" /> : definition.risk === "high" ? <AlertTriangle className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold">{definition.label}</span>
                        <span className="mt-1 block text-xs text-muted-foreground">
                          {definition.enabled ? "실행 가능" : "실행 불가"} · {definition.estimate} · {definition.step_count}단계
                        </span>
                      </span>
                      <span className="shrink-0 text-xs font-semibold text-muted-foreground">
                        {runningJob ? "진행 보기" : jobActionLabel(latestJob)}
                      </span>
                    </button>
                  );
                })}
              </div>
            </aside>
          </div>
        </section>
      )}

      {tab === "runs" && (
        <section className="space-y-4">
          <div>
            <h2 className="text-base font-bold">실행 기록</h2>
            <p className="mt-1 text-xs text-muted-foreground">최근 작업 {visibleRecentJobs.length}건</p>
          </div>
          <div className="overflow-hidden rounded-lg border bg-card">
            {visibleRecentJobs.length > 0 ? (
              <div className="scrollbar-natural max-h-[520px] divide-y overflow-y-auto">
                {visibleRecentJobs.map((job) => (
                  <button
                    key={job.id}
                    type="button"
                    onClick={() => void loadJobDetail(job.id)}
                    className="grid w-full gap-3 p-4 text-left transition-colors hover:bg-muted/30 sm:grid-cols-[minmax(220px,1fr)_110px_130px_155px_24px] sm:items-center"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold">{job.label}</span>
                      <span className="mt-1 block truncate text-xs text-muted-foreground">{job.current_step || job.message || `작업 #${job.id}`}</span>
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5">
                      <JobBadge status={job.status} />
                      {recoveredByJobId.has(job.id) && (
                        <span className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-1 text-[11px] font-bold text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-300">
                          후속 #{recoveredByJobId.get(job.id)?.id}에서 복구
                        </span>
                      )}
                    </span>
                    <span className="text-xs text-muted-foreground">{job.completed_steps}/{job.step_count} 단계</span>
                    <span className="text-xs text-muted-foreground">{formatDate(job.started_at || job.created_at)}</span>
                    <ChevronRight className="hidden h-4 w-4 text-muted-foreground sm:block" />
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex min-h-44 flex-col items-center justify-center gap-2 text-sm text-muted-foreground">
                <FileClock className="h-6 w-6" />
                실행 기록이 없습니다.
              </div>
            )}
          </div>
        </section>
      )}

      {tab === "monitoring" && (
        <section className="space-y-6">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold">OpenAI 비용 및 토큰 사용량</h2>
            <button
              type="button"
              onClick={() => void loadTokenUsage()}
              disabled={tokenLoading}
              className="inline-flex h-10 items-center gap-2 rounded-md border bg-card px-4 text-xs font-semibold hover:bg-muted disabled:opacity-50"
            >
              <RefreshCw className={clsx("h-3.5 w-3.5", tokenLoading && "animate-spin")} />
              새로고침
            </button>
          </div>

          {tokenLoading && !tokenUsage ? (
            <div className="flex min-h-[30vh] items-center justify-center">
              <LoaderCircle className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : tokenUsage ? (
            <div className="space-y-6">
              <div className="rounded-lg border bg-card p-4">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
                  <div>
                    <h3 className="text-sm font-bold">상세 리포트 추론 설정</h3>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      {tokenUsage.report_ai_config.configured_model} · 다음 신규 생성부터 적용되며 강도별로 캐시가 분리됩니다.
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      현재 출처: {tokenUsage.report_ai_config.source === "admin"
                        ? "관리자 설정"
                        : tokenUsage.report_ai_config.source === "environment"
                          ? "환경 설정"
                          : "기본값"}
                      {tokenUsage.report_ai_config.updated_at
                        ? ` · ${formatDate(tokenUsage.report_ai_config.updated_at)}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
                    <label className="grid gap-1 text-xs font-semibold" htmlFor="report-reasoning-effort">
                      추론 강도
                      <select
                        id="report-reasoning-effort"
                        value={reasoningEffortDraft}
                        onChange={(event) => setReasoningEffortDraft(event.target.value as ReasoningEffort)}
                        disabled={savingReasoningEffort}
                        className="h-10 min-w-48 rounded-md border bg-background px-3 text-sm font-semibold disabled:opacity-50"
                      >
                        {tokenUsage.report_ai_config.supported_reasoning_efforts.map((effort) => (
                          <option key={effort} value={effort}>
                            {reasoningEffortMeta[effort].label} ({effort}) · {reasoningEffortMeta[effort].description}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button
                      type="button"
                      onClick={() => void saveReportReasoningEffort()}
                      disabled={
                        savingReasoningEffort
                        || reasoningEffortDraft === tokenUsage.report_ai_config.reasoning_effort
                      }
                      className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-primary px-4 text-xs font-bold text-primary-foreground hover:opacity-90 disabled:opacity-50"
                    >
                      {savingReasoningEffort && <LoaderCircle className="h-3.5 w-3.5 animate-spin" />}
                      설정 적용
                    </button>
                  </div>
                </div>
              </div>

              {/* Summary Cards */}
              <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-7">
                {[
                  { label: "총 호출 횟수", value: `${formatNumber(tokenUsage.summary.total_calls)} 회` },
                  { label: "규칙 기반 전환", value: `${formatNumber(tokenUsage.summary.degraded_calls)} 회` },
                  { label: "실패 호출", value: `${formatNumber(tokenUsage.summary.failed_calls)} 회` },
                  { label: "누적 추정 비용", value: `$${tokenUsage.summary.total_cost.toFixed(5)}` },
                  { label: "상세 리포트 누적 비용", value: `$${tokenUsage.summary.report_cost.toFixed(5)}` },
                  { label: "챗봇 누적 비용", value: `$${tokenUsage.summary.chatbot_cost.toFixed(5)}` },
                  { label: "총 사용 토큰", value: `${formatNumber(tokenUsage.summary.total_tokens)} T` }
                ].map((card) => (
                  <div key={card.label} className="rounded-lg border bg-card p-4">
                    <p className="text-xs font-semibold text-muted-foreground">{card.label}</p>
                    <p className="mt-2 text-lg font-bold">{card.value}</p>
                  </div>
                ))}
              </div>

              {/* Breakdown Grid */}
              <div className="grid gap-6 md:grid-cols-2">
                {/* Model Breakdown */}
                <div className="rounded-lg border bg-card p-4">
                  <h3 className="mb-3 text-sm font-bold border-b pb-2">모델별 비용 분포</h3>
                  <div className="space-y-3">
                    {Object.entries(tokenUsage.model_breakdown).map(([model, meta]) => (
                      <div key={model} className="flex items-center justify-between text-xs">
                        <span className="font-semibold text-muted-foreground">{model}</span>
                        <span className="font-mono">
                          {meta.calls}회 호출 · {formatNumber(meta.total_tokens)}토큰 · <strong>${meta.estimated_cost.toFixed(5)}</strong>
                        </span>
                      </div>
                    ))}
                    {Object.keys(tokenUsage.model_breakdown).length === 0 && (
                      <p className="text-center text-xs text-muted-foreground py-4">사용된 모델 데이터가 없습니다.</p>
                    )}
                  </div>
                </div>

                {/* Feature Breakdown */}
                <div className="rounded-lg border bg-card p-4">
                  <h3 className="mb-3 text-sm font-bold border-b pb-2">기능별 비용 분포</h3>
                  <div className="space-y-3">
                    {Object.entries(tokenUsage.feature_breakdown).map(([feature, meta]) => (
                      <div key={feature} className="flex items-center justify-between text-xs">
                        <span className="font-semibold text-muted-foreground">{feature}</span>
                        <span className="font-mono">
                          {meta.calls}회 호출 · {formatNumber(meta.total_tokens)}토큰 · <strong>${meta.estimated_cost.toFixed(5)}</strong>
                        </span>
                      </div>
                    ))}
                    {Object.keys(tokenUsage.feature_breakdown).length === 0 && (
                      <p className="text-center text-xs text-muted-foreground py-4">사용된 기능 데이터가 없습니다.</p>
                    )}
                  </div>
                </div>
              </div>

              {/* Log List */}
              <details className="overflow-hidden rounded-lg border bg-card">
                <summary className="flex cursor-pointer items-center justify-between gap-3 bg-muted/40 p-4 hover:bg-muted/60">
                  <span className="text-sm font-bold">최근 OpenAI 호출 로그</span>
                  <span className="text-xs font-normal text-muted-foreground">최대 100건 · {formatNumber(tokenUsage.logs.length)}건 표시</span>
                </summary>
                <div className="scrollbar-natural max-h-[480px] overflow-auto">
                  <table className="w-full text-left text-xs border-collapse">
                    <thead className="sticky top-0 z-10 bg-muted text-muted-foreground font-semibold">
                      <tr>
                        <th className="p-3">일시</th>
                        <th className="p-3">기능</th>
                        <th className="p-3">상태</th>
                        <th className="p-3">생성 방식</th>
                        <th className="p-3">사용 모델</th>
                        <th className="p-3">추론</th>
                        <th className="p-3 text-right">Prompt</th>
                        <th className="p-3 text-right">Completion</th>
                        <th className="p-3 text-right">총 토큰</th>
                        <th className="p-3 text-right">추정 비용</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {tokenUsage.logs.map((log) => (
                        <tr key={log.id} className="hover:bg-muted/30">
                          <td className="p-3 text-muted-foreground">{formatDate(log.created_at)}</td>
                          <td className="max-w-[280px] p-3 font-semibold">
                            {log.feature_name}
                            {log.error_message && (
                              <span className="mt-1 line-clamp-2 block font-normal text-rose-600 dark:text-rose-300" title={log.error_message}>
                                {log.error_type ? `${log.error_type} · ` : ""}{log.error_message}
                              </span>
                            )}
                            {log.original_validation_issues.length > 0 && (
                              <details className="mt-1 font-normal text-muted-foreground">
                                <summary className="cursor-pointer">원 검증 {log.original_validation_issues.length}건</summary>
                                <ul className="mt-1 max-w-[420px] space-y-1 font-mono text-[10px] leading-4">
                                  {log.original_validation_issues.map((issue, index) => (
                                    <li key={`${log.id}-issue-${index}`} className="break-words">{issue}</li>
                                  ))}
                                </ul>
                              </details>
                            )}
                          </td>
                          <td className="p-3">
                            <span className={clsx(
                              "rounded-full px-2 py-1 text-[10px] font-black",
                              log.status === "success"
                                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300"
                                : log.status === "degraded"
                                  ? "bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300"
                                  : "bg-rose-100 text-rose-700 dark:bg-rose-950/50 dark:text-rose-300",
                            )}>
                              {log.status === "success" ? "성공" : log.status === "degraded" ? "규칙 전환" : "실패"}
                            </span>
                          </td>
                          <td className="p-3 font-semibold text-muted-foreground">{reportGenerationModeLabel(log.generation_mode)}</td>
                          <td className="p-3 font-mono text-muted-foreground">{log.model_name}</td>
                          <td className="p-3 font-mono text-muted-foreground">{log.reasoning_effort || "-"}</td>
                          <td className="p-3 text-right font-mono">{formatNumber(log.prompt_tokens)}</td>
                          <td className="p-3 text-right font-mono">{formatNumber(log.completion_tokens)}</td>
                          <td className="p-3 text-right font-mono">{formatNumber(log.total_tokens)}</td>
                          <td className="p-3 text-right font-mono font-semibold text-primary">${log.estimated_cost.toFixed(5)}</td>
                        </tr>
                      ))}
                      {tokenUsage.logs.length === 0 && (
                        <tr>
                          <td colSpan={10} className="text-center py-6 text-muted-foreground">누적된 OpenAI 호출 로그가 아직 없습니다.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </details>

            </div>
          ) : (
            <div className="flex min-h-[30vh] items-center justify-center text-sm text-muted-foreground">비용 데이터를 불러올 수 없습니다.</div>
          )}
        </section>
      )}

      {tab === "report-evaluation" && <AdminReportEvaluationPanel refreshToken={extensionRefreshToken} />}
      {tab === "analytics" && <AdminAnalyticsPanel refreshToken={extensionRefreshToken} />}
      {tab === "quality" && (
        <AdminQualityPanel
          refreshToken={extensionRefreshToken}
          snapshot={dashboard ? {
            generated_at: dashboard.generated_at,
            summary: {
              source_count: dashboard.summary.source_count,
              healthy_source_count: dashboard.summary.healthy_source_count,
              product_quarter: dashboard.summary.product_quarter,
            },
            layers: dashboard.layers.map((layer) => ({
              key: layer.key,
              label: layer.label,
              status: layer.status,
              count: layer.count,
              unit: layer.unit,
              updated_at: layer.updated_at,
              note: layer.note || null,
            })),
          } : undefined}
        />
      )}
      {tab === "errors" && <AdminErrorLogsPanel refreshToken={extensionRefreshToken} />}
      {tab === "comments" && <AdminCommentsPanel refreshToken={extensionRefreshToken} />}

      {pendingJob && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/45 p-4" role="presentation" onMouseDown={() => setPendingJob(null)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-job-title"
            className="w-full max-w-md rounded-lg border bg-card p-5 shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <span className={clsx(
                "flex h-10 w-10 shrink-0 items-center justify-center rounded-md",
                pendingJob.risk === "high" ? "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300" : "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
              )}>
                <AlertTriangle className="h-5 w-5" />
              </span>
              <button type="button" onClick={() => setPendingJob(null)} title="닫기" aria-label="확인 창 닫기" className="rounded-md p-1 hover:bg-muted">
                <X className="h-4 w-4" />
              </button>
            </div>
            <h2 id="confirm-job-title" className="mt-4 text-lg font-bold">{pendingJob.label}</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{pendingJob.description}</p>
            <p className="mt-3 rounded-md border bg-muted/40 p-3 text-xs leading-5 text-muted-foreground">{pendingJob.scope_note}</p>
            <div className="mt-4 flex flex-wrap items-center gap-4 border-y py-3 text-xs text-muted-foreground">
              <span>{pendingJob.step_count}단계</span>
              <span>예상 {pendingJob.estimate}</span>
              <span>{pendingJob.risk === "high" ? "높은 주의" : "확인 필요"}</span>
              {pendingJob.source_ids.length > 0 && <span>관련 원천 {pendingJob.source_ids.length}개</span>}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={() => setPendingJob(null)} className="h-10 rounded-md border px-4 text-sm font-semibold hover:bg-muted">취소</button>
              <button
                type="button"
                onClick={() => void executeJob(pendingJob, true)}
                disabled={startingKey === pendingJob.key}
                className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground disabled:opacity-60"
              >
                {startingKey === pendingJob.key ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                실행
              </button>
            </div>
          </section>
        </div>
      )}

      {selectedJob && (
        <div className="fixed inset-0 z-[105] bg-black/25" role="presentation" onMouseDown={() => setSelectedJob(null)}>
          <aside
            role="dialog"
            aria-modal="true"
            aria-label="작업 실행 로그"
            className="absolute inset-y-0 right-0 flex w-full max-w-2xl flex-col border-l bg-card shadow-2xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header className="flex items-start justify-between gap-4 border-b p-5">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="truncate text-lg font-bold">{selectedJob.label}</h2>
                  <JobBadge status={selectedJob.status} />
                </div>
                <p className="mt-1 text-xs text-muted-foreground">작업 #{selectedJob.id} · {formatDuration(selectedJob.duration_seconds)}</p>
              </div>
              <div className="flex gap-1">
                {selectedJob.is_active && (
                  <button
                    type="button"
                    onClick={() => void cancelRunningJob(selectedJob.id)}
                    disabled={selectedJob.status === "cancelling" || cancellingJobId === selectedJob.id}
                    title="작업 중지"
                    aria-label="작업 중지"
                    className="inline-flex h-9 w-9 items-center justify-center rounded-md text-red-700 hover:bg-red-50 disabled:opacity-50 dark:text-red-300 dark:hover:bg-red-950/40"
                  >
                    {cancellingJobId === selectedJob.id ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <CircleStop className="h-4 w-4" />}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void loadJobDetail(selectedJob.id)}
                  disabled={jobDetailLoading}
                  title="로그 새로고침"
                  aria-label="로그 새로고침"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-muted disabled:opacity-50"
                >
                  <RefreshCw className={clsx("h-4 w-4", jobDetailLoading && "animate-spin")} />
                </button>
                <button type="button" onClick={() => setSelectedJob(null)} title="닫기" aria-label="로그 닫기" className="inline-flex h-9 w-9 items-center justify-center rounded-md hover:bg-muted">
                  <X className="h-4 w-4" />
                </button>
              </div>
            </header>
            <div className="grid grid-cols-2 gap-px border-b bg-border sm:grid-cols-4">
              {[
                ["상태", jobMeta[selectedJob.status].label],
                ["진행", `${selectedJob.completed_steps}/${selectedJob.step_count} · ${jobProgress(selectedJob).toFixed(1)}%`],
                ["시작", formatDate(selectedJob.started_at)],
                ["종료 코드", formatExitCode(selectedJob.exit_code)],
              ].map(([label, value]) => (
                <div key={label} className="bg-card p-3">
                  <p className="text-[11px] font-semibold text-muted-foreground">{label}</p>
                  <p className="mt-1 truncate text-xs font-bold" title={value}>{value}</p>
                </div>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto bg-muted/20 p-4">
              {selectedJob.steps && selectedJob.steps.length > 0 && (
                <section className="mb-3 overflow-hidden rounded-lg border bg-card">
                  <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
                    <h3 className="text-sm font-bold">단계별 상태</h3>
                    <span className="text-xs text-muted-foreground">
                      생략 {selectedJob.skipped_steps || 0}개
                    </span>
                  </header>
                  <div className="scrollbar-natural max-h-72 divide-y overflow-y-auto">
                    {selectedJob.steps.map((step) => (
                      <div key={step.step_index} className="flex items-start gap-3 px-4 py-3 text-xs">
                        <span className={clsx(
                          "mt-0.5 flex h-5 min-w-5 items-center justify-center rounded-full text-[10px] font-bold",
                          step.status === "running" && "bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300",
                          step.status === "completed" && "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300",
                          step.status.startsWith("skipped") && "bg-slate-100 text-slate-600 dark:bg-slate-900 dark:text-slate-300",
                          ["failed", "cancelled"].includes(step.status) && "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
                          step.status === "pending" && "bg-muted text-muted-foreground"
                        )}>
                          {step.step_index}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold">{step.label}</p>
                          <p className="mt-1 text-muted-foreground">
                            {step.status === "skipped_checkpoint" ? `체크포인트 재사용${step.reused_from_job_id ? ` · #${step.reused_from_job_id}` : ""}`
                              : step.status === "skipped_dependency" ? "관련 원천 변경 없음"
                              : step.message || step.status}
                          </p>
                          {step.total_units > 0 && step.status === "running" && (
                            <p className="mt-1 font-medium text-blue-700 dark:text-blue-300">
                              {formatNumber(step.current_units)}/{formatNumber(step.total_units)} {step.unit || "건"}
                              {formatEta(step.eta_seconds) ? ` · ${formatEta(step.eta_seconds)}` : ""}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}
              {selectedDataCheckSummary && (
                <div className="mb-3">
                  <FreshnessResultPanel
                    summary={selectedDataCheckSummary}
                    title="구조화된 최신 상태 점검 결과"
                  />
                </div>
              )}
              <details open={selectedJob.is_active} className="overflow-hidden rounded-lg border bg-card">
                <summary className="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 text-sm font-bold hover:bg-muted/50">
                  실행 로그
                  <span className="text-xs font-normal text-muted-foreground">최근 16,000자</span>
                </summary>
                <div className="bg-[#101715] p-4">
                  <pre className="scrollbar-natural max-h-[420px] overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-[#d8e5e1]">
                    {selectedJob.log || "아직 기록된 로그가 없습니다."}
                  </pre>
                </div>
              </details>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
