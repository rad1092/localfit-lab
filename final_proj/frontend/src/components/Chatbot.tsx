"use client";

import { apiUrl, fetchAuth } from "@/lib/api";
import { BotMascot, type BotMascotMood } from "@/components/BotMascot";
import { BookmarkPlus, ExternalLink, Loader2, Maximize2, RefreshCcw, Send, X } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";


type ChatState = {
  area_code?: string | null;
  area_name?: string | null;
  industry_code?: string | null;
  business_type?: string | null;
  budget?: number | null;
  last_report_id?: number | null;
};

type ChatOption = {
  label: string;
  type: string;
  value: string;
  payload?: Partial<ChatState>;
};

type ChatMessage = {
  role: "user" | "assistant";
  type: "text" | "report";
  content: string;
  reportData?: ChatbotReport;
  isGuest?: boolean;
  message?: string;
  options?: ChatOption[];
};

type ChatbotReport = {
  area_code?: string;
  area_name?: string;
  condition?: {
    area_name: string;
    business_type?: string | null;
    budget?: number | null;
  };
  compact_response: {
    condition_summary: string;
    quick_judgement: string;
    main_risks: string[];
    alternative_areas: Array<{ area_code?: string; area_name: string; reason?: string; interpretation_level?: string }>;
    cta: string;
    report_id?: number;
    ai_explanation?: string;
    evidence_basis?: string[];
    source_citations?: Array<{ title?: string; source_path?: string; theme?: string; used_for?: string }>;
    recommended_strategy?: string[];
  };
  actions?: Array<{ label: string; type: string; target: string }>;
};

const initialText =
  "반가워요. 상권이나 업종을 아직 정하지 않았어도 괜찮아요. 돈, 경험, 아이디어처럼 지금 걸리는 부분부터 편하게 이야기해 주세요.";

function publicText(value?: string | null) {
  return String(value || "").replace(/\b(?:100|[1-9]?\d)(?:\.\d+)?\s*점(?!포)/g, "등급 기준");
}

