import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { AuthProvider } from "@/components/auth/AuthProvider";
import { ChatProvider } from "@/components/chat/ChatProvider";
import { CodexStatusProvider } from "@/components/research/CodexStatusProvider";
import ChatDock from "@/components/chat/ChatDock";
import Header from "@/components/layout/Header";
import Footer from "@/components/layout/Footer";
import { SITE_NAME, SITE_DESCRIPTION } from "@/lib/constants";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: {
    default: SITE_NAME,
    template: `%s | ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  openGraph: {
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    type: "website",
    locale: "ko_KR",
  },
  robots: {
    index: true,
    follow: true,
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background font-sans">
        <AuthProvider>
          <CodexStatusProvider>
            <ChatProvider>
              {/* #119 B4: 상단 바가 화면에 붙어 있어(sticky) 키보드 사용자가 메뉴를 건너뛸 수 있게 한다. 초점을 받으면 보인다. */}
              <a
                href="#main"
                className="sr-only z-[60] rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4"
              >
                본문으로 건너뛰기
              </a>
              <Header />
              <main id="main" tabIndex={-1} className="mx-auto w-full max-w-7xl flex-1 px-6 py-10 focus:outline-none">
                {children}
              </main>
              <Footer />
              <ChatDock />
            </ChatProvider>
          </CodexStatusProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
