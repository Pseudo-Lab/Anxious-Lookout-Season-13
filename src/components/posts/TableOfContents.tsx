"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { parseBlockDocument } from "@/lib/blocks/document";
import { headingAnchor, outline, TOC_MIN_HEADINGS } from "@/lib/blocks/toc";

/** 읽기 진행 표시: 상단 바 바로 아래의 얇은 막대. 글(article) 안에서 얼마나 내려왔는지만 보인다(장식, 화면 읽기 도구에는 숨김). */
function ReadingProgress({ anchor }: { anchor: React.RefObject<HTMLElement | null> }) {
  const [ratio, setRatio] = useState(0);
  useEffect(() => {
    const article = anchor.current?.closest("article");
    if (!article) return;
    let frame = 0;
    const update = () => {
      frame = 0;
      const rect = article.getBoundingClientRect();
      const total = rect.height - window.innerHeight;
      setRatio(total <= 0 ? 1 : Math.min(1, Math.max(0, -rect.top / total)));
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [anchor]);
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-x-0 z-40 h-0.5" style={{ top: "var(--site-header-height, 0px)" }}>
      <div className="h-full origin-left bg-indigo-600 transition-transform duration-75" style={{ transform: `scaleX(${ratio})` }} />
    </div>
  );
}

/** #119 F1: 소제목이 TOC_MIN_HEADINGS개 이상인 글의 목차(접고 펼 수 있음)와 읽기 진행 표시. */
export default function TableOfContents({ content }: { content: unknown }) {
  const entries = useMemo(() => {
    const parsed = parseBlockDocument(content);
    return parsed.ok ? outline(parsed.doc) : [];
  }, [content]);
  const ref = useRef<HTMLElement>(null);
  if (entries.length < TOC_MIN_HEADINGS) return null;
  const top = Math.min(...entries.map((e) => e.level));
  return (
    <nav ref={ref} aria-labelledby="toc-title" className="mb-8 rounded-xl bg-stone-50 p-4 ring-1 ring-stone-200">
      <ReadingProgress anchor={ref} />
      <details>
        <summary id="toc-title" className="cursor-pointer text-sm font-semibold text-stone-900">
          목차 <span className="font-normal text-stone-600">({entries.length})</span>
        </summary>
        <ol className="mt-3 space-y-1.5 text-sm">
          {entries.map((e) => (
            <li key={e.id} style={{ paddingLeft: `${Math.min(e.level - top, 3) * 1}rem` }}>
              <a href={`#${headingAnchor(e.id)}`} className="text-stone-700 hover:text-indigo-700 hover:underline">
                {e.text}
              </a>
            </li>
          ))}
        </ol>
      </details>
    </nav>
  );
}
