import { cslTypeLabel, formatBibliography } from "@/lib/research/bibliography";
import type { Bibliography } from "@/lib/research/types";

// #105 한 줄 요약·서지정보 표시(자료·문서 상세, 버전 기록).

export function SummaryLine({ summary, className = "" }: { summary: string | null | undefined; className?: string }) {
  if (!summary) return null;
  return <p className={`text-sm text-stone-900 ${className}`}>{summary}</p>;
}

export function BibliographyLine({ bibliography }: { bibliography: Bibliography | null | undefined }) {
  if (!bibliography) return null;
  const text = formatBibliography(bibliography);
  return (
    <p className="text-xs text-stone-500">
      <span className="mr-1.5 rounded bg-stone-100 px-1.5 py-0.5 text-stone-900">{cslTypeLabel(bibliography.type)}</span>
      {text || "서지 항목 없음"}
    </p>
  );
}
