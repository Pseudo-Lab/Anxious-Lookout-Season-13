"use client";

import { Fragment, type CSSProperties, type ReactNode } from "react";
import { parseBlockDocument } from "@/lib/blocks/document";
import { clampImageWidth, ptToPx, type Align, type Block, type FlowBlock, type ListBlock, type Mark, type Run, type TableBlock } from "@/lib/blocks/types";
import { CELL_BG_CLASS, CELL_TEXT_COLOR_CLASS, cellBackgroundStyle, cellTextColorVar, textColorStyle, TEXT_COLOR_CLASS } from "@/lib/blocks/userColor";
import { ownImageUrl } from "@/lib/posts/api";
import ScrollBox from "@/components/research/ScrollBox";
import YouTubeCard from "./YouTubeCard";

// #32 공개·미리보기 renderer(post-blocks v1). 서버가 준 문서도 같은 검사(parseBlockDocument)를 거친 뒤에만 그린다.
// - 모든 글자는 React 텍스트 노드다(HTML 문자열을 넣지 않는다). run의 LF는 <br>.
// - DOM 속성은 검증된 값으로 직접 만든다: 색·배경은 #RRGGBB, 정렬은 enum, 이미지 주소는 imageId로 만든 자체 주소뿐.
// - 링크는 계약 정책을 통과한 값만 있고, 외부 링크는 새 창 + noopener noreferrer nofollow ugc.
// - YouTube는 누르기 전에는 외부 요청이 없는 카드다.
// - #36: 글자 크기는 pt*4/3 px(10..36pt로 제한), 이미지 폭은 부모 폭의 10..100%, 표 열 폭 가중치는 고정 표 배치의 열 %.
// - 검사에 실패하거나 모르는 schemaVersion이면 내용을 그리지 않고 오류를 보인다.

const TEXT_ALIGN: Record<Align, CSSProperties["textAlign"]> = { left: "left", center: "center", right: "right" };
const FIGURE_ALIGN: Record<Align, string> = { left: "mr-auto", center: "mx-auto", right: "ml-auto" };

function lines(text: string) {
  return text.split("\n").map((part, i) => (
    <Fragment key={i}>
      {i > 0 && <br />}
      {part}
    </Fragment>
  ));
}

function wrap(node: ReactNode, mark: Mark, key: number, cellBg?: string): ReactNode {
  if (typeof mark !== "string") {
    // #38 D-38-1: 글자색은 다크 모드에서 밝기만 올린 색으로 그린다(userColor). 배경 칸 안에서는 그 배경 위 AA 값.
    if ("color" in mark)
      return cellBg ? (
        <span key={key} className={`${TEXT_COLOR_CLASS} ${CELL_TEXT_COLOR_CLASS}`} style={{ ...textColorStyle(mark.color), "--pb-cell-cd": cellTextColorVar(mark.color, cellBg) } as CSSProperties}>
          {node}
        </span>
      ) : (
        <span key={key} className={TEXT_COLOR_CLASS} style={textColorStyle(mark.color)}>
          {node}
        </span>
      );
    return (
      <span key={key} style={{ fontSize: `${ptToPx(mark.fontSize)}px` }}>
        {node}
      </span>
    );
  }
  switch (mark) {
    case "bold":
      return <strong key={key}>{node}</strong>;
    case "italic":
      return <em key={key}>{node}</em>;
    case "underline":
      return <u key={key}>{node}</u>;
    case "strike":
      return <s key={key}>{node}</s>;
    case "code":
      return <code key={key}>{node}</code>;
  }
}

function Runs({ runs, cellBg }: { runs: Run[]; cellBg?: string }) {
  return (
    <>
      {runs.map((run, i) => {
        // 바깥부터 bold → … → color → fontSize 순으로 감싼다(mark 순서는 canonical로 고정)
        let node: ReactNode = lines(run.text);
        for (let m = run.marks.length - 1; m >= 0; m--) node = wrap(node, run.marks[m], m, cellBg);
        if (run.link) {
          const external = !run.link.startsWith("#");
          node = external ? (
            <a href={run.link} target="_blank" rel="noopener noreferrer nofollow ugc">
              {node}
            </a>
          ) : (
            <a href={run.link}>{node}</a>
          );
        }
        return <Fragment key={i}>{node}</Fragment>;
      })}
    </>
  );
}

function List({ list }: { list: ListBlock }) {
  const items = list.items.map((item) => (
    <li key={item.id}>
      <p>
        <Runs runs={item.blocks[0].runs} />
      </p>
      {item.blocks[1] && <List list={item.blocks[1]} />}
    </li>
  ));
  return list.ordered ? <ol start={list.start}>{items}</ol> : <ul>{items}</ul>;
}

const HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"] as const;

/** 열 폭 가중치 → 고정 배치의 열 %(100*w/합). 가중치가 없는 표는 지금까지처럼 내용에 맞춘 배치를 쓴다. */
export const columnPercents = (weights: number[]) => {
  const sum = weights.reduce((a, b) => a + b, 0);
  return weights.map((w) => (100 * w) / sum);
};
function ColumnWidths({ table }: { table: TableBlock }) {
  if (!table.columnWidths) return null;
  return (
    <colgroup>
      {columnPercents(table.columnWidths).map((p, i) => (
        <col key={i} style={{ width: `${p}%` }} />
      ))}
    </colgroup>
  );
}

function BlockView({ block }: { block: Block }) {
  switch (block.type) {
    case "paragraph":
      return (
        <p style={{ textAlign: TEXT_ALIGN[block.align ?? "left"] }}>
          <Runs runs={block.runs} />
        </p>
      );
    case "heading": {
      const H = HEADINGS[block.level - 1];
      return (
        <H style={{ textAlign: TEXT_ALIGN[block.align] }}>
          <Runs runs={block.runs} />
        </H>
      );
    }
    case "quote":
      return (
        <blockquote>
          {block.blocks.map((b) => (
            <BlockView key={b.id} block={b} />
          ))}
        </blockquote>
      );
    case "list":
      return <List list={block} />;
    case "divider":
      return <hr />;
    case "code":
      return (
        <ScrollBox as="pre" label={`코드 (${block.language})`}>
          <code data-language={block.language}>{block.text}</code>
        </ScrollBox>
      );
    case "image":
      return (
        <figure className={`my-6 ${FIGURE_ALIGN[block.align]}`} style={{ width: `${clampImageWidth(block.widthPercent)}%`, maxWidth: "100%" }}>
          {/* eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음) */}
          <img src={ownImageUrl(block.imageId)} alt={block.alt} loading="lazy" className="w-full rounded-lg ring-1 ring-stone-200" />
          {block.caption.length > 0 && (
            <figcaption className="mt-1 text-center text-sm text-stone-500">
              <Runs runs={block.caption} />
            </figcaption>
          )}
        </figure>
      );
    case "embed":
      return (
        <figure className="my-6">
          <YouTubeCard resourceId={block.resourceId} />
          {block.caption.length > 0 && (
            <figcaption className="mt-1 text-center text-sm text-stone-500">
              <Runs runs={block.caption} />
            </figcaption>
          )}
        </figure>
      );
    case "table":
      return (
        <ScrollBox label="표" className="table-scroll">
          <table className={block.columnWidths ? "pb-weighted" : undefined}>
            <ColumnWidths table={block} />
            <tbody>
              {block.rows.map((row) => (
                <tr key={row.id}>
                  {row.cells.map((cell) => {
                    const Cell = row.header ? "th" : "td";
                    return (
                      <Cell
                        key={cell.id}
                        className={cell.background ? CELL_BG_CLASS : undefined}
                        style={{ textAlign: TEXT_ALIGN[cell.align], ...(cell.background ? cellBackgroundStyle(cell.background) : {}) }}
                      >
                        <span className="cell">
                          {cell.blocks.map((p) => (
                            <p key={p.id} className="my-0">
                              <Runs runs={p.runs} cellBg={cell.background} />
                            </p>
                          ))}
                        </span>
                      </Cell>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </ScrollBox>
      );
    case "columns":
      return (
        <div className="pb-columns">
          {block.columns.map((col) => (
            <div key={col.id} className="pb-column" style={{ flexGrow: col.width }}>
              {col.blocks.map((b: FlowBlock) => (
                <BlockView key={b.id} block={b} />
              ))}
            </div>
          ))}
        </div>
      );
  }
}

/** 블록 문서 본문. content는 서버가 준 그대로(검사 전) 넘긴다. */
export default function BlockRenderer({ content }: { content: unknown }) {
  const parsed = parseBlockDocument(content);
  if (!parsed.ok) {
    return (
      <p role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-800 ring-1 ring-red-200">
        {parsed.error.code === "unsupported_schema_version"
          ? "이 글은 지금 화면이 알지 못하는 형식으로 저장되어 있어 표시하지 않았습니다. 새로고침해 보세요."
          : "본문 형식이 올바르지 않아 표시하지 않았습니다."}
      </p>
    );
  }
  return (
    <div className="prose max-w-none">
      {parsed.doc.blocks.map((b) => (
        <BlockView key={b.id} block={b} />
      ))}
    </div>
  );
}
