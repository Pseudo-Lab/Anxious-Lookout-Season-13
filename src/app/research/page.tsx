"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import AuthGate from "@/components/research/AuthGate";
import { DocumentList, MaterialList, SessionList } from "@/components/research/ItemLists";
import CodexStatusBanner from "@/components/research/CodexStatusBanner";
import NewSessionForm from "@/components/research/NewSessionForm";

const TABS = [
  { key: "materials", label: "자료" },
  { key: "documents", label: "문서" },
  { key: "sessions", label: "대화" },
] as const;

type Tab = (typeof TABS)[number]["key"];

function ResearchHome() {
  const params = useSearchParams();
  const raw = params.get("tab");
  const tab: Tab = TABS.some((t) => t.key === raw) ? (raw as Tab) : "materials";

  return (
    <>
      <nav className="mb-6 flex gap-1 border-b border-stone-200" aria-label="내 연구 구분">
        {TABS.map((t) => (
          <Link
            key={t.key}
            href={`/research/?tab=${t.key}`}
            aria-current={tab === t.key ? "page" : undefined}
            className={`-mb-px border-b-2 px-4 py-2 text-sm font-medium ${
              tab === t.key ? "border-indigo-600 text-indigo-700" : "border-transparent text-stone-500 hover:text-stone-800"
            }`}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      <AuthGate>
        {tab === "materials" && <MaterialList />}
        {tab === "documents" && <DocumentList />}
        {tab === "sessions" && (
          <SessionList
            action={
              <div className="space-y-3">
                <CodexStatusBanner />
                <NewSessionForm />
              </div>
            }
          />
        )}
      </AuthGate>
    </>
  );
}

export default function ResearchPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="mb-2 text-2xl font-bold text-stone-900">내 연구</h1>
      <p className="mb-6 text-sm text-stone-500">수집한 자료와 작성한 문서, Codex와 나눈 대화를 모아 봅니다. 모두 본인에게만 보입니다.</p>
      <Suspense>
        <ResearchHome />
      </Suspense>
    </div>
  );
}
