"use client";

import { useState } from "react";
import Link from "next/link";
import { API_ENABLED, SITE_NAME } from "@/lib/constants";
import LoginButton from "@/components/auth/LoginButton";
import { canUseResearch } from "@/lib/auth/api";
import { useAuth } from "@/hooks/useAuth";

export default function Header() {
  const { user, status } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);

  // 메뉴 노출은 화면 안내용일 뿐이며 실제 권한은 backend가 검증한다.
  // 글 쓰기·관리는 아직 새 API로 대체되지 않아 "준비 중" 안내 화면으로 연결된다.
  const navLinks = [
    { href: "/", label: "홈", show: true },
    { href: "/posts/", label: "글 목록", show: true },
    { href: "/public/", label: "공개 문서", show: API_ENABLED },
    // 승인된 편집자·관리자에게만 안내한다. 이용 가능 여부는 각 API 응답으로 판단한다.
    { href: "/research/", label: "내 연구", show: status === "authenticated" && canUseResearch(user) },
    { href: "/write/", label: "글 쓰기", show: user?.isApproved === true },
    { href: "/admin/", label: "관리", show: user?.role === "admin" && user.isApproved },
    { href: "/status/", label: "상태", show: true },
  ].filter((l) => l.show);

  return (
    <header className="sticky top-0 z-50 border-b border-white/30 bg-white/60 backdrop-blur-md">
      <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2 text-xl font-bold tracking-tight text-stone-900">
          <svg className="h-7 w-7 text-indigo-600" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" d="M6.115 5.19l.319 1.913A6 6 0 0 0 8.11 10.36L9.75 12l-.387.775c-.217.433-.132.956.21 1.298l1.348 1.348c.21.21.329.497.329.795v1.089c0 .426.24.815.622 1.006l.153.076c.433.217.956.132 1.298-.21l.723-.723a8.7 8.7 0 0 0 2.288-4.042 1.087 1.087 0 0 0-.358-1.099l-1.33-1.108c-.251-.21-.582-.299-.905-.245l-1.17.195a1.125 1.125 0 0 1-.98-.314l-.295-.295a1.125 1.125 0 0 1 0-1.591l.13-.132a1.125 1.125 0 0 1 1.3-.21l.603.302a.809.809 0 0 0 1.086-1.086L14.25 7.5l1.256-.837a4.5 4.5 0 0 0 1.528-1.732l.146-.292M6.115 5.19A9 9 0 1 0 17.18 4.64M6.115 5.19A8.965 8.965 0 0 1 12 3c1.929 0 3.72.607 5.18 1.64" />
          </svg>
          {SITE_NAME}
        </Link>

        {/* Desktop nav */}
        <div className="hidden items-center gap-6 md:flex">
          <nav className="flex items-center gap-5 text-sm font-medium">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className="text-stone-500 transition-colors hover:text-indigo-600"
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <LoginButton />
        </div>

        {/* Mobile hamburger */}
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="flex h-9 w-9 items-center justify-center rounded-lg text-stone-600 hover:bg-stone-100 md:hidden"
        >
          {mobileOpen ? (
            <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
            </svg>
          ) : (
            <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25h16.5" />
            </svg>
          )}
        </button>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="border-t border-stone-100 bg-white px-6 pb-4 md:hidden">
          <nav className="flex flex-col gap-1 py-2">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setMobileOpen(false)}
                className="rounded-lg px-3 py-2 text-sm font-medium text-stone-600 transition-colors hover:bg-stone-50 hover:text-indigo-600"
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <div className="border-t border-stone-100 pt-3">
            <LoginButton />
          </div>
        </div>
      )}
    </header>
  );
}
