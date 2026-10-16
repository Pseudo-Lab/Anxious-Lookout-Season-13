"use client";

import { useMemo, useState } from "react";
import { foldContext, lineDiff, type Part, type Row } from "@/lib/research/textDiff";

// 본문 원문(Markdown)의 줄 단위 비교. 렌더하지 않고 텍스트로만 보인다(SafeMarkdown 규칙과 무관하게 실행되는 것이 없다).

function Text({ text, parts, mark }: { text: string; parts?: Part[]; mark: "ins" | "del" | null }) {
  if (!parts || !mark) return <>{text || " "}</>;
  const Tag = mark;
  const strong = mark === "ins" ? "bg-emerald-200" : "bg-rose-200";
  return (
    <>
      {parts.map((p, i) =>
        p.changed ? (
          <Tag key={i} className={`${strong} rounded-sm no-underline`}>
            {p.text}
          </Tag>
        ) : (
          <span key={i}>{p.text}</span>
        )
      )}
    </>
  );
}

function Line({ row }: { row: Row }) {
  const tone = row.kind === "add" ? "bg-emerald-50" : row.kind === "del" ? "bg-rose-50" : "";
  const sign = row.kind === "add" ? "+" : row.kind === "del" ? "−" : " ";
  const label = row.kind === "add" ? "추가" : row.kind === "del" ? "삭제" : null;
  const parts = row.kind === "context" ? undefined : row.parts;
  return (
    <div data-diff={row.kind} className={`grid grid-cols-[2.5rem_2.5rem_1rem_minmax(0,1fr)] ${tone}`}>
      <span className="select-none pr-1 text-right text-stone-400">{row.kind !== "add" ? row.oldNo : ""}</span>
      <span className="select-none pr-1 text-right text-stone-400">{row.kind !== "del" ? row.newNo : ""}</span>
      <span aria-hidden="true" className="select-none text-center text-stone-500">
        {sign}
      </span>
      <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">
        {label && <span className="sr-only">{label}: </span>}
        <Text text={row.text} parts={parts} mark={row.kind === "add" ? "ins" : row.kind === "del" ? "del" : null} />
      </span>
    </div>
  );
}

/** 두 본문의 GitHub 스타일 비교. before가 기준(이전), after가 비교 대상(이후). */
export default function TextDiff({
  before,
  after,
  beforeTitle,
  afterTitle,
}: {
  before: string;
  after: string;
  beforeTitle?: string;
  afterTitle?: string;
}) {
  const diff = useMemo(() => lineDiff(before, after), [before, after]);
  const blocks = useMemo(() => (diff ? foldContext(diff.rows) : []), [diff]);
  const [opened, setOpened] = useState<Set<number>>(new Set());

  const titleChanged = beforeTitle !== undefined && afterTitle !== undefined && beforeTitle !== afterTitle;
  return (
    <div className="space-y-2">
      {titleChanged && (
        <p className="text-sm">
          <span className="text-stone-500">제목: </span>
          <del className="bg-rose-100 text-rose-900">{beforeTitle}</del> → <ins className="bg-emerald-100 text-emerald-900 no-underline">{afterTitle}</ins>
        </p>
      )}
      {!diff ? (
        <p role="status" className="text-sm text-amber-800">
          두 본문의 차이가 너무 커서 줄 단위로 비교하지 못했습니다. 각 버전을 펼쳐 직접 확인하세요.
        </p>
      ) : diff.added === 0 && diff.removed === 0 ? (
        <p role="status" className="text-sm text-stone-600">
          본문이 같습니다{titleChanged ? "(제목만 다릅니다)" : ""}.
        </p>
      ) : (
        <>
          <p className="text-xs text-stone-600">
            <span className="font-medium text-emerald-700">+{diff.added}줄</span>{" "}
            <span className="font-medium text-rose-700">−{diff.removed}줄</span>
          </p>
          <div className="overflow-hidden rounded-lg font-mono text-xs leading-5 text-stone-800 ring-1 ring-stone-200">
            {blocks.map((b, i) =>
              b.kind === "rows" || opened.has(b.id) ? (
                <div key={i}>
                  {b.rows.map((r, k) => (
                    <Line key={k} row={r} />
                  ))}
                </div>
              ) : (
                <button
                  key={i}
                  type="button"
                  onClick={() => setOpened(new Set(opened).add(b.id))}
                  className="block w-full bg-stone-100 px-3 py-1 text-left font-sans text-xs text-indigo-700 hover:bg-stone-200"
                >
                  같은 줄 {b.rows.length}개 펼치기
                </button>
              )
            )}
          </div>
        </>
      )}
    </div>
  );
}
