export function DemoBanner() {
  if (process.env.NEXT_PUBLIC_DEMO_MODE !== "true") return null;

  return (
    <aside className="flex min-h-9 shrink-0 flex-wrap items-center justify-center gap-x-2 gap-y-1 border-b border-[#0f766e]/25 bg-[#e6fffa] px-3 py-1.5 text-center text-[11px] font-semibold text-[#115e59] sm:text-xs">
      <strong className="rounded-full bg-[#0f766e] px-2 py-0.5 text-white">실행 데모</strong>
      <span>합성 샘플 데이터로 동작하며 실제 창업·투자 판단에 사용할 수 없습니다.</span>
      <a
        href="https://whago.net"
        target="_blank"
        rel="noreferrer"
        className="font-black underline decoration-[#0f766e]/40 underline-offset-2 hover:decoration-[#0f766e]"
      >
        운영 서비스 보기
      </a>
    </aside>
  );
}
