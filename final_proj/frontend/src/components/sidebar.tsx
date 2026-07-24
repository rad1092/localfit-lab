"use client";

import {
  isNavigationActive,
  mainNavigationItems,
  navigationHref,
} from "@/components/navigation";
import clsx from "clsx";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

/** Mobile bottom navigation. Desktop navigation is rendered by Topbar. */
export function Sidebar() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const areaCode = searchParams.get("areaCode") || searchParams.get("area");

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-[70] flex min-h-16 border-t bg-card/95 px-1 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden"
      aria-label="모바일 주요 메뉴"
    >
      {mainNavigationItems.map((item) => {
        const Icon = item.icon;
        const active = isNavigationActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={navigationHref(item.href, areaCode)}
            aria-label={item.label}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "flex min-w-0 flex-1 flex-col items-center justify-center gap-1 px-0.5 py-2 text-[10px] font-semibold",
              active ? "text-primary" : "text-muted-foreground",
            )}
          >
            <Icon className="h-5 w-5 shrink-0" strokeWidth={active ? 2.4 : 1.8} />
            <span className="max-w-full truncate whitespace-nowrap">{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
