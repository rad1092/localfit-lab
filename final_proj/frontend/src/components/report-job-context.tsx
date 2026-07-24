"use client";

import {
  AUTH_CHANGED_EVENT,
  apiUrl,
  fetchAuth,
  logProductEvent,
} from "@/lib/api";
import { CheckCircle2, CircleAlert, LoaderCircle, X } from "lucide-react";
import Link from "next/link";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

const REPORT_JOB_STORAGE_KEY = "localfit:active-report-job";
const REPORT_JOB_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;
const MAX_POLL_FAILURES = 8;

export type ReportJobType = "single" | "comparison";
export type ReportJobStatus =
  | "submitting"
  | "queued"
  | "running"
  | "completed"
  | "failed";

export interface ReportJobContext {
  areaCode?: string;
  areaName?: string;
  eventAreaCode?: string;
  industryName?: string;
  budgetManwon?: number | null;
  comparisonCount?: number;
  reportLabel?: string;
}

export interface ReportJob {
  id: string;
  reportType: ReportJobType;
  status: ReportJobStatus;
  progressMessage: string;
  context: ReportJobContext;
  result?: Record<string, unknown>;
  errorMessage?: string;
  createdAt?: string;
  startedAt?: string;
  completedAt?: string;
}

interface ReportJobServerResponse {
  job_id: string;
  report_type: ReportJobType;
  status: Exclude<ReportJobStatus, "submitting">;
  progress_message?: string;
  result?: Record<string, unknown> | null;
  error_message?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
}

interface ReportJobContextValue {
  job: ReportJob | null;
  isHydrated: boolean;
  startJob: (
    reportType: ReportJobType,
    payload: Record<string, unknown>,
    context?: ReportJobContext,
  ) => Promise<string>;
  resumeJob: (jobId: string, context?: ReportJobContext) => void;
  dismissJob: () => void;
}

const ReportJobStateContext = createContext<ReportJobContextValue | null>(null);

function isActiveStatus(status?: ReportJobStatus) {
  return status === "submitting" || status === "queued" || status === "running";
}

