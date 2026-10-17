"use client";

import { useRef, useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { IMAGE_MAX_BYTES, IMAGE_TYPES, deletePostImage, listPostImages, ownImageUrl, uploadPostImage } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { PostImage } from "@/lib/posts/types";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import { isOwnImage } from "@/components/research/SafeMarkdown";

// #28 D2 이미지: 직접 올린 이미지만 본문·표지에 쓴다. 올린 이미지는 발행해야 공개되고, 그 전에는 나만 볼 수 있다.

const KB = 1024;
const sizeText = (n: number) => (n >= KB * KB ? `${(n / KB / KB).toFixed(1)} MiB` : `${Math.max(1, Math.round(n / KB))} KiB`);
/** Markdown 대체 텍스트로 쓸 수 있게 괄호·대괄호·줄바꿈을 정리한다. */
const altOf = (name: string) => name.replace(/\.[^.]+$/, "").replace(/[[\]()\n\r]/g, " ").trim().slice(0, 100) || "이미지";

function Thumb({ url, alt, className = "" }: { url: string; alt: string; className?: string }) {
  if (!isOwnImage(url)) return null;
  // eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음)
  return <img src={url} alt={alt} loading="lazy" className={`rounded object-cover ring-1 ring-stone-200 ${className}`} />;
}

export default function ImagePanel({
  coverImageId,
  onInsert,
  onCover,
  readOnly,
}: {
  coverImageId: string | null;
  // 본문 커서 위치에 Markdown을 넣는다
  onInsert: (markdown: string) => void;
  onCover: (id: string | null) => void;
  readOnly?: boolean;
}) {
  const [tick, setTick] = useState(0);
  const list = usePagedList(`post-images:${tick}`, (cursor) => listPostImages(cursor));
  const [open, setOpen] = useState(false);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const { run, busy } = useMutation();

  async function upload(f: File) {
    setFailure(null);
    setNote(null);
    if (!(IMAGE_TYPES as readonly string[]).includes(f.type)) {
      setNote("PNG, JPEG, WebP 이미지만 올릴 수 있습니다(SVG·GIF는 올릴 수 없습니다).");
      return;
    }
    if (f.size > IMAGE_MAX_BYTES) {
      setNote(`이미지가 너무 큽니다(${sizeText(f.size)}). 5 MiB 이하 파일만 올릴 수 있습니다.`);
      return;
    }
    setUploading(true);
    const res = await run(`post-image:upload:${f.name}:${f.size}:${f.lastModified}:${f.type}`, (ctx) => uploadPostImage(f, ctx));
    setUploading(false);
    if (!res.ok) {
      setFailure(res.failure);
      return;
    }
    onInsert(`![${altOf(f.name)}](${res.data.url})`);
    setNote(`올렸습니다(${res.data.width}×${res.data.height}, ${sizeText(res.data.size)}). 본문 커서 위치에 넣었습니다. 발행하기 전에는 나만 볼 수 있습니다.`);
    setOpen(true);
    setTick((t) => t + 1);
  }

  async function remove(img: PostImage) {
    setFailure(null);
    setNote(null);
    if (!window.confirm("이 이미지를 지울까요? 어디에서도 쓰지 않는 이미지만 지울 수 있습니다.")) return;
    const res = await run(`post-image:delete:${img.id}`, (ctx) => deletePostImage(img.id, ctx));
    if (res.ok) {
      if (coverImageId === img.id) onCover(null);
      setTick((t) => t + 1);
    } else setFailure(res.failure);
  }

  return (
    <section aria-label="이미지" className="space-y-3 rounded-lg p-3 ring-1 ring-stone-200">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-stone-700">이미지·표지</h2>
        <div className="flex items-center gap-3 text-xs">
          {!readOnly && (
            <>
              <input
                ref={file}
                type="file"
                accept={IMAGE_TYPES.join(",")}
                aria-label="이미지 파일"
                className="sr-only"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) void upload(f);
                }}
              />
              <button type="button" onClick={() => file.current?.click()} disabled={uploading || busy} className="font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
                {uploading ? "올리는 중..." : "이미지 올리기"}
              </button>
            </>
          )}
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="text-stone-600 hover:text-stone-900">
            {open ? "내 이미지 닫기" : "내 이미지"}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs text-stone-600">
        <span>표지:</span>
        {coverImageId ? (
          <>
            <Thumb url={ownImageUrl(coverImageId)} alt="표지" className="h-12 w-16" />
            {!readOnly && (
              <button type="button" onClick={() => onCover(null)} className="text-stone-500 underline hover:text-red-700">
                표지 빼기
              </button>
            )}
          </>
        ) : (
          <span className="text-stone-400">없음 (내 이미지에서 ‘표지로’를 누르세요)</span>
        )}
      </div>
      <p className="text-xs text-stone-500">PNG·JPEG·WebP, 5 MiB까지. 올린 이미지는 사진 정보(EXIF 등)를 지운 뒤 저장되며, 발행한 글에 쓰인 것만 공개됩니다.</p>
      {note && <p role="status" className="text-xs text-stone-700">{note}</p>}
      {failure && <ErrorNotice failure={failure} prefix="이미지 작업을 하지 못했습니다." describe={postErrorMessage} />}
      {open && (
        <div className="space-y-2">
          {list.failure && <ErrorNotice failure={list.failure} prefix="이미지 목록을 불러오지 못했습니다." describe={postErrorMessage} />}
          {list.loaded && !list.failure && list.items.length === 0 && <p className="text-xs text-stone-500">올린 이미지가 없습니다.</p>}
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {list.items.map((img) => (
              <li key={img.id} className="space-y-1 text-xs">
                <Thumb url={img.url} alt={`내 이미지 ${img.width}×${img.height}`} className="h-24 w-full" />
                <p className="text-stone-500">
                  {img.width}×{img.height} · {sizeText(img.size)}
                </p>
                {!readOnly && (
                  <p className="flex flex-wrap gap-2">
                    <button type="button" onClick={() => onInsert(`![이미지](${img.url})`)} className="font-medium text-indigo-600 hover:text-indigo-800">
                      본문에 넣기
                    </button>
                    <button type="button" onClick={() => onCover(img.id)} disabled={coverImageId === img.id} className="text-indigo-600 hover:text-indigo-800 disabled:text-stone-400">
                      {coverImageId === img.id ? "표지" : "표지로"}
                    </button>
                    <button type="button" onClick={() => void remove(img)} disabled={busy} className="text-stone-500 hover:text-red-700">
                      지우기
                    </button>
                  </p>
                )}
              </li>
            ))}
          </ul>
          {list.nextCursor && (
            <button type="button" onClick={() => void list.loadMore()} disabled={list.loading} className="text-xs font-medium text-indigo-600 hover:text-indigo-800">
              이미지 더 보기
            </button>
          )}
        </div>
      )}
    </section>
  );
}
