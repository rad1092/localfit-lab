"use client";

import { DemoMap } from "@/components/DemoMap";
import { apiUrl } from "@/lib/api";
import { analyzeOfficialAreas, analyzeSpatialZone, fetchOfficialAreaBoundary } from "@/lib/spatial";
import { AreaBoundaryFeature, GeoJsonPolygonGeometry, SpatialZoneAnalysis, ZoneShape } from "@/types/spatial";
import {
  Circle,
  Layers2,
  LoaderCircle,
  MapPinned,
  Pentagon,
  RotateCcw,
  Square,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

const KAKAO_MAP_APP_KEY =
  process.env.NEXT_PUBLIC_KAKAO_MAP_API_KEY || process.env.NEXT_PUBLIC_KAKAO_REST_API_KEY;
const DEMO_MAP_ENABLED = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

let kakaoMapSdkPromise: Promise<void> | null = null;

const logExternalApiCall = async (apiName: string, endpoint: string, statusCode: number, responseTimeMs?: number) => {
  try {
    await fetch(apiUrl("/admin/external-api-log"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        api_name: apiName,
        endpoint: endpoint,
        status_code: statusCode,
        response_time_ms: responseTimeMs,
        call_type: "GET"
      }),
    });
  } catch (err) {
    console.error("Failed to log external API call:", err);
  }
};

