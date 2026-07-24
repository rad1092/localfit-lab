"use client";

import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import styles from "./AnimatedChatbotMascot.module.css";

export type MascotState = "idle" | "listening" | "thinking" | "speaking" | "success" | "error";
export type MascotPalette = "cream" | "navy";
type MascotFrame = Exclude<MascotState, "speaking"> | "blink" | "speaking-a" | "speaking-b" | `sit-stand-${0 | 1 | 2 | 3 | 4}`;

const frames: MascotFrame[] = [
  "idle", "blink", "listening", "thinking", "speaking-a", "speaking-b", "success", "error",
  "sit-stand-0", "sit-stand-1", "sit-stand-2", "sit-stand-3", "sit-stand-4",
];

function assets(directory: "light" | "dark") {
  return Object.fromEntries(frames.map((frame) => [frame, `/chatbot-mascot/${directory}/${frame}.png`])) as Record<MascotFrame, string>;
}

const mascotAssets: Record<MascotPalette, Record<MascotFrame, string>> = {
  cream: assets("light"),
  navy: assets("dark"),
};

function subscribeReducedMotion(callback: () => void) {
  const media = window.matchMedia("(prefers-reduced-motion: reduce)");
  media.addEventListener("change", callback);
  return () => media.removeEventListener("change", callback);
}

export function AnimatedChatbotMascot({
  state = "idle",
  palette = "navy",
  size = 112,
  enableIdleSitStand = true,
  className = "",
  alt = "상권 분석 챗봇",
}: {
  state?: MascotState;
  palette?: MascotPalette;
  size?: number;
  enableIdleSitStand?: boolean;
  className?: string;
  alt?: string;
}) {
  const reducedMotion = useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    () => true,
  );
  const [blink, setBlink] = useState(false);
  const [speakingFrame, setSpeakingFrame] = useState<"a" | "b">("a");
  const [idleFrame, setIdleFrame] = useState<`sit-stand-${0 | 1 | 2 | 3 | 4}` | null>(null);

  useEffect(() => {
    if (state !== "idle" || reducedMotion) return;
    let blinkTimer: ReturnType<typeof setTimeout>;
    let resetTimer: ReturnType<typeof setTimeout>;
    const scheduleBlink = () => {
      blinkTimer = setTimeout(() => {
        setBlink(true);
        resetTimer = setTimeout(() => {
          setBlink(false);
          scheduleBlink();
        }, 150);
      }, 2600 + Math.random() * 2200);
    };
    scheduleBlink();
    return () => {
      clearTimeout(blinkTimer);
      clearTimeout(resetTimer);
    };
  }, [state, reducedMotion]);

  useEffect(() => {
    if (state !== "idle" || !enableIdleSitStand || reducedMotion) return;
    const timers: Array<ReturnType<typeof setTimeout>> = [];
    let disposed = false;
    const later = (callback: () => void, delay: number) => {
      const timer = setTimeout(() => !disposed && callback(), delay);
      timers.push(timer);
    };
    const scheduleSitStand = () => later(runSitStand, 7000 + Math.random() * 5000);
    function runSitStand() {
      setIdleFrame("sit-stand-0");
      later(() => setIdleFrame("sit-stand-1"), 260);
      later(() => setIdleFrame("sit-stand-2"), 540);
      later(() => setIdleFrame("sit-stand-3"), 1900);
      later(() => setIdleFrame("sit-stand-4"), 2200);
      later(() => {
        setIdleFrame(null);
        scheduleSitStand();
      }, 2520);
    }
    scheduleSitStand();
    return () => {
      disposed = true;
      timers.forEach(clearTimeout);
    };
  }, [enableIdleSitStand, reducedMotion, state]);

  useEffect(() => {
    if (state !== "speaking" || reducedMotion) return;
    const timer = setInterval(() => setSpeakingFrame((current) => current === "a" ? "b" : "a"), 210);
    return () => clearInterval(timer);
  }, [reducedMotion, state]);

  const frame = useMemo<MascotFrame>(() => {
    if (reducedMotion) return state === "speaking" ? "speaking-a" : state;
    if (state === "idle" && idleFrame) return idleFrame;
    if (state === "idle" && blink) return "blink";
    if (state === "speaking") return `speaking-${speakingFrame}`;
    return state;
  }, [blink, idleFrame, reducedMotion, speakingFrame, state]);

  useEffect(() => {
    const oppositePalette: MascotPalette = palette === "navy" ? "cream" : "navy";
    const sources = new Set([
      mascotAssets.cream.idle,
      mascotAssets.navy.idle,
      mascotAssets[oppositePalette][frame],
    ]);
    const images = [...sources].map((src) => {
      const image = new Image();
      image.src = src;
      return image;
    });
    return () => images.forEach((image) => { image.src = ""; });
  }, [frame, palette]);

  return (
    <span
      className={`${styles.mascot} ${styles[state]} ${className}`}
      style={{ width: size, height: size }}
      data-palette={palette}
      data-state={state}
      data-action={!reducedMotion && idleFrame ? "sit-stand" : undefined}
    >
      {/* PNG frames are intentionally rendered without optimization so animation swaps immediately. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={mascotAssets[palette][frame]} alt={alt} draggable={false} width={size} height={size} />
    </span>
  );
}
