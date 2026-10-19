// #32 고정 블록 문서(post-blocks v1) 저장·렌더 경계 타입(api-contracts/issue32-blocks-draft.md v0.2).
// 편집기 내부 모델(Tiptap JSON)은 저장하지 않는다. 편집기 <-> 이 형식 변환은 adapter가 맡는다.

import { uuidV4 } from "@/lib/research/idempotency";

export const BLOCKS_FORMAT = "post-blocks";
export const BLOCKS_VERSION = 1;

export type Align = "left" | "center" | "right";

/** mark 순서는 이 배열 순서로 고정한다(color는 마지막). */
export const SIMPLE_MARKS = ["bold", "italic", "underline", "strike", "code"] as const;
export type SimpleMark = (typeof SIMPLE_MARKS)[number];
export type Mark = SimpleMark | { color: string };

/** run.text의 LF는 문단 안 줄바꿈(renderer br)이다. 빈 text는 허용하지 않는다. */
export interface Run {
  text: string;
  marks: Mark[];
  link?: string;
}

export const CODE_LANGUAGES = [
  "plain", "bash", "c", "cpp", "css", "go", "html", "java", "javascript", "json", "jsx",
  "markdown", "python", "rust", "sql", "tsx", "typescript", "xml", "yaml",
] as const;
export type CodeLanguage = (typeof CODE_LANGUAGES)[number];

export const IMAGE_WIDTHS = [25, 50, 75, 100] as const;
export type ImageWidth = (typeof IMAGE_WIDTHS)[number];

export const EMBED_PROVIDERS = ["youtube"] as const;
export type EmbedProvider = (typeof EMBED_PROVIDERS)[number];
export const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;

interface Base {
  id: string;
}

export interface ParagraphBlock extends Base {
  type: "paragraph";
  runs: Run[];
  // 표 셀 안 paragraph는 align을 갖지 않는다(cell.align만 쓴다)
  align?: Align;
}
export interface HeadingBlock extends Base {
  type: "heading";
  level: 1 | 2 | 3 | 4 | 5 | 6;
  runs: Run[];
  align: Align;
}
export interface ListItem extends Base {
  // 정확히 [paragraph, list?]
  blocks: [ParagraphBlock] | [ParagraphBlock, ListBlock];
}
export interface ListBlock extends Base {
  type: "list";
  ordered: boolean;
  start?: number;
  items: ListItem[];
}
export interface QuoteBlock extends Base {
  type: "quote";
  blocks: (ParagraphBlock | ListBlock)[];
}
export interface DividerBlock extends Base {
  type: "divider";
}
export interface CodeBlock extends Base {
  type: "code";
  text: string;
  language: CodeLanguage;
}
export interface ImageBlock extends Base {
  type: "image";
  imageId: string;
  alt: string;
  caption: Run[];
  widthPercent: ImageWidth;
  align: Align;
}
export interface TableCell extends Base {
  blocks: ParagraphBlock[];
  align: Align;
  background?: string;
}
export interface TableRow extends Base {
  header: boolean;
  cells: TableCell[];
}
export interface TableBlock extends Base {
  type: "table";
  rows: TableRow[];
}
export interface Column extends Base {
  width: 1 | 2 | 3 | 4;
  blocks: FlowBlock[];
}
export interface ColumnsBlock extends Base {
  type: "columns";
  columns: Column[];
}
export interface EmbedBlock extends Base {
  type: "embed";
  provider: EmbedProvider;
  resourceId: string;
  caption: Run[];
}

/** columns 안에 올 수 있는 블록(columns·table 제외). */
export type FlowBlock = ParagraphBlock | HeadingBlock | ListBlock | QuoteBlock | DividerBlock | CodeBlock | ImageBlock | EmbedBlock;
export type Block = FlowBlock | TableBlock | ColumnsBlock;

export interface BlockDocument {
  format: typeof BLOCKS_FORMAT;
  version: typeof BLOCKS_VERSION;
  blocks: Block[];
}

/** 빈 문서: 빈 paragraph 하나(id는 새로 만든다). */
export const emptyDocument = (): BlockDocument => ({
  format: BLOCKS_FORMAT,
  version: BLOCKS_VERSION,
  blocks: [{ id: uuidV4(), type: "paragraph", runs: [], align: "left" }],
});

/** 계약 §3 상한(착수 첫 inventory 후 재조정 예정). front 사전 검사와 서버가 같은 값을 쓴다. */
export const LIMITS = {
  listDepth: 3,
  columnsMin: 2,
  columnsMax: 3,
  tableRows: 100,
  tableCols: 20,
  totalIds: 2000,
} as const;
