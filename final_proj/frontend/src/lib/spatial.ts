import { apiUrl } from "@/lib/api";
import { AreaBoundaryFeature, SpatialZoneAnalysis, ZoneShape } from "@/types/spatial";

async function apiJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = "공간 분석 요청을 처리하지 못했습니다.";
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : message;
    } catch {
      // Keep the stable user-facing fallback.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function fetchOfficialAreaBoundary(areaCode: string, signal?: AbortSignal) {
  return apiJson<AreaBoundaryFeature>(
    apiUrl(`/spatial/areas/${encodeURIComponent(areaCode)}/boundary?simplify_m=1.5`),
    { signal }
  );
}

export function analyzeSpatialZone(
  shape: ZoneShape,
  options?: { industryQuery?: string; signal?: AbortSignal }
) {
  return apiJson<SpatialZoneAnalysis>(apiUrl("/spatial/zones/analyze"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      shape,
      industry_query: options?.industryQuery?.trim() || null,
    }),
    signal: options?.signal,
  });
}

export function analyzeOfficialAreas(
  areaCodes: string[],
  options?: { industryQuery?: string; signal?: AbortSignal }
) {
  return apiJson<SpatialZoneAnalysis>(apiUrl("/spatial/zones/analyze"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      official_area_codes: areaCodes,
      industry_query: options?.industryQuery?.trim() || null,
    }),
    signal: options?.signal,
  });
}
