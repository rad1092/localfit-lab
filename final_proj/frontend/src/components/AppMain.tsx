"use client";

import { usePathname } from "next/navigation";

export function AppMain({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isWorkspace = pathname === "/" || pathname === "/trade";

  return (
    <main
      className={
        isWorkspace
          ? "min-h-0 flex-1 overflow-hidden bg-background pb-[calc(4rem+env(safe-area-inset-bottom))] lg:pb-0"
          : "min-h-0 flex-1 overflow-auto bg-background p-4 pb-[calc(5rem+env(safe-area-inset-bottom))] lg:p-6 lg:pb-8"
      }
    >
      {children}
    </main>
  );
}
