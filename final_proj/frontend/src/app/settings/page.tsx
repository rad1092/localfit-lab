"use client";

import {
  apiUrl,
  fetchAuth,
  fetchCurrentUser,
  notifyAuthChanged,
  type AuthUser,
} from "@/lib/api";
import { ExternalLink, LoaderCircle, LogOut, Save, ShieldCheck, UserRound } from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { useRouter } from "next/navigation";
import { FormEvent, useEffect, useState, useSyncExternalStore } from "react";

export default function SettingsPage() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(() => () => undefined, () => true, () => false);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [nickname, setNickname] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!localStorage.getItem("token") || localStorage.getItem("guest_mode") === "true") {
      router.replace("/login");
      return;
    }
    fetchCurrentUser()
      .then((current) => {
        if (!current) throw new Error("사용자 정보를 불러오지 못했습니다.");
        setUser(current);
        setNickname(current.nickname);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : "사용자 정보를 불러오지 못했습니다."))
      .finally(() => setLoading(false));
  }, [router]);

  const saveProfile = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const payload: Record<string, string> = { nickname: nickname.trim() };
      if (newPassword) {
        payload.current_password = currentPassword;
        payload.new_password = newPassword;
      }
      const response = await fetchAuth(apiUrl("/auth/me"), {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || "설정을 저장하지 못했습니다.");
      setUser(result);
      setNickname(result.nickname);
      setCurrentPassword("");
      setNewPassword("");
      setMessage("계정 설정을 저장했습니다.");
      notifyAuthChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "설정을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const logout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("guest_mode");
    notifyAuthChanged();
    router.push("/login");
  };

  if (loading) {
    return <div className="grid min-h-[50vh] place-items-center" role="status"><LoaderCircle className="h-6 w-6 animate-spin text-primary" /></div>;
  }
  if (!user) {
    return <section className="mx-auto mt-16 max-w-lg rounded-2xl border bg-card p-8 text-center" role="alert"><h1 className="text-xl font-black">사용자 정보를 확인할 수 없습니다.</h1><p className="mt-2 text-sm text-muted-foreground">다시 로그인해 주세요.</p></section>;
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
      <header>
        <p className="text-xs font-black text-primary">USER SETTINGS</p>
        <h1 className="mt-1 text-3xl font-black">설정</h1>
        <p className="mt-2 text-sm text-muted-foreground">계정 정보와 화면 테마를 한곳에서 관리합니다.</p>
      </header>

      <form onSubmit={saveProfile} className="rounded-2xl border bg-card p-6 surface-shadow">
        <div className="mb-5 flex items-center gap-3 border-b pb-4">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-primary"><UserRound className="h-5 w-5" /></span>
          <div><h2 className="font-black">계정 정보</h2><p className="text-xs text-muted-foreground">{user.is_admin ? "관리자" : "일반 사용자"} 계정</p></div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-bold">이메일<input value={user.email} disabled className="mt-2 h-11 w-full rounded-xl border bg-muted px-3 font-normal text-muted-foreground" /></label>
          <label className="text-sm font-bold">닉네임<input value={nickname} onChange={(event) => setNickname(event.target.value)} minLength={1} maxLength={50} required className="mt-2 h-11 w-full rounded-xl border bg-background px-3 font-normal outline-none focus:border-primary" /></label>
          <label className="text-sm font-bold">현재 비밀번호<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" className="mt-2 h-11 w-full rounded-xl border bg-background px-3 font-normal outline-none focus:border-primary" /></label>
          <label className="text-sm font-bold">새 비밀번호<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={8} autoComplete="new-password" placeholder="변경할 때만 입력 · 8자 이상" className="mt-2 h-11 w-full rounded-xl border bg-background px-3 font-normal outline-none focus:border-primary" /></label>
        </div>
        {message && <p className="mt-4 text-sm font-bold text-primary" role="status">{message}</p>}
        {error && <p className="mt-4 text-sm font-bold text-destructive" role="alert">{error}</p>}
        <div className="mt-5 flex justify-end"><button type="submit" disabled={saving || !nickname.trim() || Boolean(newPassword && (!currentPassword || newPassword.length < 8))} className="inline-flex h-10 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-bold text-primary-foreground disabled:opacity-40">{saving ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />} 설정 저장</button></div>
      </form>

      <section className="rounded-2xl border bg-card p-6 surface-shadow">
        <h2 className="font-black">화면 테마</h2>
        <p className="mt-1 text-sm text-muted-foreground">라이트, 다크 또는 기기 설정을 사용할 수 있습니다.</p>
        <div className="mt-4 flex flex-wrap gap-3">
          {([[
            "light", "라이트",
          ], ["dark", "다크"], ["system", "시스템"]] as const).map(([value, label]) => (
            <button key={value} type="button" onClick={() => setTheme(value)} className={`rounded-xl px-4 py-2 text-sm font-bold ${mounted && theme === value ? "bg-primary text-primary-foreground" : "bg-muted hover:bg-accent"}`}>{label}</button>
          ))}
        </div>
      </section>

      {user.is_admin && (
        <section className="rounded-2xl border bg-card p-6 surface-shadow">
          <div className="flex items-start gap-3"><span className="grid h-10 w-10 place-items-center rounded-xl bg-accent text-primary"><ShieldCheck className="h-5 w-5" /></span><div><h2 className="font-black">관리자 도구</h2><p className="mt-1 text-sm leading-6 text-muted-foreground">외부 API 연결 상태와 데이터 파이프라인은 관리자 페이지에서 확인합니다.</p></div></div>
          <Link href="/admin?tab=integrations" className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl border px-4 text-sm font-bold hover:bg-muted">외부 연결 관리 <ExternalLink className="h-4 w-4" /></Link>
        </section>
      )}

      <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border bg-card p-6 surface-shadow">
        <div><h2 className="font-black">로그아웃</h2><p className="mt-1 text-sm text-muted-foreground">현재 기기의 로그인 정보를 삭제합니다.</p></div>
        <button type="button" onClick={logout} className="inline-flex h-10 items-center gap-2 rounded-xl border px-4 text-sm font-bold hover:bg-muted"><LogOut className="h-4 w-4" /> 로그아웃</button>
      </section>
    </div>
  );
}
