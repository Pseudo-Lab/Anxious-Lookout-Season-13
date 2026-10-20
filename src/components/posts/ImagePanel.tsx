"use client";

import { useEffect, useRef, useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { IMAGE_MAX_BYTES, IMAGE_TYPES, deletePostImage, listPostImages, ownImageUrl, uploadPostImage } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import type { PostImage } from "@/lib/posts/types";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import ErrorNotice from "@/components/research/ErrorNotice";
import { isOwnImage } from "@/components/research/SafeMarkdown";

// #28 D2 이미지: 직접 올린 이미지만 본문·표지에 쓴다. 올린 이미지는 발행해야 공개되고, 그 전에는 나만 볼 수 있다.
// #36 F7: 패널을 접고 펼 수 있고(이미지가 많을 때), 접힘 상태는 이 브라우저에만 기억한다(저장소를 쓸 수 없으면 펼친 채 시작).
// #36 F8: 지금 쓰고 있는 이미지(이 글의 본문·표지, 서버가 알려 준 다른 초안·제안·버전·발행본)는 '지우기' 대신 '사용 중'과 그 이유를 보인다.
//         서버 삭제 규칙은 그대로다(어디서도 안 쓰는 이미지만 지운다). 다른 곳의 쓰임을 모르면 지우기를 두고 409 안내를 보인다.

const COLLAPSE_KEY = "post-image-panel:collapsed";
function readCollapsed() {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}
function writeCollapsed(v: boolean) {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, v ? "1" : "0");
  } catch {
    // 저장소를 쓸 수 없으면 이 화면에서만 접는다
  }
}
const USE_TEXT: Record<string, string> = { draft: "초안", proposal: "제안", version: "버전", publication: "발행본" };

const KB = 1024;
const sizeText = (n: number) => (n >= KB * KB ? `${(n / KB / KB).toFixed(1)} MiB` : `${Math.max(1, Math.round(n / KB))} KiB`);
/** 파일 이름에서 대체 텍스트를 만든다(확장자·줄바꿈 제거). */
const altOf = (name: string) => name.replace(/\.[^.]+$/, "").replace(/[\n\r]+/g, " ").trim().slice(0, 100) || "이미지";

function Thumb({ url, alt, className = "" }: { url: string; alt: string; className?: string }) {
  if (!isOwnImage(url)) return null;
  // eslint-disable-next-line @next/next/no-img-element -- 서버 권한 검사를 거치는 원본(최적화 서버 없음)
  return <img src={url} alt={alt} loading="lazy" className={`rounded object-cover ring-1 ring-stone-200 ${className}`} />;
}

/** 쓰는 곳이 있으면 '사용 중(…)'을, 없으면(알려진 범위에서) '지우기'를 보인다. */
function InUse({ img, here, onRemove, busy }: { img: PostImage; here: boolean; onRemove: () => void; busy: boolean }) {
  const elsewhere = (img.usage ?? []).map((u) => USE_TEXT[u]).filter(Boolean);
  const where = [...(here ? ["이 글"] : []), ...elsewhere];
  if (where.length) {
    return (
      <span className="text-stone-500" title="쓰는 곳에서 모두 빼면 지울 수 있습니다">
        사용 중({where.join("·")})
      </span>
    );
  }
  return (
    <button type="button" onClick={onRemove} disabled={busy} className="text-stone-500 hover:text-red-700">
      지우기
    </button>
  );
}

export default function ImagePanel({
  coverImageId,
  onInsert,
  onCover,
  onUploading,
  readOnly,
  usedHere,
}: {
  coverImageId: string | null;
  // 이 글(편집 중인 본문·표지)에서 쓰고 있는 이미지 id
  usedHere?: ReadonlySet<string>;
  // 본문 커서 위치에 이미지 블록을 넣는다(#32: 주소가 아니라 imageId로 넣는다)
  onInsert: (image: { id: string; alt: string }) => void;
  onCover: (id: string | null) => void;
  // 업로드 시작·끝(편집기가 저장·닫기를 잠근다)
  onUploading?: (uploading: boolean) => void;
  readOnly?: boolean;
}) {
  // 늦게 끝난 업로드가 닫힌(언마운트) 편집기나 잠긴 편집기에 쓰지 않게 최신 상태를 따로 본다.
  const alive = useRef(true);
  const latestReadOnly = useRef(readOnly);
  const onUploadingRef = useRef(onUploading);
  useEffect(() => {
    latestReadOnly.current = readOnly;
    onUploadingRef.current = onUploading;
  });
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      onUploadingRef.current?.(false);
    };
  }, []);
  const [tick, setTick] = useState(0);
  const list = usePagedList(`post-images:${tick}`, (cursor) => listPostImages(cursor));
  const [open, setOpen] = useState(false);
  // 편집기는 로그인 확인 뒤 브라우저에서만 그려지므로 처음 상태를 바로 저장소에서 읽는다
  const [collapsed, setCollapsedState] = useState(readCollapsed);
  const setCollapsed = (v: boolean) => {
    setCollapsedState(v);
    writeCollapsed(v);
  };
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
    onUploading?.(true);
    const res = await run(`post-image:upload:${f.name}:${f.size}:${f.lastModified}:${f.type}`, (ctx) => uploadPostImage(f, ctx));
    if (!alive.current) return;
    setUploading(false);
    onUploadingRef.current?.(false);
    if (!res.ok) {
      setFailure(res.failure);
      return;
    }
    if (latestReadOnly.current) {
      setNote("올렸지만 편집기가 잠겨 있어 본문에 넣지 않았습니다. 내 이미지에서 다시 넣을 수 있습니다.");
    } else {
      onInsert({ id: res.data.id, alt: altOf(f.name) });
      setNote(`올렸습니다(${res.data.width}×${res.data.height}, ${sizeText(res.data.size)}). 본문 커서 위치에 넣었습니다. 발행하기 전에는 나만 볼 수 있습니다.`);
    }
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
          {!collapsed && (
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="text-stone-600 hover:text-stone-900">
              {open ? "내 이미지 닫기" : "내 이미지"}
            </button>
          )}
          <button
            type="button"
            onClick={() => setCollapsed(!collapsed)}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "이미지·표지 펼치기" : "이미지·표지 접기"}
            className="rounded px-1.5 text-stone-500 hover:bg-stone-100 hover:text-stone-900"
          >
            {collapsed ? "▸ 펼치기" : "▾ 접기"}
          </button>
        </div>
      </div>
      {collapsed && (note || failure) && (
        <>
          {note && <p role="status" className="text-xs text-stone-700">{note}</p>}
          {failure && <ErrorNotice failure={failure} prefix="이미지 작업을 하지 못했습니다." describe={postErrorMessage} />}
        </>
      )}
      {!collapsed && (
        <>
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
          <span className="text-stone-500">없음 (내 이미지에서 ‘표지로’를 누르세요)</span>
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
                    <button type="button" onClick={() => onInsert({ id: img.id, alt: "" })} className="font-medium text-indigo-600 hover:text-indigo-800">
                      본문에 넣기
                    </button>
                    <button type="button" onClick={() => onCover(img.id)} disabled={coverImageId === img.id} className="text-indigo-600 hover:text-indigo-800 disabled:text-stone-400">
                      {coverImageId === img.id ? "표지" : "표지로"}
                    </button>
                    <InUse img={img} here={usedHere?.has(img.id) || coverImageId === img.id} onRemove={() => void remove(img)} busy={busy} />
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
        </>
      )}
    </section>
  );
}
