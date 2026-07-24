"use client";

import {
  isNavigationActive,
  mainNavigationItems,
  navigationHref,
} from "@/components/navigation";
import { useSelectedArea } from "@/components/selected-area-context";
import {
  AUTH_CHANGED_EVENT,
  apiUrl,
  fetchAuth,
  fetchCurrentUser,
  logProductEvent,
  notifyAuthChanged,
  type AuthUser,
} from "@/lib/api";
import { displayGradeOrPending } from "@/lib/score-grade";
import clsx from "clsx";
import { ChevronDown, LogIn, Search, Settings, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

interface SearchArea {
  area_code: string;
  area_name: string;
  district_code?: string | null;
  grade?: string | null;
  display_grade?: string | null;
  latitude?: number | null;
  longitude?: number | null;
}

export function Topbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { selectedArea, setSelectedArea } = useSelectedArea();
  const [keyword, setKeyword] = useState("");
  const [results, setResults] = useState<SearchArea[]>([]);
  const [searching, setSearching] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [guest, setGuest] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const searchRootRef = useRef<HTMLDivElement>(null);
  const accountRootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const hydrateAuth = async () => {
      setGuest(localStorage.getItem("guest_mode") === "true");
      if (!localStorage.getItem("token")) {
        setUser(null);
        return;
      }
      const current = await fetchCurrentUser().catch(() => null);
      setUser(current);
      if (current) setGuest(false);
    };
    void hydrateAuth();
    window.addEventListener(AUTH_CHANGED_EVENT, hydrateAuth);
    return () => window.removeEventListener(AUTH_CHANGED_EVENT, hydrateAuth);
  }, []);

  useEffect(() => {
    const closeMenus = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!searchRootRef.current?.contains(target)) setShowResults(false);
      if (!accountRootRef.current?.contains(target)) setAccountOpen(false);
    };
    document.addEventListener("pointerdown", closeMenus);
    return () => document.removeEventListener("pointerdown", closeMenus);
  }, []);

  const searchAreas = async (event: React.FormEvent) => {
    event.preventDefault();
    const query = keyword.trim();
    if (!query || searching) return;
    setSearching(true);
    try {
      void logProductEvent("search_submitted").catch(() => undefined);
      const response = await fetchAuth(apiUrl(`/search?keyword=${encodeURIComponent(query)}`));
      if (!response.ok) throw new Error("검색 요청을 처리하지 못했습니다.");
      const payload = await response.json();
      setResults(Array.isArray(payload) ? payload : []);
      setShowResults(true);
    } catch {
      setResults([]);
      setShowResults(true);
    } finally {
      setSearching(false);
    }
  };

  const selectArea = (area: SearchArea) => {
    setSelectedArea({
      areaCode: area.area_code,
      areaName: area.area_name,
      latitude: typeof area.latitude === "number" ? area.latitude : null,
      longitude: typeof area.longitude === "number" ? area.longitude : null,
    });
    void logProductEvent("area_selected", { area_code: area.area_code }).catch(() => undefined);
    setKeyword("");
    setShowResults(false);
    router.push(`/trade?areaCode=${encodeURIComponent(area.area_code)}&area=${encodeURIComponent(area.area_code)}`);
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("guest_mode");
    setUser(null);
    setGuest(false);
    notifyAuthChanged();
    router.push("/");
  };

  return (
    <header className="relative z-[60] flex h-16 shrink-0 items-center gap-3 border-b bg-card px-3 lg:px-5">
      <Link href="/" className="flex shrink-0 items-center gap-2" aria-label="LocalFit Lab 홈">
        <span className="grid h-9 w-9 place-items-center rounded-xl bg-primary text-xs font-black text-primary-foreground">LF</span>
        <span className="hidden text-sm font-black tracking-tight xl:block">LOCALFIT LAB</span>
      </Link>

      <nav className="hidden h-full shrink-0 items-center lg:flex" aria-label="주요 메뉴">
        {mainNavigationItems.map((item) => {
          const active = isNavigationActive(pathname, item.href);
          return (
            <Link
              key={item.href}
              href={navigationHref(item.href, selectedArea?.areaCode || null)}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "relative flex h-full items-center px-3 text-sm font-bold transition-colors",
                active ? "text-primary" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {item.label}
              {active && <span className="absolute inset-x-3 bottom-0 h-0.5 rounded-full bg-primary" />}
            </Link>
          );
        })}
      </nav>

      <div ref={searchRootRef} className="relative ml-auto min-w-0 flex-1 sm:max-w-[460px]">
        <form onSubmit={searchAreas}>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            type="search"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onFocus={() => results.length > 0 && setShowResults(true)}
            aria-label="서울 상권 검색"
            placeholder="상권명 또는 행정동 검색"
            className="h-10 w-full rounded-xl border bg-background pl-9 pr-10 text-sm outline-none placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/15"
          />
          <button
            type="submit"
            aria-label="검색"
            disabled={searching || !keyword.trim()}
            className="absolute right-1 top-1 grid h-8 w-8 place-items-center rounded-lg text-primary hover:bg-accent disabled:opacity-40"
          >
            <Search className={clsx("h-4 w-4", searching && "animate-pulse")} />
          </button>
        </form>

        {showResults && (
          <div className="surface-shadow absolute right-0 top-12 z-[90] max-h-[420px] w-full min-w-[300px] overflow-y-auto rounded-xl border bg-card p-2 sm:min-w-[430px]">
            {results.length ? (
              <ul className="space-y-1">
                {results.slice(0, 20).map((area) => (
                  <li key={area.area_code}>
                    <button
                      type="button"
                      onClick={() => selectArea(area)}
                      className="flex w-full items-center justify-between gap-4 rounded-lg px-3 py-2.5 text-left hover:bg-accent"
                    >
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-bold">{area.area_name}</span>
                        <span className="mt-0.5 block text-xs text-muted-foreground">상권코드 {area.area_code}</span>
                      </span>
                      <span
                        className="shrink-0 rounded-full bg-primary/10 px-2 py-1 text-xs font-black text-primary"
                        aria-label={`수요·접근 등급 ${displayGradeOrPending(area.display_grade, area.grade)}`}
                      >
                        {displayGradeOrPending(area.display_grade, area.grade)}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">검색 결과가 없습니다.</p>
            )}
          </div>
        )}
      </div>

      <div ref={accountRootRef} className="relative shrink-0">
        {user ? (
          <>
            <button
              type="button"
              onClick={() => setAccountOpen((open) => !open)}
              className="flex h-10 items-center gap-2 rounded-xl px-2 hover:bg-muted"
              aria-expanded={accountOpen}
            >
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-xs font-black text-primary">
                {user.nickname.slice(0, 1).toUpperCase()}
              </span>
              <span className="hidden max-w-24 truncate text-sm font-semibold 2xl:block">{user.nickname}</span>
              <ChevronDown className="hidden h-3.5 w-3.5 text-muted-foreground 2xl:block" />
            </button>
            {accountOpen && (
              <div className="surface-shadow absolute right-0 top-12 w-48 rounded-xl border bg-card p-2 text-sm">
                <Link href="/settings" className="flex items-center gap-2 rounded-lg px-3 py-2 font-semibold hover:bg-accent">
                  <Settings className="h-4 w-4" /> 설정
                </Link>
                {user.is_admin && (
                  <Link href="/admin" className="flex items-center gap-2 rounded-lg px-3 py-2 font-semibold hover:bg-accent">
                    <ShieldCheck className="h-4 w-4" /> 관리자
                  </Link>
                )}
                <button type="button" onClick={logout} className="flex w-full items-center gap-2 rounded-lg px-3 py-2 font-semibold hover:bg-accent">
                  <LogIn className="h-4 w-4 rotate-180" /> 로그아웃
                </button>
              </div>
            )}
          </>
        ) : (
          <Link
            href="/login"
            title={guest ? "게스트 모드 · 로그인" : "로그인"}
            className="flex h-10 items-center gap-2 rounded-xl px-2 text-sm font-semibold text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            {guest ? <UserRound className="h-5 w-5" /> : <LogIn className="h-5 w-5" />}
            <span className="hidden xl:inline">{guest ? "게스트" : "로그인"}</span>
          </Link>
        )}
      </div>
    </header>
  );
}
