"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { describeFailure } from "@/lib/api/client";
import { GITHUB_LOGIN_URL, isAdmin, type AuthUser } from "@/lib/auth/api";
import GitHubIcon from "./GitHubIcon";

// #119 D4: 관리·상태·API 토큰은 상단 메뉴가 아니라 계정 메뉴에 둔다(표시용이며 권한은 서버가 판단한다).
function accountLinks(user: AuthUser) {
  return [
    { href: "/account/tokens/", label: "API 토큰", show: true },
    { href: "/admin/", label: "관리", show: user.role === "admin" && user.isApproved },
    // #94: 서비스 상태는 관리자에게만
    { href: "/status/", label: "서비스 상태", show: isAdmin(user) },
  ].filter((l) => l.show);
}

function isCurrent(pathname: string, href: string) {
  return pathname === href || pathname.startsWith(href);
}

function useSignOut() {
  const { signOut } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function run() {
    setPending(true);
    setError("");
    const result = await signOut();
    if (!result.ok) setError(`로그아웃 실패: ${describeFailure(result.failure)}`);
    setPending(false);
  }
  return { pending, error, run };
}

function PendingBadge() {
  return (
    <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">승인 대기</span>
  );
}

/** 넓은 화면: 계정 이름 버튼을 누르면 펼쳐지는 메뉴(disclosure). Esc·바깥 클릭·이동 시 닫힌다. */
function AccountDropdown({ user }: { user: AuthUser }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const panelId = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const out = useSignOut();
  const links = accountLinks(user);
  const current = links.some((l) => isCurrent(pathname, l.href));

  // 다른 화면으로 옮기면 닫는다(렌더 중 이전 값과 비교).
  const [seenPath, setSeenPath] = useState(pathname);
  if (seenPath !== pathname) {
    setSeenPath(pathname);
    setOpen(false);
  }

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open]);

  return (
    <div
      ref={root}
      className="relative"
      onKeyDown={(e) => {
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
          button.current?.focus();
        }
      }}
    >
      <button
        ref={button}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors hover:bg-stone-100 ${
          current ? "border-indigo-300 text-indigo-700" : "border-stone-300 text-stone-900"
        }`}
      >
        <span className="sr-only">계정 메뉴: </span>
        {user.login}
        {!user.isApproved && <PendingBadge />}
        <svg aria-hidden="true" className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
        </svg>
      </button>
      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 top-full z-50 mt-2 w-52 rounded-xl bg-white p-2 shadow-lg ring-1 ring-stone-200"
      >
        <ul className="space-y-0.5">
          {links.map((l) => (
            <li key={l.href}>
              <Link
                href={l.href}
                aria-current={isCurrent(pathname, l.href) ? "page" : undefined}
                className="block rounded-lg px-3 py-2 text-sm text-stone-900 hover:bg-stone-100 aria-[current=page]:font-semibold aria-[current=page]:text-indigo-700"
              >
                {l.label}
              </Link>
            </li>
          ))}
        </ul>
        <div className="mt-1 border-t border-stone-100 pt-1">
          <button
            type="button"
            onClick={() => void out.run()}
            disabled={out.pending}
            className="block w-full rounded-lg px-3 py-2 text-left text-sm text-stone-700 hover:bg-stone-100 disabled:opacity-50"
          >
            {out.pending ? "로그아웃 중..." : "로그아웃"}
          </button>
          {out.error && (
            <p role="alert" className="px-3 pb-1 text-xs text-red-600">
              {out.error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** 좁은 화면(모바일 메뉴 안): 펼친 목록 그대로. */
function AccountInline({ user, onNavigate }: { user: AuthUser; onNavigate?: () => void }) {
  const pathname = usePathname();
  const out = useSignOut();
  return (
    <div className="space-y-1">
      <p className="flex items-center gap-2 px-3 py-1 text-sm font-medium text-stone-900">
        <span className="sr-only">로그인한 계정: </span>
        {user.login}
        {!user.isApproved && <PendingBadge />}
      </p>
      <nav aria-label="계정" className="flex flex-col gap-1">
        {accountLinks(user).map((l) => (
          <Link
            key={l.href}
            href={l.href}
            onClick={onNavigate}
            aria-current={isCurrent(pathname, l.href) ? "page" : undefined}
            className="rounded-lg px-3 py-2 text-sm font-medium text-stone-900 hover:bg-stone-50 hover:text-indigo-600 aria-[current=page]:bg-indigo-50 aria-[current=page]:text-indigo-700"
          >
            {l.label}
          </Link>
        ))}
      </nav>
      <button
        type="button"
        onClick={() => void out.run()}
        disabled={out.pending}
        className="w-full rounded-lg px-3 py-2 text-left text-sm text-stone-700 hover:bg-stone-50 disabled:opacity-50"
      >
        {out.pending ? "로그아웃 중..." : "로그아웃"}
      </button>
      {out.error && (
        <p role="alert" className="px-3 text-xs text-red-600">
          {out.error}
        </p>
      )}
    </div>
  );
}

/**
 * 상단 계정 영역. 로그인 확인 중에는 자리만 잡아 둔다(#119 A2: 확인 뒤 메뉴가 밀리지 않게).
 * variant="inline"은 모바일 메뉴 안에 펼쳐 보인다.
 */
export default function LoginButton({ variant = "dropdown", onNavigate }: { variant?: "dropdown" | "inline"; onNavigate?: () => void }) {
  const { status, user, refresh } = useAuth();

  if (status === "disabled") return null;
  if (status === "loading") {
    return <span aria-hidden="true" className="block h-9 w-32 animate-pulse rounded-lg bg-stone-200/70" />;
  }

  if (status === "error") {
    return (
      <button
        onClick={() => void refresh()}
        className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1 text-sm text-amber-700 transition-colors hover:bg-amber-100"
      >
        로그인 상태 확인 실패 · 다시 시도
      </button>
    );
  }

  if (status === "unauthenticated" || !user) {
    // 백엔드 경로로의 전체 페이지 이동이므로 next/link가 아닌 <a>를 쓴다.
    return (
      <a
        href={GITHUB_LOGIN_URL}
        className="flex w-fit items-center gap-2 rounded-lg bg-stone-900 px-4 py-1.5 text-sm font-medium text-white transition-colors hover:bg-stone-800"
      >
        <GitHubIcon className="h-4 w-4" />
        GitHub 로그인
      </a>
    );
  }

  return variant === "inline" ? <AccountInline user={user} onNavigate={onNavigate} /> : <AccountDropdown user={user} />;
}
