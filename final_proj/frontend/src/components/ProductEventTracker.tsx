"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { logProductEvent } from "@/lib/api";

const LAST_PAGE_VIEW_KEY = "localfit:last-page-view";
const DUPLICATE_WINDOW_MS = 1_500;

export function ProductEventTracker() {
  const pathname = usePathname();

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const now = Date.now();
      try {
        const previous = JSON.parse(
          sessionStorage.getItem(LAST_PAGE_VIEW_KEY) || "null",
        ) as { pathname?: string; recordedAt?: number } | null;
        if (
          previous?.pathname === pathname &&
          typeof previous.recordedAt === "number" &&
          now - previous.recordedAt < DUPLICATE_WINDOW_MS
        ) {
          return;
        }
        sessionStorage.setItem(
          LAST_PAGE_VIEW_KEY,
          JSON.stringify({ pathname, recordedAt: now }),
        );
      } catch {
        sessionStorage.removeItem(LAST_PAGE_VIEW_KEY);
      }
      void logProductEvent("page_view").catch(() => undefined);
    }, 0);

    return () => window.clearTimeout(timer);
  }, [pathname]);

  return null;
}
