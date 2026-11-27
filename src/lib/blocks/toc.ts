import type { Block, BlockDocument, HeadingBlock } from "./types";

// #119 F1: 글 상세 목차. 본문 맨 위 단계와 단(columns) 안의 소제목만 모은다(인용·표 안에는 소제목이 없다).

export interface OutlineEntry {
  id: string;
  level: HeadingBlock["level"];
  text: string;
}

/** 목차를 보일 최소 소제목 수 */
export const TOC_MIN_HEADINGS = 3;

/** renderer가 소제목에 붙이는 id(같은 문서 안 블록 id는 서로 다르다). */
export const headingAnchor = (blockId: string) => `h-${blockId}`;

function collect(blocks: readonly Block[], out: OutlineEntry[]) {
  for (const b of blocks) {
    if (b.type === "heading") {
      const text = b.runs.map((r) => r.text).join("").replace(/\s+/g, " ").trim();
      if (text) out.push({ id: b.id, level: b.level, text });
    } else if (b.type === "columns") {
      for (const c of b.columns) collect(c.blocks, out);
    }
  }
}

export function outline(doc: BlockDocument): OutlineEntry[] {
  const out: OutlineEntry[] = [];
  collect(doc.blocks, out);
  return out;
}
