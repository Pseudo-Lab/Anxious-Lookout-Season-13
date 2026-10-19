"use client";

import { useState } from "react";

// #32 YouTube 영상 카드(계약 §2 embed). 사용자가 누르기 전에는 외부 요청을 하나도 만들지 않는다(썸네일 포함).
// 누르면 youtube-nocookie.com에 검증된 11자 ID로만 만든 경로를 고정 sandbox iframe으로 연다.
// 편집 화면(activatable=false)에서는 재생하지 않는다.

const ORIGIN = "https://www.youtube-nocookie.com";

export default function YouTubeCard({ resourceId, activatable = true }: { resourceId: string; activatable?: boolean }) {
  const [active, setActive] = useState(false);
  if (active) {
    return (
      <div className="aspect-video w-full overflow-hidden rounded-lg bg-black">
        <iframe
          src={`${ORIGIN}/embed/${resourceId}?autoplay=1`}
          title="YouTube 동영상"
          className="h-full w-full"
          sandbox="allow-scripts allow-same-origin allow-presentation"
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
          referrerPolicy="strict-origin-when-cross-origin"
          allowFullScreen
        />
      </div>
    );
  }
  return (
    <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-lg bg-stone-800 p-4 text-center text-stone-100">
      <span className="text-sm font-semibold">YouTube 동영상</span>
      <span className="font-mono text-xs text-stone-400">{resourceId}</span>
      {activatable ? (
        <button type="button" onClick={() => setActive(true)} className="mt-1 rounded-full bg-white px-4 py-1.5 text-sm font-medium text-stone-900 hover:bg-stone-200">
          눌러서 재생 (YouTube에 연결합니다)
        </button>
      ) : (
        <span className="text-xs text-stone-400">편집 화면에서는 재생하지 않습니다.</span>
      )}
    </div>
  );
}