function createClientJobId() {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi?.randomUUID) return cryptoApi.randomUUID();
  if (cryptoApi) {
    const bytes = new Uint8Array(16);
    cryptoApi.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`.padEnd(32, "0");
}

function persistReportJob(job: ReportJob | null) {
  if (!job?.id) {
    localStorage.removeItem(REPORT_JOB_STORAGE_KEY);
    return;
  }
  const storedJob = {
    id: job.id,
    reportType: job.reportType,
    status: job.status,
    progressMessage: job.progressMessage,
    context: job.context,
    errorMessage: job.errorMessage,
    createdAt: job.createdAt,
    startedAt: job.startedAt,
    completedAt: job.completedAt,
  };
  localStorage.setItem(REPORT_JOB_STORAGE_KEY, JSON.stringify(storedJob));
}

function isExpiredJob(job: ReportJob) {
  const createdAt = Date.parse(job.createdAt || "");
  return Number.isFinite(createdAt) && Date.now() - createdAt > REPORT_JOB_RETENTION_MS;
}

function buildResultHref(job: ReportJob) {
  const params = new URLSearchParams({ reportJob: job.id });
  if (job.context.areaCode) params.set("areaCode", job.context.areaCode);
  return `/ai?${params.toString()}`;
}

function ReportJobBanner({
  job,
  onDismiss,
}: {
  job: ReportJob | null;
  onDismiss: () => void;
}) {
  if (!job) return null;

  const active = isActiveStatus(job.status);
  const completed = job.status === "completed";
  const failed = job.status === "failed";
  const title = completed
    ? "AI 리포트 생성 완료"
    : failed
      ? "AI 리포트 생성 실패"
      : "AI 리포트 생성 중";

  return (
    <aside
      aria-live="polite"
      className="fixed right-4 top-16 z-[90] w-[min(26rem,calc(100vw-2rem))] rounded-2xl border bg-card p-4 shadow-xl"
      role="status"
    >
      <div className="flex items-start gap-3">
        {active ? (
          <LoaderCircle className="mt-0.5 size-5 shrink-0 animate-spin text-primary" />
        ) : completed ? (
          <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-emerald-600" />
        ) : (
          <CircleAlert className="mt-0.5 size-5 shrink-0 text-destructive" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold">{title}</p>
          {job.context.reportLabel && (
            <p className="mt-0.5 truncate text-xs font-medium text-muted-foreground">
              {job.context.reportLabel}
            </p>
          )}
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {job.errorMessage || job.progressMessage}
          </p>
          {active && (
            <p className="mt-1 text-xs font-medium text-primary">
              다른 페이지를 둘러봐도 생성은 계속됩니다.
            </p>
          )}
          {completed && job.id && (
            <Link
              className="mt-3 inline-flex rounded-lg bg-primary px-3 py-2 text-xs font-bold text-primary-foreground hover:bg-primary/90"
              href={buildResultHref(job)}
            >
              완성된 리포트 보기
            </Link>
          )}
        </div>
        {!active && (
          <button
            aria-label="리포트 작업 알림 닫기"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={onDismiss}
            type="button"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
    </aside>
  );
}

export function ReportJobProvider({ children }: { children: ReactNode }) {
  const [job, setJob] = useState<ReportJob | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const jobRef = useRef<ReportJob | null>(null);
  const pollFailureCountRef = useRef(0);

  const setCurrentJob = useCallback((nextJob: ReportJob | null) => {
    jobRef.current = nextJob;
    setJob(nextJob);
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const stored = JSON.parse(
          localStorage.getItem(REPORT_JOB_STORAGE_KEY) || "null",
        ) as ReportJob | null;
        if (stored && isExpiredJob(stored)) {
          localStorage.removeItem(REPORT_JOB_STORAGE_KEY);
        } else if (!jobRef.current && stored?.id && stored.reportType && stored.status) {
          setCurrentJob({ ...stored, result: undefined });
        }
      } catch {
        localStorage.removeItem(REPORT_JOB_STORAGE_KEY);
      } finally {
        setHydrated(true);
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [setCurrentJob]);

  useEffect(() => {
    if (!hydrated) return;
    persistReportJob(job);
  }, [hydrated, job]);

  useEffect(() => {
    const clearForAuthChange = () => {
      pollFailureCountRef.current = 0;
      setCurrentJob(null);
      localStorage.removeItem(REPORT_JOB_STORAGE_KEY);
    };
    window.addEventListener(AUTH_CHANGED_EVENT, clearForAuthChange);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, clearForAuthChange);
  }, [setCurrentJob]);

  const refreshJob = useCallback(async (jobId: string) => {
    const response = await fetchAuth(apiUrl(`/reports/jobs/${jobId}`), {
      cache: "no-store",
    });
    if (!response.ok) {
      if (response.status !== 404 && response.status < 500 && jobRef.current?.id === jobId) {
        pollFailureCountRef.current = 0;
        setCurrentJob({
          ...jobRef.current,
          status: "failed",
          progressMessage: "리포트 작업 상태를 확인하지 못했습니다.",
          errorMessage: "로그인 상태 또는 작업 접근 권한을 확인해 주세요.",
        });
        return;
      }
      throw new Error(`report job status failed (${response.status})`);
    }

    const data = (await response.json()) as ReportJobServerResponse;
    pollFailureCountRef.current = 0;
    const previous = jobRef.current;
    const context = previous?.id === jobId ? previous.context : {};
    const missingCompletedResult = data.status === "completed" && !data.result;
    const normalizedStatus: ReportJobStatus = missingCompletedResult ? "failed" : data.status;
    const nextJob: ReportJob = {
      id: data.job_id,
      reportType: data.report_type,
      status: normalizedStatus,
      progressMessage: missingCompletedResult
        ? "완료된 리포트 결과를 찾지 못했습니다."
        : data.progress_message || "AI 리포트 작업 상태를 확인하고 있습니다.",
      context,
      result: data.result || undefined,
      errorMessage: missingCompletedResult
        ? "리포트 결과 데이터가 없습니다."
        : data.error_message || undefined,
      createdAt: data.created_at,
      startedAt: data.started_at || undefined,
      completedAt: data.completed_at || undefined,
    };
    setCurrentJob(nextJob);

    if (
      previous?.id === jobId
      && previous.status !== normalizedStatus
      && (normalizedStatus === "completed" || normalizedStatus === "failed")
      && (context.eventAreaCode || context.areaCode)
    ) {
      void logProductEvent(
        normalizedStatus === "completed" ? "report_completed" : "report_failed",
        { area_code: context.eventAreaCode || context.areaCode },
      ).catch(() => undefined);
    }
  }, [setCurrentJob]);

  const shouldPoll = Boolean(
    job?.id
    && (
      isActiveStatus(job.status)
      || (job.status === "completed" && !job.result)
    ),
  );

  useEffect(() => {
    if (!job?.id || !shouldPoll) return;
    let stopped = false;
    let timer: number | undefined;
    const jobId = job.id;

    const poll = async () => {
      try {
        await refreshJob(jobId);
      } catch {
        const failures = pollFailureCountRef.current + 1;
        pollFailureCountRef.current = failures;
        const current = jobRef.current;
        if (current?.id === jobId) {
          if (failures >= MAX_POLL_FAILURES) {
            setCurrentJob({
              ...current,
              status: "failed",
              progressMessage: "리포트 작업 상태 확인을 중단했습니다.",
              errorMessage: "서버 연결을 확인한 뒤 이 리포트 링크를 다시 열어 주세요.",
            });
          } else {
            setCurrentJob({
              ...current,
              progressMessage: `서버 연결 재시도 중 (${failures}/${MAX_POLL_FAILURES})`,
            });
          }
        }
      } finally {
        if (!stopped) timer = window.setTimeout(poll, 1_500);
      }
    };
    void poll();

    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [job?.id, refreshJob, setCurrentJob, shouldPoll]);

  const startJob = useCallback(async (
    reportType: ReportJobType,
    payload: Record<string, unknown>,
    context: ReportJobContext = {},
  ) => {
    if (isActiveStatus(jobRef.current?.status)) {
      throw new Error("이미 생성 중인 AI 리포트가 있습니다.");
    }

    const previousJob = jobRef.current;
    const clientJobId = createClientJobId();
    const submittingJob: ReportJob = {
      id: clientJobId,
      reportType,
      status: "submitting",
      progressMessage: "백엔드에 리포트 작업을 등록하고 있습니다.",
      context,
      createdAt: new Date().toISOString(),
    };
    pollFailureCountRef.current = 0;
    persistReportJob(submittingJob);
    setCurrentJob(submittingJob);

    try {
      const response = await fetchAuth(apiUrl(`/reports/jobs/${reportType}`), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-LocalFit-Report-Job": clientJobId,
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const message = typeof body?.detail === "string"
          ? body.detail
          : typeof body?.detail?.message === "string"
            ? body.detail.message
            : "AI 리포트 작업을 시작하지 못했습니다.";
        throw new Error(message);
      }

      const data = (await response.json()) as ReportJobServerResponse;
      const acceptedJob: ReportJob = {
        id: data.job_id,
        reportType: data.report_type,
        status: data.status,
        progressMessage: "AI 리포트 생성 대기 중",
        context,
        createdAt: data.created_at || submittingJob.createdAt,
      };
      persistReportJob(acceptedJob);
      setCurrentJob(acceptedJob);
      return data.job_id;
    } catch (error) {
      persistReportJob(previousJob);
      setCurrentJob(previousJob);
      throw error;
    }
  }, [setCurrentJob]);

  const resumeJob = useCallback((jobId: string, context: ReportJobContext = {}) => {
    if (!jobId) return;
    const current = jobRef.current;
    if (current?.id === jobId) {
      if (
        current.status === "failed"
        && current.errorMessage === "서버 연결을 확인한 뒤 이 리포트 링크를 다시 열어 주세요."
      ) {
        const retryJob: ReportJob = {
          ...current,
          status: "queued",
          progressMessage: "저장된 리포트 작업을 다시 확인하고 있습니다.",
          errorMessage: undefined,
          context: { ...current.context, ...context },
        };
        pollFailureCountRef.current = 0;
        persistReportJob(retryJob);
        setCurrentJob(retryJob);
      }
      return;
    }
    if (isActiveStatus(current?.status)) return;
    const resumedJob: ReportJob = {
      id: jobId,
      reportType: "single",
      status: "queued",
      progressMessage: "저장된 리포트 작업을 다시 확인하고 있습니다.",
      context,
    };
    pollFailureCountRef.current = 0;
    persistReportJob(resumedJob);
    setCurrentJob(resumedJob);
  }, [setCurrentJob]);

  const dismissJob = useCallback(() => {
    pollFailureCountRef.current = 0;
    setCurrentJob(null);
    localStorage.removeItem(REPORT_JOB_STORAGE_KEY);
  }, [setCurrentJob]);

  const value = useMemo<ReportJobContextValue>(() => ({
    job,
    isHydrated: hydrated,
    startJob,
    resumeJob,
    dismissJob,
  }), [dismissJob, hydrated, job, resumeJob, startJob]);

  return (
    <ReportJobStateContext.Provider value={value}>
      {children}
      <ReportJobBanner job={job} onDismiss={dismissJob} />
    </ReportJobStateContext.Provider>
  );
}

export function useReportJob() {
  const context = useContext(ReportJobStateContext);
  if (!context) {
    throw new Error("useReportJob must be used within ReportJobProvider");
  }
  return context;
}
