"use client";

import { LoginModal } from "@/components/LoginModal";
import { AUTH_CHANGED_EVENT, apiUrl, fetchAuth, fetchCurrentUser, type AuthUser } from "@/lib/api";
import type { AreaComment, CommentPageResponse } from "@/types/models";
import { CornerDownRight, LoaderCircle, MessageSquare, Pencil, Reply, Trash2 } from "lucide-react";
import { useCallback, useEffect, useId, useState } from "react";

interface CommentSectionProps {
  areaCode: string;
  industryCode?: string | null;
  industryName?: string | null;
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("ko-KR", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function CommentSection({ areaCode, industryCode, industryName }: CommentSectionProps) {
  const [scope, setScope] = useState<"area" | "industry">(industryCode ? "industry" : "area");
  const [comments, setComments] = useState<AreaComment[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [content, setContent] = useState("");
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [replyContent, setReplyContent] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editingContent, setEditingContent] = useState("");
  const [error, setError] = useState("");
  const [user, setUser] = useState<AuthUser | null>(null);
  const [showLogin, setShowLogin] = useState(false);
  const countId = useId();
  const effectiveIndustryCode = scope === "industry" ? industryCode || null : null;

  const loadComments = useCallback(async (nextPage = 1, append = false) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: String(nextPage), page_size: "20" });
      if (effectiveIndustryCode) params.set("industry_code", effectiveIndustryCode);
      const response = await fetch(apiUrl(`/areas/${encodeURIComponent(areaCode)}/comments?${params}`), {
        cache: "no-store",
      });
      if (!response.ok) throw new Error("댓글을 불러오지 못했습니다.");
      const payload = await response.json() as CommentPageResponse;
      setComments((current) => append ? [...current, ...payload.items] : payload.items);
      setPage(payload.page);
      setTotal(payload.total);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [areaCode, effectiveIndustryCode]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadComments(1), 0);
    const refreshUser = () => void fetchCurrentUser().then(setUser).catch(() => setUser(null));
    refreshUser();
    window.addEventListener(AUTH_CHANGED_EVENT, refreshUser);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener(AUTH_CHANGED_EVENT, refreshUser);
    };
  }, [loadComments]);

  const requireMember = () => {
    const loggedIn = Boolean(localStorage.getItem("token")) && localStorage.getItem("guest_mode") !== "true";
    if (!loggedIn) setShowLogin(true);
    return loggedIn;
  };

  const post = async (body: string, parentId?: number) => {
    if (!requireMember() || !body.trim()) return false;
    setSubmitting(true);
    setError("");
    try {
      const response = await fetchAuth(apiUrl(`/areas/${encodeURIComponent(areaCode)}/comments`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body: body.trim(),
          industry_code: effectiveIndustryCode,
          parent_id: parentId || null,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "댓글을 등록하지 못했습니다.");
      await loadComments(1);
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글을 등록하지 못했습니다.");
      return false;
    } finally {
      setSubmitting(false);
    }
  };

  const update = async (commentId: number) => {
    if (!editingContent.trim()) return;
    setSubmitting(true);
    try {
      const response = await fetchAuth(apiUrl(`/comments/${commentId}`), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: editingContent.trim() }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "댓글을 수정하지 못했습니다.");
      setEditingId(null);
      setEditingContent("");
      await loadComments(1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글을 수정하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (commentId: number) => {
    if (!window.confirm("이 댓글을 삭제할까요?")) return;
    setSubmitting(true);
    try {
      const response = await fetchAuth(apiUrl(`/comments/${commentId}`), { method: "DELETE" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.detail || "댓글을 삭제하지 못했습니다.");
      await loadComments(1);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "댓글을 삭제하지 못했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  const renderComment = (comment: AreaComment, reply = false) => {
    const owner = Boolean(user && comment.author?.id === user.id);
    const inactive = comment.status !== "visible";
    return (
      <article key={comment.id} className={`${reply ? "ml-6 border-t border-dashed sm:ml-10" : "border-b"} py-4`}>
        <div className="flex gap-3">
          {reply && <CornerDownRight className="mt-1 h-4 w-4 shrink-0 text-muted-foreground" />}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-sm">{inactive ? "표시되지 않는 댓글" : comment.author?.nickname || "알 수 없음"}</strong>
              <time className="text-[11px] text-muted-foreground">{formatDate(comment.created_at)}</time>
            </div>

            {editingId === comment.id ? (
              <div className="mt-2">
                <textarea value={editingContent} onChange={(event) => setEditingContent(event.target.value)} maxLength={1000} rows={3} className="w-full resize-y rounded-xl border bg-background p-3 text-sm outline-none focus:border-primary" />
                <div className="mt-2 flex justify-end gap-2">
                  <button type="button" onClick={() => setEditingId(null)} className="h-9 rounded-lg px-3 text-xs font-bold hover:bg-muted">취소</button>
                  <button type="button" onClick={() => void update(comment.id)} disabled={submitting || !editingContent.trim()} className="h-9 rounded-lg bg-primary px-3 text-xs font-bold text-primary-foreground disabled:opacity-50">저장</button>
                </div>
              </div>
            ) : (
              <p className={`mt-2 whitespace-pre-wrap break-words text-sm leading-6 ${inactive ? "italic text-muted-foreground" : ""}`}>{comment.body}</p>
            )}

            {!inactive && editingId !== comment.id && (
              <div className="mt-2 flex flex-wrap gap-3">
                {!reply && <button type="button" onClick={() => requireMember() && setReplyTo(replyTo === comment.id ? null : comment.id)} className="inline-flex items-center gap-1 text-xs font-bold text-muted-foreground hover:text-primary"><Reply className="h-3.5 w-3.5" /> 답글</button>}
                {owner && <button type="button" onClick={() => { setEditingId(comment.id); setEditingContent(comment.body); }} className="inline-flex items-center gap-1 text-xs font-bold text-muted-foreground hover:text-primary"><Pencil className="h-3.5 w-3.5" /> 수정</button>}
                {owner && <button type="button" onClick={() => void remove(comment.id)} className="inline-flex items-center gap-1 text-xs font-bold text-destructive"><Trash2 className="h-3.5 w-3.5" /> 삭제</button>}
              </div>
            )}

            {!reply && replyTo === comment.id && (
              <div className="mt-3 flex gap-2">
                <textarea value={replyContent} onChange={(event) => setReplyContent(event.target.value)} maxLength={1000} rows={2} placeholder="답글을 입력하세요" className="min-w-0 flex-1 resize-y rounded-xl border bg-background p-3 text-sm outline-none focus:border-primary" />
                <button type="button" disabled={submitting || !replyContent.trim()} onClick={() => void post(replyContent, comment.id).then((ok) => { if (ok) { setReplyTo(null); setReplyContent(""); } })} className="shrink-0 rounded-xl bg-primary px-4 text-sm font-bold text-primary-foreground disabled:opacity-50">등록</button>
              </div>
            )}
          </div>
        </div>
      </article>
    );
  };

  return (
    <section className="border-t bg-card p-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="flex items-center gap-2"><MessageSquare className="h-4 w-4 text-primary" /><h2 className="text-base font-black">상권 의견</h2></div>
          <p className="mt-1 text-xs text-muted-foreground">조회는 누구나 가능하며 작성은 로그인 후 이용할 수 있습니다.</p>
        </div>
        <span className="text-xs font-bold text-muted-foreground">{total.toLocaleString()}개</span>
      </div>

      {industryCode && (
        <div className="mt-4 grid grid-cols-2 rounded-xl bg-muted p-1" role="tablist" aria-label="댓글 범위">
          <button type="button" role="tab" aria-selected={scope === "area"} onClick={() => { setComments([]); setPage(1); setTotal(0); setScope("area"); }} className={`h-9 rounded-lg text-xs font-bold ${scope === "area" ? "bg-card text-primary shadow-sm" : "text-muted-foreground"}`}>상권 전체</button>
          <button type="button" role="tab" aria-selected={scope === "industry"} onClick={() => { setComments([]); setPage(1); setTotal(0); setScope("industry"); }} className={`h-9 rounded-lg text-xs font-bold ${scope === "industry" ? "bg-card text-primary shadow-sm" : "text-muted-foreground"}`}>{industryName || industryCode}</button>
        </div>
      )}

      <div className="mt-4">
        <textarea
          value={content}
          onChange={(event) => setContent(event.target.value)}
          onFocus={() => requireMember()}
          maxLength={1000}
          rows={3}
          aria-describedby={countId}
          placeholder="이 상권에 대한 경험이나 의견을 남겨주세요."
          className="w-full resize-y rounded-xl border bg-background p-3 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <span id={countId} className="text-xs tabular-nums text-muted-foreground">{content.length.toLocaleString()} / 1,000</span>
          <button type="button" disabled={submitting || !content.trim()} onClick={() => void post(content).then((ok) => ok && setContent(""))} className="h-10 rounded-xl bg-primary px-5 text-sm font-bold text-primary-foreground disabled:opacity-50">댓글 등록</button>
        </div>
      </div>

      {error && <p className="mt-4 rounded-xl bg-destructive/10 px-3 py-2 text-sm font-semibold text-destructive" role="alert">{error}</p>}

      <div className="mt-3">
        {loading && !comments.length ? (
          <p className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"><LoaderCircle className="h-4 w-4 animate-spin" /> 댓글을 불러오는 중입니다.</p>
        ) : comments.length ? (
          comments.map((comment) => <div key={comment.id}>{renderComment(comment)}{comment.replies.map((item) => renderComment(item, true))}</div>)
        ) : (
          <p className="py-10 text-center text-sm text-muted-foreground">아직 등록된 댓글이 없습니다.</p>
        )}
      </div>

      {comments.length < total && (
        <button type="button" disabled={loading} onClick={() => void loadComments(page + 1, true)} className="mt-4 h-10 w-full rounded-xl border text-sm font-bold hover:bg-muted disabled:opacity-50">댓글 더 보기</button>
      )}

      <LoginModal isOpen={showLogin} onClose={() => setShowLogin(false)} />
    </section>
  );
}
