"use client";
import { apiUrl, fetchAuth } from "@/lib/api";
import { displayGradeOrPending } from "@/lib/score-grade";


import { useEffect, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { LoginModal } from "@/components/LoginModal";
import {
  TwoTierNewsEvidence,
  type NewsEvidenceItem,
} from "@/components/TwoTierNewsEvidence";

interface Favorite {
  id: number;
  area_code: string;
  area_name: string;
  district_code: string;
}

interface RadarMetric {
  subject: string;
  scores: Record<string, number | null>;
}

interface AreaSwot {
  area_name: string;
  pros: string[];
  cons: string[];
}

interface ReportHeader {
  score_label?: string;
  score?: string;
  grade?: string;
  display_grade?: string;
  percentile?: string;
}

interface ReportAxis {
  axis: string;
  interpretation_level?: string;
  grade?: string | null;
  display_grade?: string | null;
}

interface ReportSourceCitation {
  provider?: string;
  dataset_name?: string;
  title?: string;
  source_url?: string;
  period?: string;
  used_for?: string;
  caveat?: string;
}

interface ReportAlternative {
  area_name?: string;
  score?: string | number | null;
  grade?: string | null;
  display_grade?: string | null;
  judgement?: string;
}

interface ReportAlternativeArea {
  area_name?: string;
  reason?: string;
}

interface SavedReportData {
  type?: "single" | "comparison";
  area_name?: string;
  summary?: string;
  score_interpretation?: string;
  executive_interpretation?: string;
  header_block?: ReportHeader;
  strengths?: string[];
  weaknesses?: string[];
  recommended_businesses?: string[];
  risk_factors?: string[];
  radar_metrics?: RadarMetric[];
  axis_interpretations?: ReportAxis[];
  evidence_basis?: string[];
  methodology_notes?: string[];
  limitations?: string[];
  source_citations?: ReportSourceCitation[];
  news_evidence?: NewsEvidenceItem[];
  alternatives?: ReportAlternative[];
  alternative_areas?: ReportAlternativeArea[];
  top_recommendation_name?: string;
  top_recommendation_reason?: string;
  swot_analysis?: AreaSwot[];
  generation_mode?: "llm" | "partial_fallback" | "deterministic";
  original_validation_issues?: string[];
  quality_status?: string;
}

interface ChatbotResultData {
  location_suitability?: string;
  business_suitability?: string;
  budget_adequacy?: string;
  swot_pros?: string[];
  swot_cons?: string[];
  overall_summary?: string;
}

interface SavedReport {
  id: number;
  report_data: SavedReportData;
  created_at: string;
}

interface ChatbotHistory {
  id: number;
  area_name: string;
  business_type: string;
  budget: number;
  result_data: ChatbotResultData;
  created_at: string;
}

function subscribeToGuestMode(onStoreChange: () => void) {
  window.addEventListener("storage", onStoreChange);
  return () => window.removeEventListener("storage", onStoreChange);
}

function getGuestModeSnapshot() {
  return typeof window !== "undefined" && window.localStorage.getItem("guest_mode") === "true";
}

function getServerGuestModeSnapshot() {
  return false;
}

function savedGenerationModeLabel(mode?: SavedReportData["generation_mode"]) {
  const labels: Record<string, string> = {
    llm: "AI 해석",
    partial_fallback: "AI 해석 · 일부 규칙 보정",
    deterministic: "규칙 기반 결과",
  };
  return (mode && labels[mode]) || "이전 리포트 · 생성 방식 기록 없음";
}

function savedReportText(text?: string | null) {
  return String(text || "")
    .replace(/\s*\[CHART:C[1-5]\]\s*/g, " ")
    .replace(/\s*\[NEWS:\d+\]\s*/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
}

export default function MyPage() {
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [savedReports, setSavedReports] = useState<SavedReport[]>([]);
  const [chatbotHistory, setChatbotHistory] = useState<ChatbotHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [isAnonymous, setIsAnonymous] = useState(false);
  
  // For viewing saved report modal
  const [viewingReport, setViewingReport] = useState<SavedReport | null>(null);
  const [viewingChatbot, setViewingChatbot] = useState<ChatbotHistory | null>(null);
  
  const [showLoginModal, setShowLoginModal] = useState(true);
  const isGuest = useSyncExternalStore(
    subscribeToGuestMode,
    getGuestModeSnapshot,
    getServerGuestModeSnapshot,
  );

  useEffect(() => {
    let cancelled = false;

    const readArray = async <T,>(path: string): Promise<T[]> => {
      const response = await fetchAuth(apiUrl(path));
      if (!response.ok) return [];
      const payload = await response.json();
      return Array.isArray(payload) ? payload : [];
    };

    const load = async () => {
      if (getGuestModeSnapshot()) {
        if (!cancelled) setLoading(false);
        return;
      }
      if (!window.localStorage.getItem("token")) {
        if (!cancelled) {
          setIsAnonymous(true);
          setLoading(false);
        }
        return;
      }

      try {
        const [favData, repData, chatbotData] = await Promise.all([
          readArray<Favorite>("/favorites"),
          // The backend collection route is `/reports/`. Avoid FastAPI's slash
          // redirect here because some browsers drop Authorization on the
          // redirected cross-origin request (3000 -> 8000), which logs the user
          // out and leaves the page in its loading state.
          readArray<SavedReport>("/reports/"),
          readArray<ChatbotHistory>("/chatbot/history"),
        ]);
        if (cancelled) return;
        setFavorites(favData);
        setSavedReports(repData);
        setChatbotHistory(chatbotData);
      } catch (error) {
        console.error(error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const removeFavorite = async (areaCode: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      await fetchAuth(apiUrl(`/favorites/${areaCode}`), { method: "DELETE" });
      setFavorites(favorites.filter(f => f.area_code !== areaCode));
    } catch (err) {
      console.error(err);
    }
  };

  const removeReport = async (reportId: number, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("정말 이 리포트를 삭제하시겠습니까?")) return;
    try {
      await fetchAuth(apiUrl(`/reports/${reportId}`), { method: "DELETE" });
      setSavedReports(savedReports.filter(r => r.id !== reportId));
    } catch (err) {
      console.error(err);
    }
  };

  const removeChatbotHistory = async (historyId: number, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("정말 이 상담 내역을 삭제하시겠습니까?")) return;
    try {
      await fetchAuth(apiUrl(`/chatbot/history/${historyId}`), { method: "DELETE" });
      setChatbotHistory(chatbotHistory.filter(h => h.id !== historyId));
    } catch (err) {
      console.error(err);
    }
  };

  const singleReports = savedReports.filter(r => r.report_data?.type === "single");
  const comparisonReports = savedReports.filter(r => r.report_data?.type !== "single");
  const COLORS = ["#8b5cf6", "#10b981", "#f59e0b", "#ef4444", "#3b82f6", "#ec4899"];

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-8 pb-16">
      <div className="flex items-center justify-between rounded-2xl border bg-card p-5 surface-shadow sm:p-7">
        <div>
          <h1 className="text-3xl font-bold">마이페이지</h1>
        </div>
      </div>

      {(isGuest || isAnonymous) && (
        <LoginModal isOpen={showLoginModal} onClose={() => {
          setShowLoginModal(false);
          if (typeof window !== "undefined") window.location.href = "/";
        }} />
      )}

      {loading && !isGuest && !isAnonymous ? (
        <div className="py-12 text-center text-muted-foreground animate-pulse border rounded-xl bg-card">
          데이터를 불러오는 중입니다...
        </div>
      ) : isGuest || isAnonymous ? (
        <div className="flex flex-col items-center justify-center py-24 text-center text-muted-foreground border rounded-xl bg-card border-dashed">
          <p className="text-lg font-semibold text-foreground mb-2">
            {isGuest ? "게스트 모드에서는 마이페이지를 이용할 수 없습니다." : "마이페이지는 로그인 후 이용할 수 있습니다."}
          </p>
          <p className="text-sm">즐겨찾기 및 상담 내역을 관리하려면 로그인해주세요.</p>
        </div>
      ) : (
        <>
          {/* Favorites Section */}
          <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
            <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
              ⭐ 즐겨찾기 목록
              <span className="bg-primary/10 text-primary text-sm px-2 py-0.5 rounded-full">
                {favorites.length}
              </span>
            </h3>
            
            {favorites.length === 0 ? (
              <div className="py-12 text-center border rounded-lg bg-muted/20 border-dashed">
                <p className="text-lg font-medium">아직 즐겨찾기한 상권이 없습니다.</p>
                <Link href="/trade" className="inline-block mt-4 px-4 py-2 bg-primary text-primary-foreground rounded-md text-sm font-medium">
                  상권 분석으로 이동
                </Link>
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {favorites.map((fav) => (
                  <Link 
                    key={fav.area_code} 
                    href={`/trade?area=${fav.area_code}`}
                    className="group relative block rounded-xl border bg-card p-5 transition-all hover:-translate-y-0.5 hover:border-primary hover:shadow-md"
                  >
                    <div className="font-bold text-lg mb-1 group-hover:text-primary transition-colors">
                      {fav.area_name}
                    </div>
                    <div className="text-sm text-muted-foreground mb-4">
                      상권코드: {fav.area_code}
                    </div>
                    <button 
                      onClick={(e) => removeFavorite(fav.area_code, e)}
                      className="absolute top-4 right-4 text-muted-foreground hover:text-destructive transition-colors p-1"
                      title="즐겨찾기 삭제"
                    >
                      ✕
                    </button>
                    <div className="text-xs font-medium text-primary mt-2 flex items-center gap-1">
                      분석 결과 보기 <span>→</span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>

          {/* Chatbot History Section */}
          <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
            <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
              💬 챗봇 상담 내역 보관함
              <span className="bg-indigo-600/10 text-indigo-600 text-sm px-2 py-0.5 rounded-full">
                {chatbotHistory.length}
              </span>
            </h3>
            
            {chatbotHistory.length === 0 ? (
              <div className="py-8 text-center border rounded-lg bg-muted/20 border-dashed text-muted-foreground">
                저장된 챗봇 상담 내역이 없습니다.
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {chatbotHistory.map((history) => (
                  <button 
                    key={history.id}
                    onClick={() => setViewingChatbot(history)}
                    className="group relative flex min-h-[140px] flex-col justify-between rounded-xl border bg-card p-5 text-left transition-all hover:-translate-y-0.5 hover:border-primary hover:shadow-md"
                  >
                    <div>
                      <div className="text-xs text-indigo-500 font-bold mb-2">AI 창업 컨설팅</div>
                      <div className="font-bold text-lg mb-1 group-hover:text-indigo-600 transition-colors line-clamp-1">
                        {history.area_name} - {history.business_type}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        예산: {history.budget.toLocaleString()}만원
                      </div>
                      <div className="text-xs text-muted-foreground mt-2">
                        {new Date(history.created_at).toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute:'2-digit' })}
                      </div>
                    </div>
                    <button 
                      onClick={(e) => removeChatbotHistory(history.id, e)}
                      className="absolute top-4 right-4 text-muted-foreground hover:text-destructive transition-colors p-1"
                      title="상담 내역 삭제"
                    >
                      ✕
                    </button>
                    <div className="text-xs font-medium text-indigo-600 mt-4 flex items-center gap-1">
                      상담 기록 열람하기 <span>→</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* Single AI Reports Section */}
          <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
            <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
              📊 단일 상권 리포트 보관함
              <span className="bg-indigo-600/10 text-indigo-600 text-sm px-2 py-0.5 rounded-full">
                {singleReports.length}
              </span>
            </h3>
            
            {singleReports.length === 0 ? (
              <div className="py-8 text-center border rounded-lg bg-muted/20 border-dashed text-muted-foreground">
                저장된 단일 상권 리포트가 없습니다.
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {singleReports.map((report) => (
                  <button 
                    key={report.id}
                    onClick={() => setViewingReport(report)}
                    className="group relative flex min-h-[140px] flex-col justify-between rounded-xl border bg-card p-5 text-left transition-all hover:-translate-y-0.5 hover:border-primary hover:shadow-md"
                  >
                    <div>
                      <div className="text-xs text-indigo-500 font-bold mb-2">단일 상권 분석</div>
                      <div className="font-bold text-lg mb-1 group-hover:text-indigo-600 transition-colors line-clamp-1">
                        {report.report_data.area_name || "알 수 없는 상권"}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {new Date(report.created_at).toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute:'2-digit' })}
                      </div>
                    </div>
                    <button 
                      onClick={(e) => removeReport(report.id, e)}
                      className="absolute top-4 right-4 text-muted-foreground hover:text-destructive transition-colors p-1"
                      title="리포트 삭제"
                    >
                      ✕
                    </button>
                    <div className="text-xs font-medium text-indigo-600 mt-4 flex items-center gap-1">
                      리포트 열람하기 <span>→</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>

          {/* Comparison AI Reports Section */}
          <section className="rounded-2xl border bg-card p-4 shadow-sm sm:p-6">
            <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
              📑 다중 상권 비교 리포트 보관함
              <span className="bg-emerald-600/10 text-emerald-600 text-sm px-2 py-0.5 rounded-full">
                {comparisonReports.length}
              </span>
            </h3>
            
            {comparisonReports.length === 0 ? (
              <div className="py-8 text-center border rounded-lg bg-muted/20 border-dashed text-muted-foreground">
                저장된 비교 분석 리포트가 없습니다.
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {comparisonReports.map((report) => {
                  const areaNames = report.report_data.swot_analysis?.map((s) => s.area_name).join(" vs ") || "비교 리포트";
                  return (
                    <button 
                      key={report.id}
                      onClick={() => setViewingReport(report)}
                      className="group relative flex min-h-[140px] flex-col justify-between rounded-xl border bg-card p-5 text-left transition-all hover:-translate-y-0.5 hover:border-primary hover:shadow-md"
                    >
                      <div>
                        <div className="text-xs text-emerald-500 font-bold mb-2">상권 비교 분석</div>
                        <div className="font-bold text-[15px] leading-tight mb-2 group-hover:text-emerald-600 transition-colors line-clamp-2">
                          {areaNames}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {new Date(report.created_at).toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute:'2-digit' })}
                        </div>
                      </div>
                      <button 
                        onClick={(e) => removeReport(report.id, e)}
                        className="absolute top-4 right-4 text-muted-foreground hover:text-destructive transition-colors p-1"
                        title="리포트 삭제"
                      >
                        ✕
                      </button>
                      <div className="text-xs font-medium text-emerald-600 mt-4 flex items-center gap-1">
                        리포트 열람하기 <span>→</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        </>
      )}

      {/* CHATBOT VIEWER MODAL */}
      {viewingChatbot && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-2 backdrop-blur-sm sm:p-4">
          <div className="flex max-h-[calc(100dvh-1rem)] w-full max-w-2xl flex-col overflow-hidden rounded-xl border bg-card shadow-2xl animate-in fade-in zoom-in-95 duration-200 sm:max-h-[90vh] sm:rounded-2xl">
            {/* Modal Header */}
            <div className="flex items-start justify-between gap-3 border-b p-4 sm:items-center sm:p-6">
              <div className="flex min-w-0 items-center gap-3">
                <div className="bg-indigo-600/20 text-indigo-600 p-2 rounded-lg text-xl">💬</div>
                <div>
                  <h2 className="text-xl font-bold">
                    AI 창업 컨설팅 기록
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    상담 일시: {new Date(viewingChatbot.created_at).toLocaleString('ko-KR')}
                  </p>
                </div>
              </div>
              <button onClick={() => setViewingChatbot(null)} className="text-muted-foreground hover:text-foreground text-2xl leading-none">&times;</button>
            </div>

            {/* Modal Body */}
            <div className="scrollbar-natural space-y-6 overflow-y-auto p-4 sm:p-6">
              {/* Request Info */}
              <div className="grid gap-3 rounded-xl bg-muted p-4 text-sm font-medium sm:grid-cols-3 sm:gap-4">
                <div><span className="text-muted-foreground text-xs block">희망 상권</span>{viewingChatbot.area_name}</div>
                <div><span className="text-muted-foreground text-xs block">업종</span>{viewingChatbot.business_type}</div>
                <div><span className="text-muted-foreground text-xs block">예산</span>{viewingChatbot.budget.toLocaleString()}만원</div>
              </div>

              {/* Analysis Results */}
              <div className="space-y-4">
                <div className="bg-card p-4 rounded-xl border shadow-sm text-sm space-y-3">
                  <h3 className="font-bold text-lg text-indigo-600 border-b pb-2 mb-2">분석 결과 리포트</h3>
                  <div>
                    <span className="font-bold text-emerald-600 block mb-1">📍 위치 적합성</span>
                    <p className="text-muted-foreground leading-relaxed">{viewingChatbot.result_data.location_suitability}</p>
                  </div>
                  <div>
                    <span className="font-bold text-blue-600 block mb-1">🏪 업종 적합성</span>
                    <p className="text-muted-foreground leading-relaxed">{viewingChatbot.result_data.business_suitability}</p>
                  </div>
                  <div>
                    <span className="font-bold text-amber-600 block mb-1">💰 예산 적정성</span>
                    <p className="text-muted-foreground leading-relaxed">{viewingChatbot.result_data.budget_adequacy}</p>
                  </div>
                </div>

                <div className="bg-card p-4 rounded-xl border shadow-sm text-sm">
                  <h3 className="font-bold text-indigo-600 mb-3">📊 SWOT 분석</h3>
                  <div className="space-y-3">
                    <div>
                      <span className="text-emerald-500 font-bold block mb-1">먼저 볼 근거</span>
                      <ul className="list-disc list-inside text-muted-foreground">
                        {viewingChatbot.result_data.swot_pros?.map((item: string, i: number) => <li key={i}>{item}</li>)}
                      </ul>
                    </div>
                    <div>
                      <span className="text-rose-500 font-bold block mb-1">현장 대조 항목</span>
                      <ul className="list-disc list-inside text-muted-foreground">
                        {viewingChatbot.result_data.swot_cons?.map((item: string, i: number) => <li key={i}>{item}</li>)}
                      </ul>
                    </div>
                  </div>
                </div>

                <div className="bg-indigo-50 border border-indigo-200 p-4 rounded-xl text-sm dark:bg-indigo-950/30 dark:border-indigo-800">
                  <span className="font-bold text-indigo-800 dark:text-indigo-300 block mb-1">💡 종합 의견</span>
                  <p className="text-indigo-900 dark:text-indigo-100 leading-relaxed">{viewingChatbot.result_data.overall_summary}</p>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="flex justify-end gap-4 border-t bg-muted/10 p-3 sm:p-4">
              <button onClick={() => setViewingChatbot(null)} className="bg-muted hover:bg-accent px-8 py-2 rounded-md font-bold text-sm transition-colors">
                닫기
              </button>
            </div>
          </div>
        </div>
      )}

      {/* REPORT VIEWER MODAL */}
      {viewingReport && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-2 backdrop-blur-sm sm:p-4">
          <div className="flex max-h-[calc(100dvh-1rem)] w-full max-w-4xl flex-col overflow-hidden rounded-xl border bg-card shadow-2xl animate-in fade-in zoom-in-95 duration-200 sm:max-h-[90vh] sm:rounded-2xl">
            {/* Modal Header */}
            <div className="flex items-start justify-between gap-3 border-b p-4 sm:items-center sm:p-6">
              <div className="flex min-w-0 items-center gap-3">
                <div className={`p-2 rounded-lg text-xl ${viewingReport.report_data.type === "single" ? "bg-indigo-600/20 text-indigo-600" : "bg-emerald-600/20 text-emerald-600"}`}>
                  {viewingReport.report_data.type === "single" ? "✨" : "📑"}
                </div>
                <div>
                  <h2 className="break-words text-lg font-bold sm:text-xl">
                    {viewingReport.report_data.type === "single" 
                      ? `[${viewingReport.report_data.area_name}] 상세 입지 분석`
                      : "AI 상권 비교 분석"}
                  </h2>
                  <p className="text-sm text-muted-foreground">
                    저장된 날짜: {new Date(viewingReport.created_at).toLocaleString('ko-KR')}
                  </p>
                  {viewingReport.report_data.type === "single" && (
                    <p className="mt-1 text-xs font-semibold text-indigo-500">
                      {savedGenerationModeLabel(viewingReport.report_data.generation_mode)}
                    </p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={async () => {
                    const res = await fetchAuth(apiUrl(`/reports/${viewingReport.id}/download?format=pdf`));
                    if (!res.ok) return alert("PDF 다운로드 실패");
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url; a.download = `ai_report_${viewingReport.id}.pdf`; a.click();
                    URL.revokeObjectURL(url);
                  }}
                  className="rounded-md border px-3 py-1.5 text-xs font-semibold hover:bg-accent"
                >
                  PDF
                </button>
                <button onClick={() => setViewingReport(null)} className="text-muted-foreground hover:text-foreground text-2xl leading-none">&times;</button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="scrollbar-natural space-y-6 overflow-y-auto p-4 sm:p-6">
              
              {viewingReport.report_data.type === "single" ? (
                // SINGLE REPORT VIEW
                <>
                  <div className="rounded-xl bg-gradient-to-br from-indigo-950 to-purple-900 p-6 text-white shadow-lg border border-indigo-800">
                    <h3 className="font-bold text-yellow-400 mb-2 flex items-center gap-2">◎ 종합 요약</h3>
                    <p className="text-sm leading-relaxed text-indigo-50">
                      {savedReportText(viewingReport.report_data.summary)}
                    </p>
                    <div className="mt-4 pt-4 border-t border-white/20">
                      <span className="text-xs font-semibold uppercase text-indigo-200">AI interpretation</span>
                      <p className="mt-2 text-sm leading-relaxed text-indigo-50">
                        {savedReportText(viewingReport.report_data.score_interpretation || viewingReport.report_data.executive_interpretation || "정량평가를 해석 기준과 근거자료로 풀어낸 결과입니다.")}
                      </p>
                    </div>
                    {viewingReport.report_data.header_block && (
                      <dl className="mt-4 border-y border-white/20 py-3 text-center">
                        <div className="px-2">
                          <dt className="text-[10px] font-semibold text-indigo-200">입지 등급</dt>
                          <dd className="mt-1 text-2xl font-black">{displayGradeOrPending(viewingReport.report_data.header_block.display_grade, viewingReport.report_data.header_block.grade || viewingReport.report_data.header_block.score)}</dd>
                        </div>
                      </dl>
                    )}
                  </div>

                  <div className="grid md:grid-cols-2 gap-4">
                    <div className="rounded-xl border bg-card p-5">
                      <p className="text-emerald-500 font-bold mb-3 flex items-center gap-1">먼저 볼 근거</p>
                      <ul className="list-disc list-inside text-sm space-y-2 text-muted-foreground">
                        {viewingReport.report_data.strengths?.map((item: string, i: number) => <li key={i}>{savedReportText(item)}</li>)}
                      </ul>
                    </div>
                    <div className="rounded-xl border bg-card p-5">
                      <p className="text-rose-500 font-bold mb-3 flex items-center gap-1">현장 대조 항목</p>
                      <ul className="list-disc list-inside text-sm space-y-2 text-muted-foreground">
                        {viewingReport.report_data.weaknesses?.map((item: string, i: number) => <li key={i}>{savedReportText(item)}</li>)}
                      </ul>
                    </div>
                  </div>

                  <div className="grid md:grid-cols-2 gap-4">
                    <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 p-5">
                      <p className="text-amber-600 font-bold mb-3 flex items-center gap-1">💡 추천 창업 업종</p>
                      <ul className="list-disc list-inside text-sm space-y-2 text-muted-foreground">
                        {viewingReport.report_data.recommended_businesses?.map((item: string, i: number) => <li key={i}>{item}</li>)}
                      </ul>
                    </div>
                    <div className="rounded-xl border border-blue-500/20 bg-blue-500/5 p-5">
                      <p className="text-blue-600 font-bold mb-3 flex items-center gap-1">⚠️ 주의 및 위험 요소</p>
                      <ul className="list-disc list-inside text-sm space-y-2 text-muted-foreground">
                        {viewingReport.report_data.risk_factors?.map((item: string, i: number) => <li key={i}>{savedReportText(item)}</li>)}
                      </ul>
                    </div>
                  </div>
                  
                  <div className="rounded-xl border bg-card p-5 mt-4">
                    <h3 className="font-bold mb-4 text-center">4대 지표 등급</h3>
                    {(viewingReport.report_data.axis_interpretations || []).length > 0 ? (
                      <dl className="divide-y border-y">
                        {(viewingReport.report_data.axis_interpretations || []).map((axis) => (
                          <div key={axis.axis} className="flex items-center justify-between gap-4 py-3">
                            <dt className="text-sm font-semibold">{axis.axis}</dt>
                            <dd className="rounded-full bg-primary/10 px-3 py-1 font-black text-primary">
                              {displayGradeOrPending(axis.display_grade, axis.grade || axis.interpretation_level)}
                            </dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <p className="py-8 text-center text-sm text-muted-foreground">이 저장 리포트에는 축별 등급 정보가 없습니다.</p>
                    )}
                  </div>

                  {(
                    (viewingReport.report_data.alternatives || []).length > 0 ||
                    (viewingReport.report_data.alternative_areas || []).length > 0
                  ) && (
                    <section className="rounded-xl border bg-card p-5">
                      <h3 className="font-bold">대안 상권</h3>
                      {(viewingReport.report_data.alternatives || []).length > 0 && (
                        <div className="mt-3 overflow-x-auto rounded-lg border">
                          <table className="w-full min-w-[560px] text-left text-sm">
                            <thead className="border-b bg-muted/30 text-xs text-muted-foreground">
                              <tr><th className="px-3 py-2.5">상권</th><th className="px-3 py-2.5">입지 등급</th><th className="px-3 py-2.5">비교 판단</th></tr>
                            </thead>
                            <tbody className="divide-y">
                              {(viewingReport.report_data.alternatives || []).map((item, index) => (
                                <tr key={`${item.area_name || "alternative"}-${index}`}>
                                  <td className="px-3 py-3 font-bold">{item.area_name || "대안 상권"}</td>
                                  <td className="px-3 py-3 font-black text-primary">{displayGradeOrPending(item.display_grade, item.grade || (typeof item.score === "string" ? item.score : null))}</td>
                                  <td className="px-3 py-3 leading-6 text-muted-foreground">{item.judgement || "같은 업종 기준으로 함께 비교할 후보입니다."}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      {(viewingReport.report_data.alternative_areas || []).length > 0 && (
                        <ul className="mt-3 divide-y border-y text-sm">
                          {(viewingReport.report_data.alternative_areas || []).map((item, index) => (
                            <li key={`${item.area_name || "alternative-area"}-${index}`} className="grid gap-1 py-3 sm:grid-cols-[150px_1fr] sm:gap-4">
                              <span className="font-bold">{item.area_name || "대안 상권"}</span>
                              <span className="leading-6 text-muted-foreground">{item.reason || "비교 후보"}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </section>
                  )}

                  <TwoTierNewsEvidence
                    items={viewingReport.report_data.news_evidence || []}
                  />

                  {(
                    (viewingReport.report_data.source_citations || []).length > 0 ||
                    (viewingReport.report_data.evidence_basis || []).length > 0 ||
                    (viewingReport.report_data.methodology_notes || []).length > 0 ||
                    (viewingReport.report_data.limitations || []).length > 0 ||
                    Boolean(viewingReport.report_data.generation_mode) ||
                    (viewingReport.report_data.original_validation_issues || []).length > 0
                  ) && (
                    <details className="overflow-hidden rounded-xl border bg-card">
                      <summary className="cursor-pointer px-5 py-4 text-sm font-bold hover:bg-muted/40">
                        데이터 출처·등급 산정 기준·해석 범위
                      </summary>
                      <div className="scrollbar-natural max-h-96 space-y-6 overflow-y-auto border-t p-5 text-sm">
                        <section>
                          <h4 className="font-bold">생성 품질 기록</h4>
                          <p className="mt-2 text-muted-foreground">{savedGenerationModeLabel(viewingReport.report_data.generation_mode)}</p>
                          {(viewingReport.report_data.original_validation_issues || []).length > 0 && (
                            <details className="mt-3">
                              <summary className="cursor-pointer text-xs font-bold text-muted-foreground">검증 추적용 원문 보기</summary>
                              <ul className="mt-2 space-y-2 font-mono text-[11px] leading-5 text-muted-foreground">
                                {(viewingReport.report_data.original_validation_issues || []).map((issue, index) => (
                                  <li key={`saved-original-issue-${index}`} className="break-words">{issue}</li>
                                ))}
                              </ul>
                            </details>
                          )}
                        </section>
                        {(viewingReport.report_data.source_citations || []).length > 0 && (
                          <section>
                            <h4 className="font-bold">데이터 출처</h4>
                            <ul className="mt-3 space-y-3 text-muted-foreground">
                              {(viewingReport.report_data.source_citations || []).map((source, index) => (
                                <li key={`${source.provider || "source"}-${source.dataset_name || source.title || index}`} className="border-l-2 border-indigo-300 pl-3 leading-6">
                                  <p className="font-semibold text-foreground">{source.provider || "공공 데이터 원천"} · {source.dataset_name || source.title || "데이터셋"}</p>
                                  <p>{source.period || "기준시점 별도 표기"}{source.used_for ? ` · ${source.used_for}` : ""}</p>
                                  {source.caveat && <p className="text-xs">해석 범위: {source.caveat}</p>}
                                  {source.source_url && <a href={source.source_url} target="_blank" rel="noreferrer" className="text-xs font-semibold text-primary hover:underline">원문 보기</a>}
                                </li>
                              ))}
                            </ul>
                          </section>
                        )}
                        <div className="grid gap-6 md:grid-cols-2">
                          <section>
                            <h4 className="font-bold">등급 산정 기준</h4>
                            <ul className="mt-3 space-y-2 text-muted-foreground">
                              {[...(viewingReport.report_data.evidence_basis || []), ...(viewingReport.report_data.methodology_notes || [])].map((item, index) => (
                                <li key={`basis-${index}`} className="border-l-2 border-indigo-300 pl-3 leading-6">{item}</li>
                              ))}
                            </ul>
                          </section>
                          <section>
                            <h4 className="font-bold">해석 범위</h4>
                            <ul className="mt-3 space-y-2 text-muted-foreground">
                              {(viewingReport.report_data.limitations || []).map((item, index) => (
                                <li key={`limit-${index}`} className="border-l-2 border-slate-300 pl-3 leading-6">{item}</li>
                              ))}
                            </ul>
                          </section>
                        </div>
                      </div>
                    </details>
                  )}
                </>
              ) : (
                // COMPARISON REPORT VIEW
                <>
                  <div className="rounded-xl border bg-muted/30 p-5">
                    <h3 className="font-bold text-emerald-600 mb-2 flex items-center gap-2">◎ 비교 총평</h3>
                    <p className="text-sm leading-relaxed text-foreground">
                      {viewingReport.report_data.summary}
                    </p>
                  </div>

                  <div className="rounded-xl bg-gradient-to-br from-emerald-950 to-teal-900 p-6 text-white relative overflow-hidden shadow-lg border border-emerald-800">
                    <h3 className="font-semibold text-yellow-400 mb-1 flex items-center gap-2">수요·접근성 맥락 우선 후보</h3>
                    <h2 className="text-3xl font-extrabold mb-4">{viewingReport.report_data.top_recommendation_name}</h2>
                    <p className="text-sm text-emerald-50 leading-relaxed max-w-2xl">
                      {viewingReport.report_data.top_recommendation_reason}
                    </p>
                  </div>

                  <div>
                    <h3 className="font-bold mb-4 flex items-center gap-2">📊 각 상권별 장단점 요약</h3>
                    <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                      {viewingReport.report_data.swot_analysis?.map((swot, idx) => (
                        <div key={idx} className="rounded-xl border bg-card p-5 flex flex-col h-full">
                          <h4 className="text-lg font-bold border-b pb-2 mb-4" style={{ color: COLORS[idx % COLORS.length] }}>
                            {swot.area_name}
                          </h4>
                          <div className="mb-4">
                            <p className="text-emerald-500 font-bold text-sm mb-2 flex items-center gap-1">👍 장점 (Pros)</p>
                            <ul className="list-disc list-inside text-sm space-y-1 text-muted-foreground">
                              {swot.pros.map((pro: string, i: number) => <li key={i}>{pro}</li>)}
                            </ul>
                          </div>
                          <div className="flex-1">
                            <p className="text-rose-500 font-bold text-sm mb-2 flex items-center gap-1">👎 단점 (Cons)</p>
                            <ul className="list-disc list-inside text-sm space-y-1 text-muted-foreground">
                              {swot.cons.map((con: string, i: number) => <li key={i}>{con}</li>)}
                            </ul>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-xl border bg-card p-5 mt-4">
                    <h3 className="font-bold mb-2 text-center">상권 비교</h3>
                    <p className="text-center text-sm text-muted-foreground">상권별 해석과 강·약점은 위 카드에서 확인할 수 있습니다.</p>
                  </div>
                </>
              )}

            </div>

            {/* Modal Footer */}
            <div className="flex justify-end gap-4 border-t bg-muted/10 p-3 sm:p-4">
              <button onClick={() => setViewingReport(null)} className="bg-muted hover:bg-accent px-8 py-2 rounded-md font-bold text-sm transition-colors">
                닫기
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
