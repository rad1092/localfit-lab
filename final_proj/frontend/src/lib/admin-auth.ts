"use client";

import { useEffect, useState } from "react";
import { apiUrl, fetchAuth } from "@/lib/api";

export type AdminAccessState = "checking" | "allowed" | "denied" | "anonymous";

export function useAdminAccess(): AdminAccessState {
  const [access, setAccess] = useState<AdminAccessState>("checking");

  useEffect(() => {
    const controller = new AbortController();

    async function verifyAccess() {
      try {
        const response = await fetchAuth(apiUrl("/admin/access"), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (response.ok) {
          const payload = (await response.json()) as { allowed?: boolean };
          setAccess(payload.allowed === false ? "denied" : "allowed");
          return;
        }
        setAccess(response.status === 401 ? "anonymous" : "denied");
      } catch {
        if (!controller.signal.aborted) {
          setAccess("denied");
        }
      }
    }

    void verifyAccess();
    return () => controller.abort();
  }, []);

  return access;
}
