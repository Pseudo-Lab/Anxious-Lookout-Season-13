"use client";

import { NodeViewContent, NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { IMAGE_WIDTHS, type Align } from "@/lib/blocks/types";
import { ownImageUrl } from "@/lib/posts/api";
import YouTubeCard from "@/components/blocks/YouTubeCard";
import { Embed, PostImage } from "./schema";

// #32 편집기 안 이미지·영상 블록. 설정(alt·폭·정렬)은 블록 위 작은 입력으로 바꾸고 caption은 그 아래에서 바로 쓴다.

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

function ImageView({ node, updateAttributes, selected, editor }: ReactNodeViewProps) {
  const { imageId, alt, widthPercent, align } = node.attrs as { imageId: string; alt: string; widthPercent: number; align: Align };
  const editable = editor.isEditable;
  return (
    <NodeViewWrapper as="figure" data-type="post-image" className={`my-4 ${FIGURE_ALIGN[align]} ${selected ? "ring-2 ring-indigo-400" : ""}`} style={{ width: `${widthPercent}%` }}>
      {/* eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음) */}
      <img src={ownImageUrl(imageId)} alt={alt} className="w-full rounded-lg ring-1 ring-stone-200" draggable={false} data-drag-handle />
      {editable && (
        <div contentEditable={false} className="mt-1 flex flex-wrap items-center gap-2 text-xs text-stone-600">
          <label className="flex flex-1 items-center gap-1">
            <span>대체 텍스트</span>
            <input
              value={alt}
              maxLength={1000}
              onChange={(e) => updateAttributes({ alt: e.target.value.replace(/[\r\n]+/g, " ") })}
              className="min-w-0 flex-1 rounded border border-stone-300 px-1.5 py-0.5"
            />
          </label>
          <select aria-label="이미지 폭" value={widthPercent} onChange={(e) => updateAttributes({ widthPercent: Number(e.target.value) })} className="rounded border border-stone-300 px-1 py-0.5">
            {IMAGE_WIDTHS.map((w) => (
              <option key={w} value={w}>
                폭 {w}%
              </option>
            ))}
          </select>
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

function EmbedView({ node, selected }: ReactNodeViewProps) {
  return (
    <NodeViewWrapper as="figure" data-type="embed" className={`my-4 ${selected ? "ring-2 ring-indigo-400" : ""}`}>
      <div contentEditable={false} data-drag-handle>
        <YouTubeCard resourceId={node.attrs.resourceId as string} activatable={false} />
      </div>
      <Caption placeholder="영상 설명(선택)" />
    </NodeViewWrapper>
  );
}

export const PostImageWithView = PostImage.extend({ addNodeView: () => ReactNodeViewRenderer(ImageView) });
export const EmbedWithView = Embed.extend({ addNodeView: () => ReactNodeViewRenderer(EmbedView) });
