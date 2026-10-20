"use client";

import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { IMAGE_WIDTH, clampImageWidth, type Align } from "@/lib/blocks/types";
import { ownImageUrl } from "@/lib/posts/api";
import YouTubeCard from "@/components/blocks/YouTubeCard";
import { Embed, PostImage } from "./schema";

// #32 편집기 안 이미지·영상 블록. 설정(alt·폭·정렬)은 블록 위 작은 입력으로 바꾸고 caption은 그 아래에서 바로 쓴다.
// #36 F3: 이미지 폭은 가장자리 손잡이를 끌거나 % 칸에 적어 바꾼다. 끄는 동안은 화면만 바꾸고 놓을 때 한 번 저장한다
// (끄는 중 매 움직임이 실행 취소 단계·자동 저장이 되지 않게). 값은 부모(본문·단) 폭에 대한 정수 %, 10..100.

const ALIGN_LABEL: Record<Align, string> = { left: "왼쪽", center: "가운데", right: "오른쪽" };
const FIGURE_ALIGN: Record<Align, string> = { left: "mr-auto", center: "mx-auto", right: "ml-auto" };

function Caption({ placeholder }: { placeholder: string }) {
  return (
    <NodeViewContent<"figcaption">
      as="figcaption"
      data-placeholder={placeholder}
      className="pb-caption mt-1 min-h-[1.5em] text-center text-sm text-stone-500"
    />
  );
}

/** 폭 % 입력(키보드·모바일). 입력 중에는 그대로 두고 확정(Enter·포커스 이동) 때 정수로 반올림·제한해 저장한다. */
function WidthInput({ value, onCommit }: { value: number; onCommit: (w: number) => void }) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const n = Number(text);
    setText(null);
    if (text.trim() !== "" && Number.isFinite(n)) onCommit(clampImageWidth(n));
  };
  return (
    <label className="flex items-center gap-1">
      <span>폭</span>
      <input
        type="number"
        inputMode="numeric"
        aria-label="이미지 폭(%)"
        min={IMAGE_WIDTH.min}
        max={IMAGE_WIDTH.max}
        step={1}
        value={text ?? String(value)}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          }
        }}
        className="w-16 rounded border border-stone-300 px-1 py-0.5"
      />
      <span>%</span>
    </label>
  );
}

/** #36 F4: 블록을 같은 묶음(본문·단) 안에서 위/아래로 한 칸 옮긴다. 끌어 옮기기(drag)는 그대로 쓸 수 있다. */
function MoveButtons({ editor, getPos, what }: Pick<ReactNodeViewProps, "editor" | "getPos"> & { what: string }) {
  const move = (dir: -1 | 1) => {
    const pos = getPos();
    if (typeof pos === "number") editor.chain().focus().moveBlockAt(pos, dir).run();
  };
  return (
    <span className="flex items-center gap-0.5">
      <button type="button" aria-label={`${what} 위로`} title={`${what} 위로`} onMouseDown={(e) => e.preventDefault()} onClick={() => move(-1)} className="rounded border border-stone-300 px-1.5 py-0.5 hover:bg-stone-100">
        ↑
      </button>
      <button type="button" aria-label={`${what} 아래로`} title={`${what} 아래로`} onMouseDown={(e) => e.preventDefault()} onClick={() => move(1)} className="rounded border border-stone-300 px-1.5 py-0.5 hover:bg-stone-100">
        ↓
      </button>
    </span>
  );
}

