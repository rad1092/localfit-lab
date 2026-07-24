import type { Metadata } from "next";
import { Suspense } from "react";
import "./globals.css";
import { AppMain } from "@/components/AppMain";
import { Chatbot } from "@/components/Chatbot";
import { ProductEventTracker } from "@/components/ProductEventTracker";
import { ReportJobProvider } from "@/components/report-job-context";
import { SelectedAreaProvider } from "@/components/selected-area-context";
import { Sidebar } from "@/components/sidebar";
import { ThemeProvider } from "@/components/theme-provider";
import { Topbar } from "@/components/topbar";

export const metadata: Metadata = {
  title: "LocalFit Lab",
  description: "서울 상권을 탐색하고 출점 조건을 검토하는 입지 분석 워크스페이스",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body className="flex h-dvh overflow-hidden bg-background text-foreground" suppressHydrationWarning>
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
          <ReportJobProvider>
            <Suspense fallback={null}>
              <SelectedAreaProvider>
                <ProductEventTracker />
                <Sidebar />
                <div className="flex min-w-0 flex-1 flex-col">
                  <Topbar />
                  <AppMain>{children}</AppMain>
                </div>
              </SelectedAreaProvider>
            </Suspense>
            <Chatbot />
          </ReportJobProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
