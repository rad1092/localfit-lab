"use client";

import { apiUrl, fetchAuth } from "@/lib/api";
import { useSearchParams } from "next/navigation";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

export interface SelectedArea {
  areaCode: string;
  areaName: string;
  latitude: number | null;
  longitude: number | null;
}

interface SelectedAreaContextValue {
  selectedArea: SelectedArea | null;
  setSelectedArea: (area: SelectedArea | null) => void;
  restoring: boolean;
  restoreError: string;
  retryRestore: () => void;
}

const SelectedAreaContext = createContext<SelectedAreaContextValue | null>(null);

export function SelectedAreaProvider({ children }: { children: React.ReactNode }) {
  const searchParams = useSearchParams();
  const urlAreaCode = searchParams.get("areaCode") || searchParams.get("area");
  const [selectedArea, setSelectedArea] = useState<SelectedArea | null>(null);
  const [restoring, setRestoring] = useState(false);
  const [restoreError, setRestoreError] = useState("");
  const [restoreKey, setRestoreKey] = useState(0);

  useEffect(() => {
    if (!urlAreaCode || selectedArea?.areaCode === urlAreaCode) return;

    const controller = new AbortController();
    const startTimer = window.setTimeout(() => {
      setRestoring(true);
      setRestoreError("");
    }, 0);
    fetchAuth(apiUrl(`/areas/${encodeURIComponent(urlAreaCode)}`), {
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("선택한 상권 정보를 불러오지 못했습니다.");
        return response.json();
      })
      .then((area) => {
        if (controller.signal.aborted) return;
        setSelectedArea({
          areaCode: area.area_code,
          areaName: area.area_name,
          latitude: typeof area.latitude === "number" ? area.latitude : null,
          longitude: typeof area.longitude === "number" ? area.longitude : null,
        });
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setRestoreError(error instanceof Error ? error.message : "상권 정보를 불러오지 못했습니다.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setRestoring(false);
      });

    return () => {
      window.clearTimeout(startTimer);
      controller.abort();
    };
  }, [restoreKey, selectedArea?.areaCode, urlAreaCode]);

  const retryRestore = useCallback(() => setRestoreKey((value) => value + 1), []);
  const value = useMemo(
    () => ({ selectedArea, setSelectedArea, restoring, restoreError, retryRestore }),
    [selectedArea, restoring, restoreError, retryRestore],
  );

  return <SelectedAreaContext.Provider value={value}>{children}</SelectedAreaContext.Provider>;
}

export function useSelectedArea() {
  const context = useContext(SelectedAreaContext);
  if (!context) throw new Error("useSelectedArea must be used inside SelectedAreaProvider");
  return context;
}
