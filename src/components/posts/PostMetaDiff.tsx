"use client";

import type { PostText } from "@/lib/posts/types";
import DocTitle from "./DocTitle";

type Meta = Pick<PostText, "description" | "slug" | "tags" | "coverImageId" | "documentIds">;

const listDiff = (before: string[], after: string[]) => ({
  added: after.filter((x) => !before.includes(x)),
  removed: before.filter((x) => !after.includes(x)),
  reordered: before.length === after.length && before.every((x) => after.includes(x)) && before.some((x, i) => after[i] !== x),
});

/** 본문 외 글 메타(요약·주소·태그·표지·연결 문서)의 변경. 바뀐 것이 없으면 아무것도 그리지 않는다. 제목·본문은 TextDiff가 보인다. */
export default function PostMetaDiff({ before, after }: { before: Meta; after: Meta }) {
  const rows: { label: string; node: React.ReactNode }[] = [];
  if (before.description !== after.description) {
    rows.push({
      label: "요약",
      node: (
        <>
          <del className="bg-rose-100 text-rose-900">{before.description || "(없음)"}</del> →{" "}
          <ins className="bg-emerald-100 text-emerald-900 no-underline">{after.description || "(없음)"}</ins>
        </>
      ),
    });
  }
  if (before.slug !== after.slug) {
    rows.push({
      label: "주소",
      node: (
        <>
          <del className="bg-rose-100 font-mono text-rose-900">{before.slug || "(없음)"}</del> →{" "}
          <ins className="bg-emerald-100 font-mono text-emerald-900 no-underline">{after.slug || "(없음)"}</ins>
        </>
      ),
    });
  }
  const tags = listDiff(before.tags, after.tags);
  if (tags.added.length || tags.removed.length || tags.reordered) {
    rows.push({
      label: "태그",
      node: (
        <>
          {tags.added.map((t) => (
            <ins key={`+${t}`} className="mr-1 rounded bg-emerald-100 px-1 text-emerald-900 no-underline">
              +{t}
            </ins>
          ))}
          {tags.removed.map((t) => (
            <del key={`-${t}`} className="mr-1 rounded bg-rose-100 px-1 text-rose-900">
              −{t}
            </del>
          ))}
          {tags.reordered && <span className="text-stone-500">순서만 바뀜</span>}
        </>
      ),
    });
  }
  if (before.coverImageId !== after.coverImageId) {
    rows.push({ label: "표지", node: after.coverImageId ? (before.coverImageId ? "다른 이미지로 바뀜" : "새로 지정") : "표지 없앰" });
  }
  const docs = listDiff(before.documentIds, after.documentIds);
  if (docs.added.length || docs.removed.length || docs.reordered) {
    rows.push({
      label: "연결 문서",
      node: (
        <span className="flex flex-col gap-0.5">
          {docs.added.map((id) => (
            <ins key={`+${id}`} className="bg-emerald-50 text-emerald-900 no-underline">
              + <DocTitle id={id} />
            </ins>
          ))}
          {docs.removed.map((id) => (
            <del key={`-${id}`} className="bg-rose-50 text-rose-900">
              − <DocTitle id={id} />
            </del>
          ))}
          {docs.reordered && <span className="text-stone-500">순서만 바뀜</span>}
        </span>
      ),
    });
  }
  if (!rows.length) return null;
  return (
    <dl aria-label="글 정보 변경" className="space-y-1 rounded-lg bg-stone-50 p-3 text-sm">
      {rows.map((r) => (
        <div key={r.label} className="flex gap-2">
          <dt className="w-16 shrink-0 text-stone-500">{r.label}</dt>
          <dd className="min-w-0 break-words">{r.node}</dd>
        </div>
      ))}
    </dl>
  );
}
