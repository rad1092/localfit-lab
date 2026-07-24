"use client";

import { KakaoMap } from "@/components/KakaoMap";
import { useSelectedArea } from "@/components/selected-area-context";
import { apiUrl, fetchAuth, logProductEvent } from "@/lib/api";
import { displayGradeOrPending } from "@/lib/score-grade";
import type { AreaData, RankingData } from "@/types/models";
import {
  ArrowRight,
  ChevronRight,
  Database,
  LoaderCircle,
  MapPin,
  Search,
  Store,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";

interface OverviewStats {
  latest_quarter: string;
  area_count: number;
  store_point_count: number;
}

interface SearchArea extends RankingData {
  latitude?: number | null;
  longitude?: number | null;
}

function quarterLabel(value?: string | null) {
  const normalized = String(value || "");
  return /^\d{5}$/.test(normalized)
    ? `${normalized.slice(0, 4)}년 ${normalized.slice(4)}분기`
    : normalized || "기준 분기 없음";
}

export default function Home() {
  const router = useRouter();
  const { selectedArea, setSelectedArea } = useSelectedArea();
  const [rankings, setRankings] = useState<RankingData[]>([]);
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [keyword, setKeyword] = useState("");
  const [searchResults, setSearchResults] = useState<SearchArea[]>([]);
  const [searching, setSearching] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetch(apiUrl("/areas/rankings"), { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error("상권 순위를 불러오지 못했습니다.");
        return response.json();
      }),
      fetch(apiUrl("/areas/stats"), { signal: controller.signal }).then((response) =>
        response.ok ? response.json() : null,
      ),
    ])
      .then(([rankingPayload, statsPayload]) => {
        setRankings(Array.isArray(rankingPayload) ? rankingPayload : []);
        setStats(statsPayload);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "상권 정보를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, []);

  const runSearch = async (event: FormEvent) => {
    event.preventDefault();
    const query = keyword.trim();
    if (!query || searching) return;
    setSearching(true);
    setError("");
    void logProductEvent("search_submitted").catch(() => undefined);
    try {
      const response = await fetchAuth(apiUrl(`/search?keyword=${encodeURIComponent(query)}`));
      if (!response.ok) throw new Error("검색 요청을 처리하지 못했습니다.");
      const payload = await response.json();
      setSearchResults(Array.isArray(payload) ? payload : []);
    } catch (reason) {
      setSearchResults([]);
      setError(reason instanceof Error ? reason.message : "검색 요청을 처리하지 못했습니다.");
    } finally {
      setSearching(false);
    }
  };

  const chooseArea = async (area: SearchArea | RankingData) => {
    const initialLatitude = "latitude" in area && typeof area.latitude === "number" ? area.latitude : null;
    const initialLongitude = "longitude" in area && typeof area.longitude === "number" ? area.longitude : null;
    let detail: Pick<AreaData, "latitude" | "longitude"> = {
      latitude: initialLatitude ?? undefined,
      longitude: initialLongitude ?? undefined,
    };
    if (initialLatitude == null || initialLongitude == null) {
      const response = await fetchAuth(apiUrl(`/areas/${encodeURIComponent(area.area_code)}`), {
        cache: "no-store",
      }).catch(() => null);
      if (response?.ok) detail = await response.json();
    }
    setSelectedArea({
      areaCode: area.area_code,
      areaName: area.area_name,
      latitude: typeof detail.latitude === "number" ? detail.latitude : null,
      longitude: typeof detail.longitude === "number" ? detail.longitude : null,
    });
    void logProductEvent("area_selected", { area_code: area.area_code }).catch(() => undefined);
  };

  const selectedRanking = useMemo(
    () => rankings.find((item) => item.area_code === selectedArea?.areaCode) || null,
    [rankings, selectedArea?.areaCode],
  );

  const openAnalysis = (areaCode: string) => {
    const encoded = encodeURIComponent(areaCode);
    router.push(`/trade?areaCode=${encoded}&area=${encoded}`);
  };

  return (
    <div className="h-full overflow-y-auto lg:overflow-hidden">
      <div className="grid min-h-full lg:h-full lg:grid-cols-[390px_minmax(0,1fr)]">
        <aside className="order-2 border-r bg-card lg:order-1 lg:min-h-0 lg:overflow-y-auto">
          <section className="border-b p-5">
            <p className="text-xs font-black text-primary">SEOUL COMMERCIAL EXPLORER</p>
            <h1 className="mt-2 text-3xl font-black leading-tight">서울 상권을<br />한눈에 탐색하세요</h1>
            <p className="mt-3 text-sm leading-6 text-muted-foreground">
              상권을 찾고 지도에서 위치를 확인한 뒤 상세 분석으로 이어집니다.
            </p>

            <form onSubmit={runSearch} className="relative mt-5">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <input
                type="search"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
                placeholder="상권명 또는 행정동 검색"
                aria-label="서울 상권 검색"
                className="h-12 w-full rounded-xl border bg-background pl-10 pr-12 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
              />
              <button
                type="submit"
                disabled={!keyword.trim() || searching}
                aria-label="검색"
                className="absolute right-1.5 top-1.5 grid h-9 w-9 place-items-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40"
              >
                {searching ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ArrowRight className="h-4 w-4" />}
              </button>
            </form>

            {searchResults.length > 0 && (
              <div className="mt-3 max-h-52 overflow-y-auto rounded-xl border p-1.5">
                {searchResults.slice(0, 12).map((area) => (
                  <button
                    key={area.area_code}
                    type="button"
                    onClick={() => void chooseArea(area)}
                    className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-accent"
                  >
                    <span className="min-w-0 truncate text-sm font-bold">{area.area_name}</span>
                    <span className="shrink-0 text-xs font-black text-primary">
                      {displayGradeOrPending(area.display_grade, area.grade)}
                    </span>
                  </button>
                ))}
              </div>
            )}
            {keyword.trim() && !searching && searchResults.length === 0 && error && (
              <p className="mt-3 text-xs font-semibold text-destructive" role="alert">{error}</p>
            )}
          </section>

          <section className="border-b p-5">
            <div className="grid grid-cols-3 divide-x rounded-xl border bg-background py-3 text-center">
              <div>
                <MapPin className="mx-auto h-4 w-4 text-primary" />
                <strong className="mt-1 block text-sm">{stats?.area_count?.toLocaleString() || "-"}</strong>
                <span className="text-[10px] text-muted-foreground">공식 상권</span>
              </div>
              <div>
                <Store className="mx-auto h-4 w-4 text-[#2563eb]" />
                <strong className="mt-1 block text-sm">{stats?.store_point_count?.toLocaleString() || "-"}</strong>
                <span className="text-[10px] text-muted-foreground">점포 위치</span>
              </div>
              <div>
                <Database className="mx-auto h-4 w-4 text-[#b45309]" />
                <strong className="mt-1 block text-sm">{quarterLabel(stats?.latest_quarter).replace("년 ", ".Q").replace("분기", "")}</strong>
                <span className="text-[10px] text-muted-foreground">기준 분기</span>
              </div>
            </div>
          </section>

          <section className="p-3">
            <div className="flex items-center justify-between px-2 pb-3 pt-1">
              <div>
                <p className="text-xs font-bold text-muted-foreground">수요·접근성 맥락 등급</p>
                <h2 className="mt-0.5 text-lg font-black">상위 상권</h2>
              </div>
              <Link href="/rankings" className="text-xs font-bold text-primary hover:underline">전체 보기</Link>
            </div>

            <div className="space-y-1">
              {loading
                ? Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-14 animate-pulse rounded-lg bg-muted" />)
                : rankings.slice(0, 8).map((area, index) => (
                    <button
                      key={area.area_code}
                      type="button"
                      onClick={() => void chooseArea(area)}
                      className={`flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left transition-colors ${selectedArea?.areaCode === area.area_code ? "border-primary bg-accent" : "border-transparent hover:border-border hover:bg-accent"}`}
                    >
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-muted text-xs font-black text-muted-foreground">{index + 1}</span>
                      <span className="min-w-0 flex-1 truncate text-sm font-bold">{area.area_name}</span>
                      <span className="rounded-full bg-primary/10 px-2 py-1 text-xs font-black text-primary">{displayGradeOrPending(area.display_grade, area.grade)}</span>
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
                    </button>
                  ))}
            </div>
          </section>
        </aside>

        <section className="relative order-1 h-[52vh] min-h-[360px] lg:order-2 lg:h-full">
          <KakaoMap
            lat={selectedArea?.latitude ?? 37.5665}
            lng={selectedArea?.longitude ?? 126.978}
            areaName={selectedArea?.areaName || "서울 전역"}
            areaCode={selectedArea?.areaCode}
            level={selectedArea ? 4 : 8}
            resolveByName={false}
          />

          <div className="surface-shadow absolute bottom-4 left-4 z-30 max-w-[calc(100%-32px)] rounded-2xl border bg-card/95 p-4 backdrop-blur">
            {selectedArea ? (
              <div className="flex items-center gap-3">
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent text-primary"><MapPin className="h-5 w-5" /></span>
                <span className="min-w-0 flex-1">
                  <strong className="block truncate text-sm">{selectedArea.areaName}</strong>
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {selectedRanking ? `공개 등급 ${displayGradeOrPending(selectedRanking.display_grade, selectedRanking.grade)}` : "선택한 상권"}
                  </span>
                </span>
                <button type="button" onClick={() => openAnalysis(selectedArea.areaCode)} className="h-10 shrink-0 rounded-xl bg-primary px-4 text-sm font-bold text-primary-foreground">
                  분석 보기
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <MapPin className="h-5 w-5 text-primary" />
                <span><strong className="block text-sm">서울 전역</strong><span className="text-xs text-muted-foreground">왼쪽 목록이나 검색에서 상권을 선택하세요.</span></span>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
