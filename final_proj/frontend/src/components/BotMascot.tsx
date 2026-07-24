"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { AnimatedChatbotMascot, type MascotState } from "./AnimatedChatbotMascot";

export type BotMascotMood = MascotState;

export function BotMascot({ mood = "idle", size = "md", decorative = false }: {
  mood?: BotMascotMood;
  size?: "sm" | "md" | "lg";
  decorative?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const pixels = size === "sm" ? 48 : size === "lg" ? 144 : 96;
  const mounted = useSyncExternalStore(
    () => () => undefined,
    () => true,
    () => false
  );

  if (!mounted || !resolvedTheme) {
    return <span aria-hidden="true" style={{ display: "inline-block", width: pixels, height: pixels }} />;
  }

  const palette = resolvedTheme === "dark" ? "cream" : "navy";

  return (
    <AnimatedChatbotMascot
      state={mood}
      palette={palette}
      size={pixels}
      enableIdleSitStand
      alt={decorative ? "" : "상권 분석 챗봇"}
    />
  );
}
