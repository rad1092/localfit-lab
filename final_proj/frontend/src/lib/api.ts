const configuredApiBase = (process.env.NEXT_PUBLIC_API_BASE_URL || "")
  .trim()
  .replace(/\/+$/, "");

export const API_BASE_URL = configuredApiBase
  ? configuredApiBase.endsWith("/api")
    ? configuredApiBase
    : `${configuredApiBase}/api`
  : "/api";

const ANONYMOUS_SESSION_KEY = "localfit:anonymous-session";
const ANONYMOUS_SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const recentProductEvents = new Map<string, number>();

interface StoredAnonymousSession {
  id: string;
  createdAt: number;
}

export type ProductEventName =
  | "page_view"
  | "search_submitted"
  | "area_selected"
  | "report_requested"
  | "report_completed"
  | "report_failed";

export interface ProductEventContext {
  area_code?: string;
}

export function apiUrl(path: string) {
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

function createAnonymousSessionId() {
  const cryptoApi = typeof globalThis !== "undefined"
    ? (globalThis.crypto as Crypto | undefined)
    : undefined;
  if (cryptoApi?.randomUUID) {
    return cryptoApi.randomUUID();
  }

  if (cryptoApi) {
    const bytes = new Uint8Array(16);
    cryptoApi.getRandomValues(bytes);
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
  }

  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function getAnonymousSessionId(): string | null {
  if (typeof window === "undefined") return null;

  const now = Date.now();
  try {
    const stored = JSON.parse(
      localStorage.getItem(ANONYMOUS_SESSION_KEY) || "null",
    ) as StoredAnonymousSession | null;
    if (
      stored?.id &&
      Number.isFinite(stored.createdAt) &&
      now - stored.createdAt < ANONYMOUS_SESSION_TTL_MS
    ) {
      return stored.id;
    }
  } catch {
    localStorage.removeItem(ANONYMOUS_SESSION_KEY);
  }

  const nextSession: StoredAnonymousSession = {
    id: createAnonymousSessionId(),
    createdAt: now,
  };
  localStorage.setItem(ANONYMOUS_SESSION_KEY, JSON.stringify(nextSession));
  return nextSession.id;
}

export interface AuthUser {
  id: number;
  email: string;
  nickname: string;
  created_at: string;
  is_admin: boolean;
}

export async function fetchAuth(url: string, options: RequestInit = {}) {
  const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
  const anonymousSessionId = getAnonymousSessionId();

  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  if (anonymousSessionId) {
    headers.set("X-LocalFit-Session", anonymousSessionId);
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 401 && typeof window !== "undefined") {
    if (localStorage.getItem("guest_mode") === "true") {
      return response;
    }
    localStorage.removeItem("token");
    window.location.href = "/login";
  }

  return response;
}

export async function logProductEvent(
  eventName: ProductEventName,
  context: ProductEventContext = {},
) {
  const eventKey = `${eventName}:${context.area_code || ""}`;
  const now = Date.now();
  const duplicateWindow = eventName === "area_selected" ? 5_000 : 0;
  if (duplicateWindow && now - (recentProductEvents.get(eventKey) || 0) < duplicateWindow) {
    return;
  }
  recentProductEvents.set(eventKey, now);

  const response = await fetchAuth(apiUrl("/events/log"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: eventName, ...context }),
    keepalive: true,
  });

  if (!response.ok) {
    throw new Error(`Event logging failed (${response.status})`);
  }
}

export async function fetchCurrentUser(): Promise<AuthUser | null> {
  if (typeof window === "undefined" || !localStorage.getItem("token")) return null;
  const response = await fetchAuth(apiUrl("/auth/me"), { cache: "no-store" });
  if (!response.ok) return null;
  return (await response.json()) as AuthUser;
}

export const AUTH_CHANGED_EVENT = "localfit:auth-changed";

export function notifyAuthChanged() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_CHANGED_EVENT));
  }
}
