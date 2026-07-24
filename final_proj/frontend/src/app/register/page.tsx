"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiUrl } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setError("");

    try {
      const res = await fetch(apiUrl("/auth/register"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, nickname }),
      });

      if (res.ok) {
        alert("회원가입이 완료되었습니다. 로그인해주세요.");
        router.push("/login");
      } else {
        const errorData = await res.json();
        setError(errorData.detail || "회원가입에 실패했습니다.");
      }
    } catch {
      setError("서버 연결에 실패했습니다.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="max-w-md mx-auto mt-20 p-6 bg-card border rounded-xl shadow-sm">
      <h1 className="text-2xl font-bold text-center mb-6">회원가입</h1>
      
      {error && (
        <div className="bg-destructive/15 text-destructive text-sm p-3 rounded-md mb-4">
          {error}
        </div>
      )}

      <form onSubmit={handleRegister} className="flex flex-col gap-4">
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
          <label className="block text-sm font-medium mb-1">닉네임</label>
          <input 
            type="text" 
            required 
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            className="w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-primary bg-background"
            placeholder="홍길동"
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
          {isLoading ? "가입 처리 중..." : "회원가입"}
        </button>
      </form>

      <div className="mt-6 text-center text-sm text-muted-foreground">
        이미 계정이 있으신가요? <Link href="/login" className="text-primary hover:underline">로그인</Link>
      </div>
    </div>
  );
}