function loadKakaoMapSdk(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new Error("Kakao Map SDK는 브라우저에서만 사용할 수 있습니다."));
  }
  if (window.kakao?.maps) {
    return Promise.resolve();
  }
  if (!KAKAO_MAP_APP_KEY) {
    return Promise.reject(new Error("NEXT_PUBLIC_KAKAO_MAP_API_KEY가 설정되지 않았습니다."));
  }

  if (!kakaoMapSdkPromise) {
    kakaoMapSdkPromise = new Promise((resolve, reject) => {
      const existingScript = document.querySelector<HTMLScriptElement>(
        'script[data-kakao-map-sdk="true"], script[src*="dapi.kakao.com/v2/maps/sdk.js"]'
      );
      const script = existingScript || document.createElement("script");
      let settled = false;

      const finish = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        if (window.kakao?.maps) {
          logExternalApiCall("Kakao Map SDK", "https://dapi.kakao.com/v2/maps/sdk.js", 200, 150);
          resolve();
        } else {
          logExternalApiCall("Kakao Map SDK", "https://dapi.kakao.com/v2/maps/sdk.js", 500, 150);
          reject(new Error("Kakao Map SDK가 로드됐지만 window.kakao.maps가 생성되지 않았습니다."));
        }
      };
      const fail = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        kakaoMapSdkPromise = null;
        logExternalApiCall("Kakao Map SDK", "https://dapi.kakao.com/v2/maps/sdk.js", 500, 150);
        reject(new Error("Kakao Map SDK 네트워크 로드에 실패했습니다."));
      };
      const timeout = window.setTimeout(() => {
        kakaoMapSdkPromise = null;
        fail();
      }, 10000);


      script.addEventListener("load", finish, { once: true });
      script.addEventListener("error", fail, { once: true });

      if (!existingScript) {
        script.dataset.kakaoMapSdk = "true";
        script.async = true;
        script.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${encodeURIComponent(KAKAO_MAP_APP_KEY)}&libraries=services,clusterer,drawing&autoload=false`;
        document.head.appendChild(script);
      }
    });
  }

  return kakaoMapSdkPromise;
}

interface KakaoMapProps {
  lat: number;
  lng: number;
  areaName?: string;
  areaCode?: string;
  level?: number;
  resolveByName?: boolean;
  enableAnalysisTools?: boolean;
  industryQuery?: string;
  onZoneAnalysisChange?: (analysis: SpatialZoneAnalysis | null) => void;
}

type DrawingTool = "circle" | "rectangle" | "polygon";
type AnalysisStatus = "idle" | "drawing" | "analyzing" | "ready" | "error";
type KakaoOverlay = { setMap: (map: unknown | null) => void };
type DrawingShapeData = {
  center: { x: number; y: number };
  radius: number;
  sPoint: { x: number; y: number };
  ePoint: { x: number; y: number };
  points: Array<{ x: number; y: number }>;
};
type KakaoDrawingManager = {
  getData: () => Record<string, DrawingShapeData[]>;
  getOverlays: (types?: unknown[]) => Record<string, KakaoOverlay[]>;
  cancel: () => void;
  remove: (overlay: KakaoOverlay) => void;
  select: (type: unknown) => void;
  undo: () => void;
  addListener: (event: string, handler: () => void) => void;
  removeListener?: (event: string, handler: () => void) => void;
};

const DRAWING_TOOLS: Array<{
  key: DrawingTool;
  label: string;
  icon: typeof Circle;
}> = [
  { key: "circle", label: "원형 영역 그리기", icon: Circle },
  { key: "rectangle", label: "사각형 영역 그리기", icon: Square },
  { key: "polygon", label: "다각형 영역 그리기", icon: Pentagon },
];

type KakaoPlace = {
  id?: string;
  place_name: string;
  category_name: string;
  road_address_name?: string;
  address_name?: string;
  phone?: string;
  place_url?: string;
  x: string;
  y: string;
};

function safeKakaoPlaceUrl(value?: string) {
  if (!value) return null;
  try {
    const url = new URL(value);
    const isKakaoHost = url.hostname === "kakao.com" || url.hostname.endsWith(".kakao.com");
    return isKakaoHost && ["http:", "https:"].includes(url.protocol) ? url.toString() : null;
  } catch {
    return null;
  }
}

function createPoiOverlayContent(place: KakaoPlace, pinColor: string) {
  const container = document.createElement("div");
  container.className = "relative z-10 flex flex-col items-center";

  const popoverId = `kakao-poi-${place.id || `${place.x}-${place.y}`}`.replace(/[^a-zA-Z0-9-_]/g, "-");
  const popover = document.createElement("div");
  popover.id = popoverId;
  popover.hidden = true;
  popover.className = "absolute bottom-full left-1/2 z-50 mb-2 min-w-[220px] -translate-x-1/2";
  popover.setAttribute("role", "group");
  popover.setAttribute("aria-label", `${place.place_name} 상세 정보`);

  const card = document.createElement("div");
  card.className = "rounded-md border border-slate-200 bg-white p-3 text-left shadow-xl";

  const title = document.createElement("p");
  title.className = "whitespace-nowrap text-sm font-extrabold text-slate-900";
  title.textContent = place.place_name;

  const category = document.createElement("p");
  category.className = "mt-0.5 text-xs font-medium text-slate-500";
  category.textContent = place.category_name.split(" > ").pop() || "주변 시설";

  const details = document.createElement("div");
  details.className = "mt-2 rounded-md bg-slate-50 p-2 text-[11px] leading-5 text-slate-600";

  const address = document.createElement("p");
  address.textContent = place.road_address_name || place.address_name || "주소 정보 없음";
  details.appendChild(address);

  const phone = document.createElement("p");
  phone.textContent = place.phone || "전화번호 정보 없음";
  details.appendChild(phone);

  card.append(title, category, details);

  const placeUrl = safeKakaoPlaceUrl(place.place_url);
  if (placeUrl) {
    const link = document.createElement("a");
    link.href = placeUrl;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.className = "mt-2 inline-flex text-xs font-bold text-blue-700 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500";
    link.textContent = "카카오맵에서 보기";
    card.appendChild(link);
  }

  popover.appendChild(card);

  const markerButton = document.createElement("button");
  markerButton.type = "button";
  markerButton.className = "flex h-10 w-9 items-end justify-center rounded-sm outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2";
  markerButton.setAttribute("aria-label", `${place.place_name} 정보 보기`);
  markerButton.setAttribute("aria-expanded", "false");
  markerButton.setAttribute("aria-controls", popoverId);

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", "32");
  svg.setAttribute("height", "40");
  svg.setAttribute("viewBox", "0 0 32 40");
  svg.setAttribute("aria-hidden", "true");
  svg.classList.add("drop-shadow-md");

  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", pinColor);
  path.setAttribute("d", "M16 0C7.163 0 0 7.163 0 16c0 12 16 24 16 24s16-12 16-24C32 7.163 24.837 0 16 0zm0 22c-3.314 0-6-2.686-6-6s2.686-6 6-6 6 2.686 6 6-2.686 6-6 6z");
  svg.appendChild(path);
  markerButton.appendChild(svg);

  let expanded = false;
  let pointerInteraction = false;
  const setExpanded = (next: boolean) => {
    expanded = next;
    popover.hidden = !next;
    markerButton.setAttribute("aria-expanded", String(next));
    container.classList.toggle("z-50", next);
    container.classList.toggle("z-10", !next);
  };

  markerButton.addEventListener("pointerdown", () => {
    pointerInteraction = true;
  });
  markerButton.addEventListener("click", (event) => {
    event.stopPropagation();
    setExpanded(!expanded);
    pointerInteraction = false;
  });
  markerButton.addEventListener("focus", () => {
    if (!pointerInteraction) setExpanded(true);
  });
  markerButton.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setExpanded(false);
      markerButton.focus();
    }
  });
  container.addEventListener("mouseenter", () => setExpanded(true));
  container.addEventListener("mouseleave", () => {
    if (!container.contains(document.activeElement)) setExpanded(false);
  });
  container.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (!container.contains(document.activeElement)) setExpanded(false);
    }, 0);
  });
  popover.addEventListener("click", (event) => event.stopPropagation());

  container.append(popover, markerButton);
  return container;
}

function closeGeoJsonRing(points: number[][]) {
  if (points.length === 0) return points;
  const first = points[0];
  const last = points.at(-1);
  if (!last || first[0] !== last[0] || first[1] !== last[1]) {
    return [...points, [...first]];
  }
  return points;
}

function currentDrawingShape(manager: KakaoDrawingManager): ZoneShape | null {
  const drawing = window.kakao?.maps?.drawing;
  if (!drawing || !manager) return null;

  const overlayType = drawing.OverlayType;
  const data = manager.getData();
  const circles = data[overlayType.CIRCLE] || [];
  const rectangles = data[overlayType.RECTANGLE] || [];
  const polygons = data[overlayType.POLYGON] || [];

  if (circles.length > 0) {
    const circle = circles.at(-1);
    if (!circle) return null;
    return {
      kind: "circle",
      center: [Number(circle.center.x), Number(circle.center.y)],
      radius_m: Number(circle.radius),
    };
  }
  if (rectangles.length > 0) {
    const rectangle = rectangles.at(-1);
    if (!rectangle) return null;
    const west = Math.min(Number(rectangle.sPoint.x), Number(rectangle.ePoint.x));
    const east = Math.max(Number(rectangle.sPoint.x), Number(rectangle.ePoint.x));
    const south = Math.min(Number(rectangle.sPoint.y), Number(rectangle.ePoint.y));
    const north = Math.max(Number(rectangle.sPoint.y), Number(rectangle.ePoint.y));
    return {
      kind: "rectangle",
      geometry: {
        type: "Polygon",
        coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
      },
    };
  }
  if (polygons.length > 0) {
    const polygon = polygons.at(-1);
    if (!polygon) return null;
    const ring = closeGeoJsonRing(
      (polygon.points || []).map((point: { x: number; y: number }) => [Number(point.x), Number(point.y)])
    );
    if (ring.length < 4) return null;
    return {
      kind: "polygon",
      geometry: { type: "Polygon", coordinates: [ring] },
    };
  }
  return null;
}

function clearDrawingOverlays(manager: KakaoDrawingManager) {
  const drawing = window.kakao?.maps?.drawing;
  if (!drawing || !manager) return;
  manager.cancel();
  const overlayTypes = [
    drawing.OverlayType.CIRCLE,
    drawing.OverlayType.RECTANGLE,
    drawing.OverlayType.POLYGON,
  ];
  const overlayGroups = manager.getOverlays(overlayTypes);
  overlayTypes.forEach((type) => {
    const overlays = overlayGroups[String(type)] || [];
    overlays.forEach((overlay) => manager.remove(overlay));
  });
}

function polygonCoordinateSets(geometry: GeoJsonPolygonGeometry): number[][][][] {
  if (geometry.type === "Polygon") {
    return [geometry.coordinates as number[][][]];
  }
  return geometry.coordinates as number[][][][];
}

function createOfficialBoundaryOverlays(map: unknown, feature: AreaBoundaryFeature): KakaoOverlay[] {
  return polygonCoordinateSets(feature.geometry).flatMap((polygonCoordinates) => {
    const rings = polygonCoordinates.map((ring) =>
      ring.map(([lon, lat]) => new window.kakao.maps.LatLng(lat, lon))
    );
    const path = rings.length === 1 ? rings[0] : rings;
    const halo = new window.kakao.maps.Polygon({
      map,
      path,
      strokeWeight: 8,
      strokeColor: "#ffffff",
      strokeOpacity: 0.82,
      strokeStyle: "solid",
      fillOpacity: 0,
      zIndex: 2,
      clickable: false,
    });
    const polygon = new window.kakao.maps.Polygon({
      map,
      path,
      strokeWeight: 3,
      strokeColor: "#0f766e",
      strokeOpacity: 0.95,
      strokeStyle: "solid",
      fillColor: "#14b8a6",
      fillOpacity: 0.12,
      zIndex: 3,
      clickable: false,
    });
    return [halo, polygon];
  });
}

function formatZoneArea(areaM2: number) {
  if (areaM2 >= 1_000_000) return `${(areaM2 / 1_000_000).toFixed(2)}㎢`;
  return `${Math.round(areaM2).toLocaleString()}㎡`;
}

export function KakaoMap(props: KakaoMapProps) {
  if (DEMO_MAP_ENABLED) {
    return <DemoMap {...props} />;
  }
  return <KakaoProductionMap {...props} />;
}

function KakaoProductionMap({
  lat,
  lng,
  areaName,
  areaCode,
  level = 4,
  resolveByName = true,
  enableAnalysisTools = false,
  industryQuery,
  onZoneAnalysisChange,
}: KakaoMapProps) {
  const mapRef = useRef<HTMLDivElement>(null);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [mapError, setMapError] = useState<string | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [mapInstance, setMapInstance] = useState<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersRef = useRef<any[]>([]);
  const mapInitializedRef = useRef(false);
  const [selectedCategory, setSelectedCategory] = useState<string>('FD6');
  const officialBoundaryOverlaysRef = useRef<KakaoOverlay[]>([]);
  const drawingManagerRef = useRef<KakaoDrawingManager | null>(null);
  const officialBoundaryFeatureRef = useRef<AreaBoundaryFeature | null>(null);
  const analysisAbortRef = useRef<AbortController | null>(null);
  const analysisTimerRef = useRef<number | null>(null);
  const ignoreDrawingStateRef = useRef(false);
  const onZoneAnalysisChangeRef = useRef(onZoneAnalysisChange);
  const runSpatialAnalysisRef = useRef<(shape: ZoneShape) => Promise<void>>(async () => undefined);
  const [activeDrawingTool, setActiveDrawingTool] = useState<DrawingTool | null>(null);
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>("idle");
  const [analysisResult, setAnalysisResult] = useState<SpatialZoneAnalysis | null>(null);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [mapStyle, setMapStyle] = useState<"roadmap" | "hybrid">("roadmap");

  const CATEGORIES = [
    { code: 'FD6', name: '음식점' },
    { code: 'CE7', name: '카페' },
    { code: 'CS2', name: '편의점' },
    { code: 'PM9', name: '약국' },
    { code: 'HP8', name: '병원' },
    { code: 'MT1', name: '대형마트' }
  ];

  useEffect(() => {
    onZoneAnalysisChangeRef.current = onZoneAnalysisChange;
  }, [onZoneAnalysisChange]);

  const executeSpatialAnalysis = useCallback(
    async (request: (signal: AbortSignal) => Promise<SpatialZoneAnalysis>) => {
      analysisAbortRef.current?.abort();
      const controller = new AbortController();
      analysisAbortRef.current = controller;
      setAnalysisStatus("analyzing");
      setAnalysisError(null);
      try {
        const result = await request(controller.signal);
        if (controller.signal.aborted) return;
        setAnalysisResult(result);
        setAnalysisStatus("ready");
        onZoneAnalysisChangeRef.current?.(result);
      } catch (error) {
        if (controller.signal.aborted) return;
        setAnalysisResult(null);
        setAnalysisStatus("error");
        setAnalysisError(error instanceof Error ? error.message : "영역 분석을 완료하지 못했습니다.");
        onZoneAnalysisChangeRef.current?.(null);
      }
    },
    []
  );

  const runSpatialAnalysis = useCallback(
    (shape: ZoneShape) =>
      executeSpatialAnalysis((signal) => analyzeSpatialZone(shape, { industryQuery, signal })),
    [executeSpatialAnalysis, industryQuery]
  );

  const runOfficialAreaAnalysis = useCallback(
    (areaCodes: string[]) =>
      executeSpatialAnalysis((signal) => analyzeOfficialAreas(areaCodes, { industryQuery, signal })),
    [executeSpatialAnalysis, industryQuery]
  );

  useEffect(() => {
    runSpatialAnalysisRef.current = runSpatialAnalysis;
  }, [runSpatialAnalysis]);

  useEffect(() => {
    let cancelled = false;
    const initMap = () => {
      if (cancelled) return;
      if (!mapInitializedRef.current && window.kakao?.maps && mapRef.current) {
        mapInitializedRef.current = true;
        setMapError(null);
        window.kakao.maps.load(() => {
          const options = {
            center: new window.kakao.maps.LatLng(lat, lng),
            level,
          };
          const map = new window.kakao.maps.Map(mapRef.current, options);
          setMapInstance(map);
          setMapLoaded(true);
          window.setTimeout(() => {
            map.relayout();
            map.setCenter(options.center);
          }, 0);
        });
      }
    };

    loadKakaoMapSdk()
      .then(initMap)
      .catch((error) => {
        if (!cancelled) {
          setMapError(`${error.message} 도메인 등록과 JavaScript 키를 확인하세요.`);
        }
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Initialize only once

  useEffect(() => {
    const controller = new AbortController();
    officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(null));
    officialBoundaryOverlaysRef.current = [];
    officialBoundaryFeatureRef.current = null;

    if (!mapInstance || !areaCode || !window.kakao?.maps) {
      return () => controller.abort();
    }

    fetchOfficialAreaBoundary(areaCode, controller.signal)
      .then((feature) => {
        if (controller.signal.aborted) return;
        officialBoundaryFeatureRef.current = feature;
        officialBoundaryOverlaysRef.current = createOfficialBoundaryOverlays(mapInstance, feature);
      })
      .catch((error) => {
        if (!controller.signal.aborted) {
          setAnalysisError(error instanceof Error ? error.message : "공식 상권 경계를 불러오지 못했습니다.");
          if (enableAnalysisTools) setAnalysisStatus("error");
        }
      });

    return () => {
      controller.abort();
      officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(null));
      officialBoundaryOverlaysRef.current = [];
    };
  }, [areaCode, enableAnalysisTools, mapInstance]);

  useEffect(() => {
    if (!enableAnalysisTools || !mapInstance || !window.kakao?.maps?.drawing) return;

    const drawing = window.kakao.maps.drawing;
    const manager = new drawing.DrawingManager({
      map: mapInstance,
      drawingMode: [
        drawing.OverlayType.CIRCLE,
        drawing.OverlayType.RECTANGLE,
        drawing.OverlayType.POLYGON,
      ],
      guideTooltip: ["draw", "drag", "edit"],
      circleOptions: {
        draggable: true,
        removable: true,
        editable: true,
        strokeColor: "#c2410c",
        strokeOpacity: 0.95,
        strokeWeight: 3,
        fillColor: "#fb923c",
        fillOpacity: 0.2,
      },
      rectangleOptions: {
        draggable: true,
        removable: true,
        editable: true,
        strokeColor: "#c2410c",
        strokeOpacity: 0.95,
        strokeWeight: 3,
        fillColor: "#fb923c",
        fillOpacity: 0.2,
      },
      polygonOptions: {
        draggable: true,
        removable: true,
        editable: true,
        strokeColor: "#c2410c",
        strokeOpacity: 0.95,
        strokeWeight: 3,
        fillColor: "#fb923c",
        fillOpacity: 0.2,
        hintStrokeColor: "#c2410c",
        hintStrokeOpacity: 0.65,
        hintStrokeStyle: "dash",
      },
    });
    drawingManagerRef.current = manager;

    const analyzeCurrentDrawing = () => {
      if (ignoreDrawingStateRef.current) return;
      const shape = currentDrawingShape(manager);
      if (!shape) {
        setAnalysisResult(null);
        setAnalysisStatus("idle");
        setAnalysisError(null);
        onZoneAnalysisChangeRef.current?.(null);
        return;
      }
      void runSpatialAnalysisRef.current(shape);
    };
    const scheduleAnalysis = () => {
      if (ignoreDrawingStateRef.current) return;
      if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current);
      analysisTimerRef.current = window.setTimeout(analyzeCurrentDrawing, 320);
    };
    const handleDrawEnd = () => {
      officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(mapInstance));
      markersRef.current.forEach((overlay) => overlay.setMap(mapInstance));
      setActiveDrawingTool(null);
      scheduleAnalysis();
    };
    const handleStateChanged = () => scheduleAnalysis();

    manager.addListener("drawend", handleDrawEnd);
    manager.addListener("state_changed", handleStateChanged);

    return () => {
      if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current);
      analysisAbortRef.current?.abort();
      if (manager.removeListener) {
        manager.removeListener("drawend", handleDrawEnd);
        manager.removeListener("state_changed", handleStateChanged);
      }
      clearDrawingOverlays(manager);
      if (drawingManagerRef.current === manager) drawingManagerRef.current = null;
    };
  }, [enableAnalysisTools, mapInstance]);

  useEffect(() => {
    let cancelled = false;
    if (mapInstance && window.kakao?.maps) {
      if (!window.kakao.maps.services) {
        queueMicrotask(() => {
          if (!cancelled) setMapError("Kakao Map services 라이브러리를 사용할 수 없습니다.");
        });
        return () => {
          cancelled = true;
        };
      }
      mapInstance.relayout();
      // Clear previous markers
      markersRef.current.forEach((m) => {
        if (typeof m.setMap === 'function') m.setMap(null);
      });
      markersRef.current = [];
      
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const newMarkers: any[] = [];

      const ps = new window.kakao.maps.services.Places();
      
      const searchAndDraw = (centerLat: number, centerLng: number, title: string) => {
        if (cancelled) return;
        const moveLatLon = new window.kakao.maps.LatLng(centerLat, centerLng);
        mapInstance.setCenter(moveLatLon);
        
        const centerMarker = new window.kakao.maps.Marker({
          position: moveLatLon,
          map: mapInstance,
        });
        newMarkers.push(centerMarker);
        markersRef.current.push(centerMarker);

        const centerContent = `
          <div style="pointer-events: none; background-color: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 14px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); font-weight: 700; font-size: 14px; color: #0f172a; margin-bottom: 45px; white-space: nowrap; text-align: center; position: relative;">
            ${title}
            <div style="position: absolute; bottom: -7px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 7px solid transparent; border-right: 7px solid transparent; border-top: 7px solid white;"></div>
            <div style="position: absolute; bottom: -8px; left: 50%; transform: translateX(-50%); width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-top: 8px solid #e2e8f0; z-index: -1;"></div>
          </div>
        `;
        const centerOverlay = new window.kakao.maps.CustomOverlay({
          position: moveLatLon,
          content: centerContent,
          yAnchor: 1
        });
        centerOverlay.setMap(mapInstance);
        newMarkers.push(centerOverlay);
        markersRef.current.push(centerOverlay);

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ps.categorySearch(selectedCategory, (data: any[], status: any) => {
          if (cancelled) return;
          if (status === window.kakao.maps.services.Status.OK) {
            (data.slice(0, 15) as KakaoPlace[]).forEach(place => {
              const pos = new window.kakao.maps.LatLng(place.y, place.x);
              // Create custom colored marker image based on category
              let pinColor = '#3b82f6'; // default blue
              if (selectedCategory === 'FD6') pinColor = '#ef4444'; // red (Food)
              else if (selectedCategory === 'CE7') pinColor = '#f59e0b'; // orange (Cafe)
              else if (selectedCategory === 'CS2') pinColor = '#10b981'; // green (Convenience)
              else if (selectedCategory === 'PM9') pinColor = '#8b5cf6'; // purple (Pharmacy)
              else if (selectedCategory === 'HP8') pinColor = '#ec4899'; // pink (Hospital)
              else if (selectedCategory === 'MT1') pinColor = '#06b6d4'; // cyan (Mart)

              const container = createPoiOverlayContent(place, pinColor);
              
              const customOverlay = new window.kakao.maps.CustomOverlay({
                position: pos,
                content: container,
                yAnchor: 1,
                zIndex: 10
              });
              
              customOverlay.setMap(mapInstance);
              newMarkers.push(customOverlay);
              markersRef.current.push(customOverlay);
            });
          }
        }, {
          location: moveLatLon,
          radius: 500 // 500m radius
        });
      };

      if (areaName && resolveByName) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ps.keywordSearch(areaName, (data: any[], status: any) => {
          if (cancelled) return;
          if (status === window.kakao.maps.services.Status.OK) {
            searchAndDraw(Number(data[0].y), Number(data[0].x), areaName);
          } else {
            // Fallback to coordinates
            searchAndDraw(lat, lng, areaName);
          }
        });
      } else {
        searchAndDraw(lat, lng, areaName || "선택된 지역");
      }
    }

    return () => {
      cancelled = true;
    };
  }, [lat, lng, areaName, mapInstance, resolveByName, selectedCategory]);

  const activateDrawingTool = (tool: DrawingTool) => {
    const manager = drawingManagerRef.current;
    const drawing = window.kakao?.maps?.drawing;
    if (!manager || !drawing) return;

    analysisAbortRef.current?.abort();
    if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current);
    ignoreDrawingStateRef.current = true;
    clearDrawingOverlays(manager);
    ignoreDrawingStateRef.current = false;
    setAnalysisResult(null);
    setAnalysisError(null);
    setAnalysisStatus("drawing");
    setActiveDrawingTool(tool);
    onZoneAnalysisChangeRef.current?.(null);
    officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(null));
    markersRef.current.forEach((overlay) => overlay.setMap(null));

    const overlayType = {
      circle: drawing.OverlayType.CIRCLE,
      rectangle: drawing.OverlayType.RECTANGLE,
      polygon: drawing.OverlayType.POLYGON,
    }[tool];
    manager.select(overlayType);
  };

  const clearZoneAnalysis = () => {
    const manager = drawingManagerRef.current;
    analysisAbortRef.current?.abort();
    if (analysisTimerRef.current !== null) window.clearTimeout(analysisTimerRef.current);
    if (manager) {
      ignoreDrawingStateRef.current = true;
      clearDrawingOverlays(manager);
      ignoreDrawingStateRef.current = false;
    }
    setActiveDrawingTool(null);
    setAnalysisResult(null);
    setAnalysisError(null);
    setAnalysisStatus("idle");
    officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(mapInstance));
    markersRef.current.forEach((overlay) => overlay.setMap(mapInstance));
    onZoneAnalysisChangeRef.current?.(null);
  };

  const undoDrawing = () => {
    const manager = drawingManagerRef.current;
    if (!manager) return;
    manager.undo();
  };

  const toggleMapStyle = () => {
    if (!mapInstance || !window.kakao?.maps?.MapTypeId) return;
    const nextStyle = mapStyle === "roadmap" ? "hybrid" : "roadmap";
    mapInstance.setMapTypeId(
      nextStyle === "hybrid"
        ? window.kakao.maps.MapTypeId.HYBRID
        : window.kakao.maps.MapTypeId.ROADMAP
    );
    setMapStyle(nextStyle);
  };

  const analyzeOfficialBoundary = () => {
    if (!areaCode || !officialBoundaryFeatureRef.current) return;
    const manager = drawingManagerRef.current;
    if (manager) {
      ignoreDrawingStateRef.current = true;
      clearDrawingOverlays(manager);
      ignoreDrawingStateRef.current = false;
    }
    officialBoundaryOverlaysRef.current.forEach((overlay) => overlay.setMap(mapInstance));
    markersRef.current.forEach((overlay) => overlay.setMap(mapInstance));
    setActiveDrawingTool(null);
    void runOfficialAreaAnalysis([areaCode]);
  };

  const directStoreMetric = analysisResult?.metrics.find((metric) => metric.key === "store_count");

  return (
    <div className="relative h-full min-h-[320px] w-full overflow-hidden bg-[#e8eeeb]">
      <div
        className={`surface-shadow absolute left-3 top-3 z-20 rounded-md border bg-card/95 p-1.5 backdrop-blur ${
          enableAnalysisTools ? "max-w-[calc(100%-76px)]" : "max-w-[calc(100%-24px)]"
        }`}
      >
        <p className="px-2 pb-1 pt-0.5 text-[10px] font-bold text-muted-foreground">주변 시설 · 반경 500m · Kakao</p>
        <div className="flex gap-1 overflow-x-auto" role="group" aria-label="주변 시설 카테고리">
          {CATEGORIES.map(cat => (
            <button
              type="button"
              key={cat.code}
              aria-pressed={selectedCategory === cat.code}
              onClick={() => setSelectedCategory(cat.code)}
              className={`h-8 whitespace-nowrap rounded-md px-3 text-xs font-semibold transition-colors ${selectedCategory === cat.code ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </div>
      {enableAnalysisTools && (
        <div
          className="surface-shadow absolute right-3 top-3 z-30 flex w-11 flex-col items-center gap-1 rounded-md border bg-card/95 p-1 backdrop-blur"
          role="toolbar"
          aria-label="지도 분석 도구"
        >
          <button
            type="button"
            title={mapStyle === "hybrid" ? "일반 지도 보기" : "항공 지도 보기"}
            aria-label={mapStyle === "hybrid" ? "일반 지도 보기" : "항공 지도 보기"}
            aria-pressed={mapStyle === "hybrid"}
            onClick={toggleMapStyle}
            className={`flex h-9 w-9 items-center justify-center rounded-md transition-colors ${
              mapStyle === "hybrid"
                ? "bg-[#18211f] text-white"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            }`}
          >
            <Layers2 className="h-4 w-4" />
          </button>
          <span className="h-px w-7 bg-border" aria-hidden="true" />
          <button
            type="button"
            title="현재 공식 상권 경계 분석"
            aria-label="현재 공식 상권 경계 분석"
            disabled={!areaCode || analysisStatus === "analyzing"}
            onClick={analyzeOfficialBoundary}
            className="flex h-9 w-9 items-center justify-center rounded-md text-primary hover:bg-accent disabled:cursor-not-allowed disabled:opacity-35"
          >
            <MapPinned className="h-4 w-4" />
          </button>
          <span className="h-px w-7 bg-border" aria-hidden="true" />
          {DRAWING_TOOLS.map(({ key, label, icon: Icon }) => (
            <button
              type="button"
              key={key}
              title={label}
              aria-label={label}
              aria-pressed={activeDrawingTool === key}
              onClick={() => activateDrawingTool(key)}
              className={`flex h-9 w-9 items-center justify-center rounded-md transition-colors ${
                activeDrawingTool === key
                  ? "bg-[#c2410c] text-white"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground"
              }`}
            >
              <Icon className="h-4 w-4" />
            </button>
          ))}
          <span className="h-px w-7 bg-border" aria-hidden="true" />
          <button
            type="button"
            title="되돌리기"
            aria-label="영역 편집 되돌리기"
            onClick={undoDrawing}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <RotateCcw className="h-4 w-4" />
          </button>
          <button
            type="button"
            title="사용자 영역 지우기"
            aria-label="사용자 영역 지우기"
            onClick={clearZoneAnalysis}
            className="flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-red-50 hover:text-red-700"
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      )}
      {enableAnalysisTools && analysisStatus !== "idle" && (
        <div
          className={`surface-shadow absolute bottom-[76px] right-4 z-20 max-w-[calc(100%-32px)] rounded-md border px-3 py-2.5 backdrop-blur sm:bottom-4 ${
            analysisStatus === "error" ? "border-red-200 bg-red-50/95 text-red-800" : "bg-card/95"
          }`}
          role="status"
        >
          {analysisStatus === "drawing" && (
            <div className="flex items-center gap-2 text-sm font-bold text-[#9a3412]">
              <Pentagon className="h-4 w-4" />
              <span>사용자 영역 지정 중</span>
            </div>
          )}
          {analysisStatus === "analyzing" && (
            <div className="flex items-center gap-2 text-sm font-bold">
              <LoaderCircle className="h-4 w-4 animate-spin text-primary" />
              <span>공간 데이터 계산 중</span>
            </div>
          )}
          {analysisStatus === "ready" && analysisResult && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <strong className="text-sm text-foreground">{formatZoneArea(analysisResult.area_m2)}</strong>
              {directStoreMetric?.value !== null && directStoreMetric?.value !== undefined && (
                <span className="font-bold text-foreground">점포 {Number(directStoreMetric.value).toLocaleString()}개</span>
              )}
              <span className="font-semibold text-muted-foreground">
                공식 경계 {analysisResult.coverage.official_boundary_coverage_pct.toFixed(0)}%
              </span>
            </div>
          )}
          {analysisStatus === "error" && <p className="text-xs font-semibold">{analysisError}</p>}
        </div>
      )}
      {!mapLoaded && (
        <div className="absolute inset-0 z-0 flex items-center justify-center bg-muted/30">
          <span className="text-sm font-medium text-muted-foreground animate-pulse">지도를 불러오는 중입니다.</span>
        </div>
      )}
      {mapError && (
        <div className="absolute inset-0 z-40 flex items-center justify-center bg-background/85 p-6 text-center">
          <span className="max-w-sm rounded-lg border bg-card px-4 py-3 text-sm text-destructive shadow-sm">
            {mapError}
          </span>
        </div>
      )}
      <div ref={mapRef} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
