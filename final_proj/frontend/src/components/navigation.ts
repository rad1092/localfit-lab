import {
  FileChartColumn,
  House,
  MapPinned,
  UserRound,
  type LucideIcon,
} from "lucide-react";

export interface NavigationItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

/** Public product navigation. Admin tools stay in the account menu. */
export const mainNavigationItems: NavigationItem[] = [
  { label: "홈", href: "/", icon: House },
  { label: "상권분석", href: "/trade", icon: MapPinned },
  { label: "AI리포트", href: "/ai", icon: FileChartColumn },
  { label: "마이페이지", href: "/mypage", icon: UserRound },
];

export function navigationHref(href: string, areaCode: string | null) {
  if (!areaCode || href === "/") return href;
  const encoded = encodeURIComponent(areaCode);
  return `${href}?areaCode=${encoded}&area=${encoded}`;
}

export function isNavigationActive(pathname: string, href: string) {
  return href === "/"
    ? pathname === "/"
    : pathname === href || pathname.startsWith(`${href}/`);
}
