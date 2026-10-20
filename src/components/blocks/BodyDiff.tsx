"use client";

import { formatChanges as localChanges, formattingChanges, textProjection, type FormatChange } from "@/lib/blocks/projection";
import type { BlockDocument } from "@/lib/blocks/types";
import TextDiff from "@/components/research/TextDiff";

// #32 본문 비교: 글자는 plain projection의 줄 비교로, 서식·배치·이동은 formatChanges로 따로 보인다.
// 글자가 같아도 서식이 바뀌었으면 "변경 없음"으로 보이지 않게 한다(계약 §5).
// changes를 주면(서버 comparison 결과) 그것을 쓰고, 없으면 같은 규칙으로 화면에서 계산한다.

const KIND_TEXT: Record<FormatChange["kind"], string> = {
  "format-only": "서식만",
  layout: "배치",
  moved: "순서 이동",
  added: "추가",
  removed: "삭제",
  ambiguous: "대응 모호",
};

const PROP_TEXT: Record<string, string> = {
  runs: "글자·글자 서식(크기 포함)·링크",
  align: "정렬",
  level: "제목 수준",
  language: "코드 언어",
  text: "코드 내용",
  alt: "대체 텍스트",
  caption: "설명",
  widthPercent: "이미지 폭",
  imageId: "이미지",
  resourceId: "영상",
  provider: "영상 제공자",
  background: "칸 배경",
  header: "머리글 행",
  width: "단 비율",
  columnWidths: "표 열 폭",
  ordered: "목록 종류",
  start: "번호 시작",
  type: "블록 종류",
  parent: "위치(다른 묶음으로)",
  position: "순서",
  structure: "구조",
};

export default function BodyDiff({
  before,
  after,
  beforeTitle,
  afterTitle,
  changes,
}: {
  before: BlockDocument;
  after: BlockDocument;
  beforeTitle?: string;
  afterTitle?: string;
  changes?: FormatChange[];
}) {
  const list = changes ?? localChanges(before, after);
  const beforeText = textProjection(before);
  const afterText = textProjection(after);
  // 글자만 바뀐 곳은 줄 비교에 보이므로 빼고, 서식·링크·배치·구조 변화는 글자 변화가 함께 있어도 남긴다
  const formatting = formattingChanges(before, after, list);
  return (
    <div className="space-y-2">
      <TextDiff before={beforeText} after={afterText} beforeTitle={beforeTitle} afterTitle={afterTitle} />
      {formatting.length > 0 && (
        <section aria-label="서식·배치 변경" className="rounded-lg bg-amber-50 p-3 text-xs text-amber-900 ring-1 ring-amber-200">
          <p className="font-medium">
            {beforeText === afterText ? "글자는 같지만 " : ""}서식·배치가 바뀐 곳 {formatting.length}곳
          </p>
          <ul className="mt-1 space-y-0.5">
            {formatting.slice(0, 30).map((c, i) => (
              <li key={i}>
                {KIND_TEXT[c.kind]}: {c.changedProperties.map((p) => PROP_TEXT[p] ?? p).join(", ")}
              </li>
            ))}
            {formatting.length > 30 && <li>그 밖에 {formatting.length - 30}곳</li>}
          </ul>
        </section>
      )}
    </div>
  );
}
