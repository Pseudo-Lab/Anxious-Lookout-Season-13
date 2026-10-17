"use client";

import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { getPostDraft } from "@/lib/posts/api";
import type { PostDraft } from "@/lib/posts/types";
import { useAddressedDraft, type DraftLookup } from "@/lib/research/useAddressedDraft";
import AuthGate from "@/components/research/AuthGate";
import Notice from "@/components/research/Notice";
import PostEditor from "@/components/posts/PostEditor";
import { NewPostProposals, ProposalReview } from "@/components/posts/PostProposals";

// #28 새 글: 입력은 서버 초안(postId 없음)으로 자동 저장되고 주소(?draft=)로 복구된다. ‘글 저장’만 글과 첫 버전을 만든다.

const NEW_PATH = "/write/new/";

async function lookup(id: string): Promise<DraftLookup<PostDraft>> {
  const r = await getPostDraft(id);
  if (r.ok && r.data.state === "active" && r.data.postId === null) return { kind: "active", draft: r.data };
  if (r.ok && r.data.state === "committed" && r.data.postId) return { kind: "committed", href: `/write/?id=${encodeURIComponent(r.data.postId)}` };
  return { kind: "missing" };
}

// 새 글 제안 검토(?proposal=). 수락하면 그 글로, 버리면 내 글 목록으로 간다.
function ReviewNewProposal({ id }: { id: string }) {
  const router = useRouter();
  return (
    <section className="space-y-3">
      <p className="text-sm text-stone-600">Codex가 대화에서 제안한 새 글입니다. 비교해 보고 수락하거나 버리세요.</p>
      <ProposalReview
        summary={{ id, version: 0 }}
        target={{ kind: "new" }}
        onDone={(post) => (post ? router.push(`/write/?id=${encodeURIComponent(post.id)}`) : router.replace("/write/"))}
      />
    </section>
  );
}

function NewPost() {
  const search = useSearchParams();
  const proposal = search.get("proposal");
  if (proposal) return <ReviewNewProposal key={proposal} id={proposal} />;
  return <NewPostEditor param={search.get("draft")} />;
}

function NewPostEditor({ param }: { param: string | null }) {
  const router = useRouter();
  const { view, onDraft, onDiscarded } = useAddressedDraft({ param, newPath: NEW_PATH, lookup });
  if (!view) return <p className="text-sm text-stone-500">초안을 불러오는 중...</p>;
  const key = view.key;
  return (
    <div className="space-y-4">
      {view.note && <Notice tone="info">{view.note}</Notice>}
      <PostEditor
        key={key}
        post={null}
        draft={view.draft}
        onDraft={(d) => onDraft(key, d)}
        onCommitted={(post) => router.push(`/write/?id=${encodeURIComponent(post.id)}`)}
        onDiscarded={() => onDiscarded("초안을 버렸습니다.")}
      />
      {view.bound && <NewPostProposals baseDraftId={view.bound} reload={key} />}
    </div>
  );
}

export default function NewPostPage() {
  return (
    <div className="mx-auto max-w-6xl">
      <Link href="/write/" className="text-sm text-stone-500 hover:text-stone-800">
        ← 내 글
      </Link>
      <h1 className="mb-6 mt-2 text-2xl font-bold text-stone-900">새 글</h1>
      <AuthGate>
        <Suspense>
          <NewPost />
        </Suspense>
      </AuthGate>
    </div>
  );
}