export function Chatbot() {
  const pathname = usePathname();
  const [isOpen, setIsOpen] = useState(false);
  const [isMiniOpen, setIsMiniOpen] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: "assistant", type: "text", content: initialText }]);
  const [inputText, setInputText] = useState("");
  const [conversationState, setConversationState] = useState<ChatState>({});
  const [loading, setLoading] = useState(false);
  const [isSavingFavorite, setIsSavingFavorite] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [roamingMood, setRoamingMood] = useState<BotMascotMood>("idle");
  const [roamingPosition, setRoamingPosition] = useState({ x: 24, y: 84 });
  const [isDraggingMascot, setIsDraggingMascot] = useState(false);
  const [showPetCloseMenu, setShowPetCloseMenu] = useState(false);
  const [isMascotHidden, setIsMascotHidden] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const dragRef = useRef({ pointerId: -1, startX: 0, startY: 0, originX: 0, originY: 0, currentX: 0, currentY: 0, moved: false });
  const suppressClickRef = useRef(false);
  const router = useRouter();

  const mascotMinX = useCallback(() => 8, []);

  const closeChatbot = useCallback(() => {
    setIsOpen(false);
    window.setTimeout(() => openerRef.current?.focus(), 0);
  }, []);

  const showChatbot = useCallback(() => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (isOpen) {
      window.setTimeout(() => inputRef.current?.focus(), 0);
      return;
    }
    setIsMiniOpen(false);
    setIsOpen(true);
  }, [isOpen]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    window.addEventListener("localfit:open-chatbot", showChatbot);
    return () => {
      window.removeEventListener("localfit:open-chatbot", showChatbot);
    };
  }, [showChatbot]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 639px)");
    const updateViewport = () => setIsMobile(media.matches);
    updateViewport();
    media.addEventListener("change", updateViewport);
    return () => media.removeEventListener("change", updateViewport);
  }, []);

  useEffect(() => {
    const clamp = (position: { x: number; y: number }) => ({
      x: Math.min(Math.max(mascotMinX(), position.x), Math.max(mascotMinX(), window.innerWidth - 104)),
      y: Math.min(Math.max(68, position.y), Math.max(68, window.innerHeight - 128)),
    });
    const restorePosition = () => {
      try {
        const saved = JSON.parse(localStorage.getItem("localfit_bot_position") || "null");
        if (typeof saved?.x === "number" && typeof saved?.y === "number") {
          setRoamingPosition(clamp(saved));
          return;
        }
      } catch {
        localStorage.removeItem("localfit_bot_position");
      }
      setRoamingPosition(clamp({ x: window.innerWidth - 112, y: window.innerHeight - 150 }));
    };
    const keepInsideViewport = () => setRoamingPosition((current) => clamp(current));
    const initialPosition = window.setTimeout(restorePosition, 0);
    window.addEventListener("resize", keepInsideViewport);
    return () => {
      window.clearTimeout(initialPosition);
      window.removeEventListener("resize", keepInsideViewport);
    };
  }, [mascotMinX]);

  useEffect(() => {
    if (!isOpen) return;

    window.setTimeout(() => inputRef.current?.focus(), 0);
    const panel = panelRef.current;
    const siblingStates: Array<{ element: HTMLElement; inert: boolean; ariaHidden: string | null }> = [];

    if (isMobile && panel?.parentElement) {
      Array.from(panel.parentElement.children).forEach((node) => {
        if (node === panel || !(node instanceof HTMLElement)) return;
        siblingStates.push({ element: node, inert: node.inert, ariaHidden: node.getAttribute("aria-hidden") });
        node.inert = true;
        node.setAttribute("aria-hidden", "true");
      });
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeChatbot();
        return;
      }
      if (event.key !== "Tab" || !panel) return;

      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
        )
      ).filter((element) => !element.hasAttribute("hidden"));
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      siblingStates.forEach(({ element, inert, ariaHidden }) => {
        element.inert = inert;
        if (ariaHidden === null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
      });
    };
  }, [closeChatbot, isMobile, isOpen]);

  const mergeState = (option?: ChatOption): ChatState => ({
    ...conversationState,
    ...(option?.payload || {}),
  });

  const sendMessage = async (text: string, option?: ChatOption) => {
    const trimmed = text.trim();
    if (!trimmed) return;

    const nextState = mergeState(option);
    const nextMessages: ChatMessage[] = [
      ...messages,
      { role: "user", type: "text", content: option?.label || trimmed },
    ];
    setMessages(nextMessages);
    setInputText("");
    setConversationState(nextState);
    setLoading(true);
    setRoamingMood("thinking");

    try {
      const res = await fetchAuth(apiUrl("/chatbot/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmed,
          state: nextState,
          history: nextMessages.map((message) => ({ role: message.role, content: message.content })),
        }),
      });

      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || "상담 요청을 처리하지 못했습니다.");
      }

      const data = await res.json();
      if (data.state) setConversationState(data.state);

      const options: ChatOption[] =
        Array.isArray(data.option_payloads) && data.option_payloads.length > 0
          ? data.option_payloads
          : Array.isArray(data.options)
            ? data.options.map((label: string) => ({ label, type: "text", value: label }))
            : [];

      if (data.type === "report" && data.report) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            type: "report",
            content: "분석 리포트를 만들었어요.",
            reportData: data.report,
            isGuest: data.is_guest,
            message: data.message,
          },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            type: "text",
            content: data.text || "조금만 더 구체적으로 말해줄래요?",
            isGuest: data.is_guest,
            message: data.message,
            options,
          },
        ]);
      }
      setRoamingMood("success");
      window.setTimeout(() => setRoamingMood("idle"), 1200);
    } catch (err) {
      const message = err instanceof Error ? err.message : "알 수 없는 오류가 발생했습니다.";
      setMessages((prev) => [...prev, { role: "assistant", type: "text", content: message }]);
      setRoamingMood("error");
      window.setTimeout(() => setRoamingMood("idle"), 1500);
    } finally {
      setLoading(false);
    }
  };

  const handleSaveFavorite = async (areaCode?: string) => {
    if (!areaCode) return;
    if (typeof window !== "undefined" && !localStorage.getItem("token")) {
      alert("로그인이 필요한 기능입니다.");
      return;
    }
    setIsSavingFavorite(true);
    try {
      const res = await fetchAuth(apiUrl(`/favorites/${areaCode}`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!res.ok) throw new Error("즐겨찾기 저장 실패");
      alert("즐겨찾기에 추가했습니다.");
    } catch {
      alert("즐겨찾기 저장 중 오류가 발생했습니다.");
    } finally {
      setIsSavingFavorite(false);
    }
  };

  const resetConversation = () => {
    setConversationState({});
    setMessages([{ role: "assistant", type: "text", content: initialText }]);
    setInputText("");
    setRoamingMood("success");
    window.setTimeout(() => setRoamingMood("idle"), 1200);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const dragMascot = {
    onPointerDown: (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0) return;
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        originX: roamingPosition.x,
        originY: roamingPosition.y,
        currentX: roamingPosition.x,
        currentY: roamingPosition.y,
        moved: false,
      };
      setIsDraggingMascot(true);
    },
    onPointerMove: (event: ReactPointerEvent<HTMLButtonElement>) => {
      const drag = dragRef.current;
      if (drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startX;
      const dy = event.clientY - drag.startY;
      if (Math.hypot(dx, dy) > 5) drag.moved = true;
      const nextPosition = {
        x: Math.min(Math.max(mascotMinX(), drag.originX + dx), Math.max(mascotMinX(), window.innerWidth - 104)),
        y: Math.min(Math.max(68, drag.originY + dy), Math.max(68, window.innerHeight - 128)),
      };
      drag.currentX = nextPosition.x;
      drag.currentY = nextPosition.y;
      setRoamingPosition(nextPosition);
    },
    onPointerUp: (event: ReactPointerEvent<HTMLButtonElement>) => {
      const drag = dragRef.current;
      if (drag.pointerId !== event.pointerId) return;
      event.currentTarget.releasePointerCapture(event.pointerId);
      suppressClickRef.current = drag.moved;
      dragRef.current.pointerId = -1;
      setIsDraggingMascot(false);
      localStorage.setItem("localfit_bot_position", JSON.stringify({ x: drag.currentX, y: drag.currentY }));
    },
  };

  const activateMascot = () => {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    setShowPetCloseMenu(false);
    if (isOpen) {
      closeChatbot();
      setRoamingMood("idle");
      return;
    }
    setIsMiniOpen((current) => !current);
    setRoamingMood(isMiniOpen ? "idle" : "listening");
    if (!isMiniOpen) window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  const openPetCloseMenu = (event: ReactMouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsMiniOpen(false);
    setShowPetCloseMenu(true);
    setRoamingMood("error");
  };

  const hideMascotForNow = () => {
    setShowPetCloseMenu(false);
    setIsMascotHidden(true);
    setIsMiniOpen(false);
    closeChatbot();
  };

  const restoreMascot = () => {
    setIsMascotHidden(false);
    setRoamingMood("success");
    window.setTimeout(() => setRoamingMood("idle"), 1200);
  };

  if (pathname === "/login" || pathname === "/register") return null;

  return (
    <>
      {!isMascotHidden && !isOpen && !isMiniOpen && !showPetCloseMenu && (
        <div
          role={loading ? "status" : undefined}
          aria-live={loading ? "polite" : undefined}
          style={{
            transform: `translate3d(${Math.max(8, roamingPosition.x - 160)}px, ${Math.max(8, roamingPosition.y - 82)}px, 0)`,
          }}
          className="pointer-events-none fixed left-0 top-0 z-[84] w-64 rounded-2xl bg-[#17211f]/95 px-4 py-3 text-left text-white shadow-xl ring-1 ring-white/10 backdrop-blur"
        >
          <span className="flex items-center gap-2 text-[11px] font-bold text-[#9BE8D6]">
            <span className={`h-2 w-2 rounded-full bg-[#63CEBA] ${loading ? "animate-pulse" : ""}`} />
            {loading ? "분석 진행 중" : "입지봇"}
          </span>
          <span className="mt-1 block line-clamp-2 text-xs leading-5 text-white/90">
            {loading
              ? "요청한 조건과 상권 데이터를 차근차근 확인하고 있어요."
              : "궁금한 상권을 물어보려면 저를 눌러주세요."}
          </span>
          <span className="absolute -bottom-2 right-9 h-4 w-4 rotate-45 bg-[#17211f]" aria-hidden="true" />
        </div>
      )}

      {!isMascotHidden && isMiniOpen && !isOpen && (
        <section
          role="dialog"
          aria-label="입지봇 미니 대화"
          style={{
            transform: `translate3d(${Math.max(8, roamingPosition.x - 224)}px, ${Math.max(72, roamingPosition.y - 374)}px, 0)`,
          }}
          className="surface-shadow fixed left-0 top-0 z-[90] flex h-[350px] w-[min(320px,calc(100vw-16px))] flex-col overflow-hidden rounded-2xl border border-[#63CEBA]/35 bg-card"
        >
          <header className="flex h-12 shrink-0 items-center justify-between border-b bg-[#17211f] px-3 text-white">
            <div className="flex items-center gap-2">
              <span className="h-2.5 w-2.5 rounded-full bg-[#63CEBA]" aria-hidden="true" />
              <span className="text-sm font-black">입지봇</span>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  setIsMiniOpen(false);
                  showChatbot();
                }}
                title="크게 보기"
                aria-label="입지봇 채팅 크게 보기"
                className="flex h-8 items-center gap-1 rounded-lg px-2 text-xs font-bold text-white/80 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9BE8D6]"
              >
                <Maximize2 className="h-3.5 w-3.5" />
                크게 보기
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsMiniOpen(false);
                  setRoamingMood("idle");
                }}
                title="미니 대화 닫기"
                aria-label="입지봇 미니 대화 닫기"
                className="flex h-8 w-8 items-center justify-center rounded-lg text-white/70 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9BE8D6]"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </header>

          <div className="scrollbar-natural flex-1 space-y-3 overflow-y-auto bg-background p-3" aria-live="polite">
            {messages.slice(-8).map((message, index) => (
              <div key={`mini-${message.role}-${index}`} className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                <div className={`max-w-[88%] whitespace-pre-wrap rounded-xl px-3 py-2 text-xs leading-5 ${message.role === "user" ? "bg-primary text-primary-foreground" : "border bg-card text-card-foreground"}`}>
                  {publicText(message.type === "report" && message.reportData
                    ? message.reportData.compact_response.quick_judgement
                    : message.content)}
                </div>
              </div>
            ))}
            {loading && (
              <div role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                상권 데이터를 확인하고 있어요…
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              sendMessage(inputText);
            }}
            className="flex shrink-0 gap-2 border-t bg-card p-2.5"
          >
            <input
              ref={inputRef}
              type="text"
              aria-label="입지봇 미니 메시지"
              placeholder="궁금한 상권을 물어보세요"
              value={inputText}
              onChange={(event) => setInputText(event.target.value)}
              disabled={loading}
              className="h-10 min-w-0 flex-1 rounded-xl border bg-background px-3 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-60"
            />
            <button
              type="submit"
              aria-label="미니 대화 메시지 전송"
              disabled={!inputText.trim() || loading}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary text-primary-foreground disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
            </button>
          </form>
          <span className="absolute -bottom-2 right-9 h-4 w-4 rotate-45 border-b border-r border-[#63CEBA]/35 bg-card" aria-hidden="true" />
        </section>
      )}

      {!isMascotHidden && (
        <button
          type="button"
          onClick={activateMascot}
          onContextMenu={openPetCloseMenu}
          {...dragMascot}
          aria-label={isOpen ? "열린 입지봇 대화창으로 이동" : "입지봇과 대화하기"}
          style={{ transform: `translate3d(${roamingPosition.x}px, ${roamingPosition.y}px, 0)` }}
          className={`fixed left-0 top-0 z-[95] flex h-28 w-24 touch-none select-none items-center justify-center rounded-[1.5rem] bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${isDraggingMascot ? "cursor-grabbing" : "cursor-grab"}`}
        >
          <BotMascot mood={loading ? "thinking" : roamingMood} size="md" decorative />
        </button>
      )}

      {!isMascotHidden && showPetCloseMenu && (
        <div
          role="dialog"
          aria-label="입지봇 닫기"
          style={{
            transform: `translate3d(${Math.max(8, roamingPosition.x - 152)}px, ${Math.max(8, roamingPosition.y - 76)}px, 0)`,
          }}
          className="fixed left-0 top-0 z-[100] w-64 rounded-2xl border border-[#63CEBA]/30 bg-[#17211f]/95 p-4 text-white shadow-2xl backdrop-blur"
        >
          <p className="text-sm font-black">입지봇을 닫을까요?</p>
          <p className="mt-1 text-xs leading-5 text-white/70">현재 화면에서만 숨겨집니다. 새로고침하면 다시 만날 수 있어요.</p>
          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setShowPetCloseMenu(false);
                setRoamingMood("idle");
              }}
              className="rounded-lg px-3 py-2 text-xs font-bold text-white/75 hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#9BE8D6]"
            >
              취소
            </button>
            <button
              type="button"
              onClick={hideMascotForNow}
              className="rounded-lg bg-[#63CEBA] px-3 py-2 text-xs font-black text-[#103D39] hover:bg-[#9BE8D6] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
            >
              입지봇 숨기기
            </button>
          </div>
          <span className="absolute -bottom-2 right-9 h-4 w-4 rotate-45 border-b border-r border-[#63CEBA]/30 bg-[#17211f]" aria-hidden="true" />
        </div>
      )}

      {isMascotHidden && (
        <button
          type="button"
          onClick={restoreMascot}
          aria-label="입지봇 다시 부르기"
          className="fixed bottom-24 right-0 z-[95] flex min-h-11 items-center gap-2 rounded-l-full border border-r-0 border-[#63CEBA]/40 bg-[#17211f]/95 py-2 pl-3 pr-4 text-xs font-black text-white shadow-xl backdrop-blur transition-transform hover:-translate-x-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#63CEBA]"
        >
          <span className="h-2.5 w-2.5 rounded-full bg-[#63CEBA] shadow-[0_0_0_4px_rgba(99,206,186,0.18)]" aria-hidden="true" />
          입지봇 다시 부르기
        </button>
      )}

      {isOpen && <aside
      ref={panelRef}
      role="dialog"
      aria-modal={isMobile || undefined}
      aria-labelledby="chatbot-title"
      className="surface-shadow fixed inset-y-0 right-0 z-[90] flex w-full flex-col border-l bg-card sm:top-16 sm:w-[430px]"
    >
      <header className="flex h-16 shrink-0 items-center justify-between border-b px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0">
            <p className="text-[11px] font-bold text-primary">AI LOCATION COPILOT</p>
            <p id="chatbot-title" className="truncate text-base font-black">입지봇</p>
          </div>
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            title="대화 새로 시작"
            onClick={resetConversation}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RefreshCcw className="h-4 w-4" />
          </button>
          <button
            type="button"
            title="입지봇 닫기"
            onClick={closeChatbot}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </header>

      <div
        className="scrollbar-natural flex-1 space-y-5 overflow-y-auto bg-background px-4 py-5"
        aria-live="polite"
        aria-relevant="additions"
      >
        {messages.map((message, index) => (
          <div
            key={`${message.role}-${index}`}
            className={`flex flex-col ${message.role === "user" ? "items-end" : "items-start"}`}
          >
            {message.role === "assistant" && (
              <span className="mb-1.5 text-[11px] font-bold text-muted-foreground">입지봇</span>
            )}

            {message.type === "text" && (
              <div
                className={`max-w-[92%] whitespace-pre-wrap rounded-md px-3.5 py-3 text-sm leading-6 ${
                  message.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "border bg-card text-card-foreground"
                }`}
              >
                {publicText(message.content)}
                {message.isGuest && message.message && (
                  <div className="mt-2 border-t border-current/15 pt-2 text-xs font-medium text-[#b45309]">
                    {message.message}
                  </div>
                )}
                {message.options && message.options.length > 0 && (
                  <div className="mt-3 grid gap-1.5">
                    {message.options.map((option, optionIndex) => (
                      <button
                        type="button"
                        key={`${option.type}-${optionIndex}`}
                        onClick={() => sendMessage(option.value, option)}
                        className="rounded-md border bg-background px-3 py-2 text-left text-xs font-semibold text-primary transition-colors hover:border-primary/40 hover:bg-accent"
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {message.type === "report" && message.reportData && (
              <ReportBubble
                report={message.reportData}
                isGuest={message.isGuest}
                guestMessage={message.message}
                isSavingFavorite={isSavingFavorite}
                onNavigate={(target) => {
                  closeChatbot();
                  router.push(target);
                }}
                onSaveFavorite={handleSaveFavorite}
              />
            )}
          </div>
        ))}

        {loading && (
          <div role="status" className="flex items-center gap-3 border-l-2 border-primary px-3 py-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            대화 맥락을 살펴보고 답변을 정리하고 있어요.
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <footer className="shrink-0 border-t bg-card p-3">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            sendMessage(inputText);
          }}
          className="flex gap-2"
        >
          <input
            ref={inputRef}
            type="text"
            aria-label="입지봇 메시지"
            value={inputText}
            onChange={(event) => setInputText(event.target.value)}
            onFocus={() => !loading && setRoamingMood("listening")}
            onBlur={() => !loading && setRoamingMood("idle")}
            disabled={loading}
            className="h-11 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-60"
          />
          <button
            type="submit"
            title="전송"
            disabled={!inputText.trim() || loading}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-[#115e59] disabled:opacity-40"
          >
            <Send className="h-4 w-4" />
          </button>
        </form>
      </footer>
    </aside>}
    </>
  );
}

function ReportBubble({
  report,
  isGuest,
  guestMessage,
  isSavingFavorite,
  onNavigate,
  onSaveFavorite,
}: {
  report: ChatbotReport;
  isGuest?: boolean;
  guestMessage?: string;
  isSavingFavorite: boolean;
  onNavigate: (target: string) => void;
  onSaveFavorite: (areaCode?: string) => void;
}) {
  return (
    <div className="mt-1 w-full rounded-md border bg-card p-4 text-sm">
      <h3 className="mb-3 border-b pb-2 text-base font-black text-primary">간편 입지 리포트</h3>
      <div className="space-y-4">
        <div className="rounded-md border bg-background p-3">
          <p className="font-medium leading-relaxed text-foreground">{publicText(report.compact_response.condition_summary)}</p>
        </div>

        <div className="rounded-md border border-primary/20 bg-accent p-3">
          <span className="mb-1 block text-xs font-bold text-primary">AI 해석</span>
          <p className="font-bold leading-snug text-foreground">{publicText(report.compact_response.quick_judgement)}</p>
        </div>

        {report.compact_response.evidence_basis && report.compact_response.evidence_basis.length > 0 && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3">
            <span className="mb-2 block text-xs font-bold text-blue-800">근거 요약</span>
            <ul className="list-inside list-disc space-y-1 text-[12px] leading-5 text-blue-950">
              {report.compact_response.evidence_basis.map((item, index) => <li key={`evidence-${index}`}>{publicText(item)}</li>)}
            </ul>
          </div>
        )}

        <div className="rounded-md border border-rose-200 bg-rose-50 p-3">
          <span className="mb-2 block text-xs font-bold text-rose-700">주의할 점</span>
          <ul className="list-inside list-disc space-y-1 text-[13px] text-rose-800">
            {report.compact_response.main_risks?.map((risk) => <li key={risk}>{publicText(risk)}</li>)}
          </ul>
        </div>

        {report.compact_response.alternative_areas?.length > 0 && (
          <div className="border-t border-dashed pt-4">
            <h4 className="mb-2 text-[13px] font-bold text-foreground">비교 후보</h4>
            <div className="flex flex-col gap-2">
              {report.compact_response.alternative_areas.map((alt) => (
                <button
                  type="button"
                  key={`${alt.area_code || alt.area_name}`}
                  onClick={() => onNavigate(alt.area_code ? `/trade?area=${alt.area_code}` : "/trade")}
                  className="w-full rounded-md border bg-background p-2.5 text-left text-[12px] transition-colors hover:border-primary/40 hover:bg-accent"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-primary">{alt.area_name}</span>
                    <span className="shrink-0 text-muted-foreground">{publicText(alt.interpretation_level || "대안 후보")}</span>
                  </div>
                  {alt.reason && <p className="mt-1 text-muted-foreground">{publicText(alt.reason)}</p>}
                </button>
              ))}
            </div>
          </div>
        )}

        {report.compact_response.recommended_strategy && report.compact_response.recommended_strategy.length > 0 && (
          <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
            <span className="mb-2 block text-xs font-bold text-emerald-800">다음 행동</span>
            <ul className="list-inside list-disc space-y-1 text-[12px] leading-5 text-emerald-900">
              {report.compact_response.recommended_strategy.map((item, index) => <li key={`strategy-${index}`}>{publicText(item)}</li>)}
            </ul>
          </div>
        )}

        <p className="text-center text-[13px] font-medium text-primary">{publicText(report.compact_response.cta)}</p>

        <div className="flex flex-col gap-2 border-t border-dashed pt-4">
          {report.actions?.map((action) => (
            <button
              type="button"
              key={action.target}
              onClick={() => onNavigate(action.target)}
              className="flex w-full items-center justify-center gap-2 rounded-md border border-primary/30 bg-card py-3 font-bold text-primary transition-colors hover:bg-accent"
            >
              {publicText(action.label)}
              <ExternalLink className="h-4 w-4" />
            </button>
          ))}
          <button
            type="button"
            onClick={() => onSaveFavorite(report.area_code)}
            disabled={isSavingFavorite}
            className="flex w-full items-center justify-center gap-2 rounded-md border border-amber-200 bg-amber-50 py-2 text-xs font-medium text-amber-800 transition-colors hover:bg-amber-100 disabled:opacity-50"
          >
            <BookmarkPlus className="h-4 w-4" />
            즐겨찾기에 추가
          </button>
        </div>

        {isGuest && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-center text-[10px] font-bold text-amber-800">
            {guestMessage || "게스트 모드에서는 상담 기록이 임시 저장됩니다."}
          </div>
        )}
      </div>
    </div>
  );
}
