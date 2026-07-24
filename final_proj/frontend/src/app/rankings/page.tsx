"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import { displayGradeOrPending } from "@/lib/score-grade";

interface Ranking {
  rank: number;
  area_code: string;
  area_name: string;
  score: number;
  grade?: string | null;
  display_grade?: string | null;
  trend: string;
  score_type: "demand_accessibility_context";
  score_label: string;
  official_rank_eligible: false;
}

export default function Rankings() {
  const [rankings, setRankings] = useState<Ranking[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(apiUrl("/rankings"))
      .then(res => res.json())
      .then(data => setRankings(data))
      .catch(err => console.error(err))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-background text-foreground p-8">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">상권 수요·접근성 맥락 순위</h1>
          <p className="text-muted-foreground">업종 간 추천 순위가 아닌 상권 공통 맥락 기준 Top 100</p>
        </div>
        <nav className="flex gap-4">
          <Link href="/" className="text-muted-foreground hover:text-primary">Dashboard</Link>
          <Link href="/rankings" className="font-semibold text-primary">Rankings</Link>
          <Link href="/opportunity" className="text-muted-foreground hover:text-primary">Opportunity</Link>
        </nav>
      </header>

      <div className="flex gap-6">
        <aside className="w-64 flex flex-col gap-4">
          <div className="rounded-xl border bg-card p-4">
            <h3 className="font-semibold mb-2">필터</h3>
            <div className="space-y-2 text-sm text-muted-foreground">
              <label className="flex items-center gap-2"><input type="checkbox" /> 서울특별시</label>
              <label className="flex items-center gap-2"><input type="checkbox" /> 경기도</label>
            </div>
          </div>
          <div className="rounded-xl border bg-card p-4">
            <h3 className="font-semibold mb-2">검색</h3>
            <input type="text" placeholder="상권 이름 검색..." className="w-full p-2 rounded border bg-background text-sm" />
          </div>
        </aside>

        <main className="flex-1 rounded-xl border bg-card overflow-hidden min-h-[500px]">
          {loading ? (
            <div className="flex items-center justify-center h-full text-muted-foreground animate-pulse">
              랭킹 데이터를 계산하고 불러오는 중입니다...
            </div>
          ) : (
            <table className="w-full text-left text-sm">
              <thead className="bg-muted/50 border-b">
                <tr>
                  <th className="p-4 font-semibold">Rank</th>
                  <th className="p-4 font-semibold">Area Name</th>
                  <th className="p-4 font-semibold">수요·접근성 등급</th>
                  <th className="p-4 font-semibold">Trend</th>
                </tr>
              </thead>
              <tbody>
                {rankings.map((r) => (
                  <tr key={r.rank} className="border-b hover:bg-muted/20 transition-colors">
                    <td className="p-4 font-bold">{r.rank}</td>
                    <td className="p-4"><Link href={`/trade?area=${r.area_code}`} className="hover:underline">{r.area_name}</Link></td>
                    <td className="p-4"><span className="rounded-full bg-primary/10 px-3 py-1 font-black text-primary">{displayGradeOrPending(r.display_grade, r.grade)}</span></td>
                    <td className="p-4">
                      <span className={r.trend.startsWith("+") ? "text-emerald-500 font-bold" : r.trend === "-" ? "text-muted-foreground" : "text-rose-500 font-bold"}>
                        {r.trend.startsWith("+") ? `▲ ${r.trend.slice(1)}` : r.trend.startsWith("-") && r.trend !== "-" ? `▼ ${r.trend.slice(1)}` : "-"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </main>
      </div>
    </div>
  );
}
