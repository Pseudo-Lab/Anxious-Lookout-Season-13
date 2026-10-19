"use client";

import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import type { Editor } from "@tiptap/react";
import { fromEditor, toEditor, type EditorNode } from "@/lib/blocks/adapter";
import type { BlockError } from "@/lib/blocks/document";
import type { BlockDocument } from "@/lib/blocks/types";

// #32 글 본문 편집기: post-blocks 문서 <-> 블록 편집기. 편집기는 작성 화면에서만 client-only로 불러온다.
// - 편집 결과는 계약 검사(fromEditor)를 통과한 canonical 문서만 바깥에 알린다. 통과하지 못하면(상한 초과 등)
//   마지막으로 통과한 내용을 그대로 두고 이유를 보이며, onProblem으로 바깥에도 알린다. 화면의 입력이 저장된 본문과
//   다르므로 바깥은 고칠 때까지 저장 완료(새 버전·보관 닫기)를 막아야 한다(리뷰 M1).
// - 서버 초안 불러오기·이전 버전 되돌리기처럼 바깥에서 본문을 바꾸면 편집기를 그 내용으로 다시 만든다.
//   편집기가 낸 문서 객체를 기억해 두고, 그와 다른 객체가 들어오면 바깥 변경으로 본다(자동 저장은 객체를 바꾸지 않는다).

const BlockEditor = dynamic(() => import("./editor/BlockEditor"), {
  ssr: false,
  loading: () => <p className="text-sm text-stone-500">편집기를 불러오는 중...</p>,
});

const PROBLEM_TEXT: Record<string, string> = {
  content_limit_exceeded: "본문이 저장할 수 있는 크기·개수 상한을 넘었습니다",
  payload_too_large: "본문이 1 MiB를 넘었습니다",
  invalid_block_document: "저장할 수 없는 본문 구조가 있습니다",
};

export function blockProblemText(e: BlockError) {
  return `${PROBLEM_TEXT[e.code] ?? "저장할 수 없는 본문입니다"}(${e.path || "문서 전체"}). 고칠 때까지 마지막으로 저장 가능한 본문을 유지합니다.`;
}

export default function PostBodyEditor({
  value,
  onChange,
  onEditor,
  onUploading,
  onProblem,
  readOnly,
}: {
  value: BlockDocument;
  onChange: (doc: BlockDocument) => void;
  // 편집기가 준비되면(이미지 패널의 "본문에 넣기"가 쓴다)
  onEditor?: (editor: Editor) => void;
  onUploading?: (uploading: boolean) => void;
  // 저장할 수 없는 편집 상태가 생기고(오류) 풀릴 때(null)
  onProblem?: (problem: BlockError | null) => void;
  readOnly?: boolean;
}) {
  // init: 편집기를 만들 때 넣은 내용(편집기 JSON). 편집 중에는 바꾸지 않는다.
  const [shown, setShown] = useState(() => ({ key: 0, doc: value, init: toEditor(value) }));
  const [problem, setProblem] = useState<BlockError | null>(null);
  const onProblemRef = useRef(onProblem);
  useEffect(() => {
    onProblemRef.current = onProblem;
  });
  useEffect(() => {
    onProblemRef.current?.(problem);
  }, [problem]);
  // 편집기가 사라질 때(편집 닫기·버리기) 바깥의 잠금을 남기지 않는다
  useEffect(() => () => onProblemRef.current?.(null), []);
  // 바깥에서 본문이 바뀌었으면 편집기를 새로 만든다(렌더 중 상태 조정)
  if (value !== shown.doc) {
    setShown({ key: shown.key + 1, doc: value, init: toEditor(value) });
    if (problem) setProblem(null);
  }
  return (
    <div className="space-y-1">
      <span className="block text-sm font-medium text-stone-700">본문</span>
      <BlockEditor
        key={shown.key}
        initialContent={shown.init}
        readOnly={readOnly}
        onUploading={onUploading}
        onReady={onEditor}
        onChange={(json) => {
          const r = fromEditor(json as EditorNode);
          if (!r.ok) {
            setProblem(r.error);
            return;
          }
          setProblem(null);
          setShown((s) => ({ ...s, doc: r.doc }));
          onChange(r.doc);
        }}
      />
      {problem && (
        <p role="alert" className="text-xs text-amber-800">
          {blockProblemText(problem)}
        </p>
      )}
    </div>
  );
}
