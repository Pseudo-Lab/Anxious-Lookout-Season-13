"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { describeFailure } from "@/lib/api/client";
import { GITHUB_LOGIN_URL, authErrorMessage } from "@/lib/auth/api";
import GitHubIcon from "@/components/auth/GitHubIcon";

function LoginPanel() {
  const searchParams = useSearchParams();
  const authError = searchParams.get("auth_error");
  const { status, user, failure, refresh } = useAuth();

  return (
    <>
      {authError !== null && (
        <p role="alert" className="mb-6 rounded-lg bg-red-50 px-4 py-3 text-center text-sm text-red-700">
          {authErrorMessage(authError)}
        </p>
      )}

      {status === "authenticated" && user ? (
        <div className="text-center">
          <p className="text-sm text-stone-600">
            <span className="font-medium text-stone-900">{user.login}</span> 계정으로 로그인되어 있습니다.
          </p>
          {!user.isApproved && (
            <p className="mt-2 text-sm text-amber-700">관리자 승인을 기다리고 있습니다.</p>
          )}
          <Link href="/" className="mt-6 inline-block text-sm font-medium text-indigo-600 hover:text-indigo-800">
            홈으로
          </Link>
        </div>
      ) : (
        <>
          {/* 백엔드 로그인 시작 경로로 전체 페이지 이동 */}
          <a
            href={GITHUB_LOGIN_URL}
            className="flex w-full items-center justify-center gap-3 rounded-lg bg-stone-900 px-4 py-3 text-sm font-medium text-white transition-colors hover:bg-stone-800"
          >
            <GitHubIcon className="h-5 w-5" />
            GitHub로 계속하기
          </a>
          {status === "error" && failure && (
            <p className="mt-4 text-center text-sm text-amber-700">
              로그인 상태를 확인하지 못했습니다. {describeFailure(failure)}{" "}
              <button onClick={() => void refresh()} className="underline hover:text-amber-900">
                다시 시도
              </button>
            </p>
          )}
        </>
      )}
    </>
  );
}

export default function LoginPage() {
  return (
    <div className="-mx-6 -mt-10 flex min-h-[70vh] items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-50 via-white to-amber-50">
      <div className="mx-4 w-full max-w-sm rounded-2xl bg-white p-8 shadow-lg ring-1 ring-stone-200/60">
        <div className="mb-6 flex justify-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600">
            <svg className="h-7 w-7 text-white" fill="none" stroke="currentColor" strokeWidth={1.5} viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
            </svg>
          </div>
        </div>

        <h1 className="mb-1 text-center text-2xl font-bold text-stone-900">
          초조한 전망대
        </h1>
        <p className="mb-8 text-center text-sm text-stone-500">
          GitHub 계정으로 시작하세요
        </p>

        <Suspense>
          <LoginPanel />
        </Suspense>

        <p className="mt-6 text-center text-xs text-stone-400">
          가입 후 관리자 승인이 필요합니다
        </p>
      </div>
    </div>
  );
}
