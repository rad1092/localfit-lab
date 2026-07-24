"use client";

import { useState } from "react";
import Link from "next/link";
import { apiUrl, notifyAuthChanged } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const formData = new URLSearchParams();
      formData.append("username", email);
      formData.append("password", password);

      const res = await fetch(apiUrl("/auth/login"), {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formData,
      });

      if (res.ok) {
        const data = await res.json();
        localStorage.setItem("token", data.access_token);
        localStorage.removeItem("guest_mode");
        notifyAuthChanged();
        // Force reload to update header state or redirect to home
        window.location.href = "/";
      } else {
        const errorData = await res.json();
        setError(errorData.detail || "로그인에 실패했습니다.");
      }
    } catch {
      setError("서버 연결에 실패했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleGuestLogin = () => {
    localStorage.removeItem("token");
    localStorage.setItem("guest_mode", "true");
    notifyAuthChanged();
    window.location.href = "/";
  };

  return (
    <div className="max-w-md mx-auto mt-20 p-6 bg-card border rounded-xl shadow-sm">
      <h1 className="text-2xl font-bold text-center mb-6">로그인</h1>
      
      {error && (
        <div className="bg-destructive/15 text-destructive text-sm p-3 rounded-md mb-4">
          {error}
        </div>
      )}

      <form onSubmit={handleLogin} className="flex flex-col gap-4">
        <div>
          <label className="block text-sm font-medium mb-1">이메일</label>
          <input 
            type="email" 
            required 
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary bg-background"
          />
        </div>
        
        <div>
          <label className="block text-sm font-medium mb-1">비밀번호</label>
          <input 
            type="password" 
            required 
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary bg-background"
          />
        </div>

        <button 
          type="submit" 
          disabled={isLoading}
          className="w-full py-2 bg-primary text-primary-foreground rounded-md font-medium mt-2 disabled:opacity-50"
        >
          {isLoading ? "로그인 중..." : "로그인"}
        </button>

        <div className="relative my-2">
          <div className="absolute inset-0 flex items-center">
            <span className="w-full border-t" />
          </div>
          <div className="relative flex justify-center text-xs uppercase">
            <span className="bg-card px-2 text-muted-foreground">또는</span>
          </div>
        </div>

        <button 
          type="button" 
          onClick={handleGuestLogin}
          className="w-full py-2 border rounded-md font-medium hover:bg-muted transition-colors"
        >
          게스트로 시작하기
        </button>
      </form>

      <div className="mt-6 text-center text-sm text-muted-foreground">
        계정이 없으신가요? <Link href="/register" className="text-primary hover:underline">회원가입</Link>
      </div>
    </div>
  );
}