function ImageView({ node, updateAttributes, selected, editor, getPos }: ReactNodeViewProps) {
  const { imageId, alt, widthPercent, align } = node.attrs as { imageId: string; alt: string; widthPercent: number; align: Align };
  const editable = editor.isEditable;
  const figure = useRef<HTMLElement | null>(null);
  // 끄는 중의 폭(놓기 전까지 문서에는 쓰지 않는다)
  const [dragging, setDragging] = useState<number | null>(null);
  const shown = dragging ?? clampImageWidth(widthPercent);

  function startResize(e: ReactPointerEvent<HTMLSpanElement>) {
    const el = figure.current;
    const box = el?.parentElement;
    if (!el || !box) return;
    e.preventDefault();
    const handle = e.currentTarget;
    handle.setPointerCapture(e.pointerId);
    const startX = e.clientX;
    const startWidth = el.getBoundingClientRect().width;
    const full = box.getBoundingClientRect().width || 1;
    // 가운데 정렬은 양쪽으로 커지므로 움직인 거리의 두 배, 오른쪽 정렬은 왼쪽 손잡이라 반대 방향이다
    const factor = align === "center" ? 2 : align === "right" ? -1 : 1;
    let last = clampImageWidth(widthPercent);
    const move = (ev: PointerEvent) => {
      last = clampImageWidth(((startWidth + (ev.clientX - startX) * factor) / full) * 100);
      setDragging(last);
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      setDragging(null);
      if (last !== widthPercent) updateAttributes({ widthPercent: last });
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  }

  return (
    <NodeViewWrapper
      as="figure"
      ref={figure}
      data-type="post-image"
      className={`relative my-4 max-w-full ${FIGURE_ALIGN[align]} ${selected ? "ring-2 ring-indigo-400" : ""}`}
      style={{ width: `${shown}%` }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음) */}
      <img src={ownImageUrl(imageId)} alt={alt} className="w-full rounded-lg ring-1 ring-stone-200" draggable={false} data-drag-handle />
      {editable && (
        <span
          contentEditable={false}
          role="presentation"
          title="끌어서 폭 바꾸기"
          data-testid="image-resize"
          onPointerDown={startResize}
          className={`absolute top-1/2 h-10 w-2.5 -translate-y-1/2 cursor-ew-resize touch-none rounded-full bg-indigo-500/80 ring-2 ring-white ${align === "right" ? "-left-1" : "-right-1"}`}
        />
      )}
      {editable && (
        <div contentEditable={false} className="mt-1 flex flex-wrap items-center gap-2 text-xs text-stone-900">
          <label className="flex flex-1 items-center gap-1">
            <span>대체 텍스트</span>
            <input
              value={alt}
              maxLength={1000}
              onChange={(e) => updateAttributes({ alt: e.target.value.replace(/[\r\n]+/g, " ") })}
              className="min-w-0 flex-1 rounded border border-stone-300 px-1.5 py-0.5"
            />
          </label>
          <WidthInput value={shown} onCommit={(w) => w !== widthPercent && updateAttributes({ widthPercent: w })} />
          <MoveButtons editor={editor} getPos={getPos} what="이미지" />
          <select aria-label="이미지 정렬" value={align} onChange={(e) => updateAttributes({ align: e.target.value })} className="rounded border border-stone-300 px-1 py-0.5">
            {(Object.keys(ALIGN_LABEL) as Align[]).map((a) => (
              <option key={a} value={a}>
                {ALIGN_LABEL[a]}
              </option>
            ))}
          </select>
        </div>
      )}
      <Caption placeholder="이미지 설명(선택)" />
    </NodeViewWrapper>
  );
}

function EmbedView({ node, selected, editor, getPos }: ReactNodeViewProps) {
  return (
    <NodeViewWrapper as="figure" data-type="embed" className={`my-4 ${selected ? "ring-2 ring-indigo-400" : ""}`}>
      <div contentEditable={false} data-drag-handle>
        <YouTubeCard resourceId={node.attrs.resourceId as string} activatable={false} />
      </div>
      {editor.isEditable && (
        <div contentEditable={false} className="mt-1 flex justify-end text-xs text-stone-900">
          <MoveButtons editor={editor} getPos={getPos} what="영상" />
        </div>
      )}
      <Caption placeholder="영상 설명(선택)" />
    </NodeViewWrapper>
  );
}

export const PostImageWithView = PostImage.extend({ addNodeView: () => ReactNodeViewRenderer(ImageView) });
export const EmbedWithView = Embed.extend({ addNodeView: () => ReactNodeViewRenderer(EmbedView) });
