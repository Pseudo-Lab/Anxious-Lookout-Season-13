"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLoad } from "@/lib/research/hooks";
import { isUncertain } from "@/lib/research/idempotency";
import type { SessionDetail } from "@/lib/research/types";
import { getResearchCalls, groupResearchCalls, researchCallHref, type ResearchCall } from "@/lib/research/researchCalls";

/** 모델/native 기록과 분리된 서버 소유 작업 기록. requestId를 개별 말풍선과 추정 연결하지 않는다. */
export function ResearchActivityList({ calls }: { calls: ResearchCall[] }) {
  const rows = groupResearchCalls(calls).filter(({ call }) => call.status === "succeeded" && (call.saved?.resourceType === "document" || call.saved?.resourceType === "post"));
  if (!rows.length) return null;
  return (
    <ul aria-label="저장한 문서·글" className="space-y-1 text-xs text-stone-500">
        {rows.map(({ call }) => {
          const href = researchCallHref(call);
          const saved = call.saved;
          return (
            <li key={call.id} data-research-call={call.id} className="break-words">
              저장됨: {saved?.title || "(제목 없음)"} v{saved?.versionNumber} · {href && <a href={href} className="text-indigo-600 underline hover:no-underline">보기</a>}
            </li>
          );
        })}
    </ul>
  );
}

export default function ResearchActivity({ id, record, active, poll, onChange }: { id: string; record: SessionDetail; active: boolean; poll: boolean; onChange: (signal: string) => void }) {
  const { result, refreshFailure, reload } = useLoad(`research-calls:${id}`, () => getResearchCalls(id));
  const seen = useRef({ record, active });
  const [retrying, setRetrying] = useState(false);
  useEffect(() => {
    const prev = seen.current;
    seen.current = { record, active };
    // polling 상세 갱신 및 SSE 확정 GET 뒤에 receipt도 갱신한다. 대화창 재오픈 때도 확인한다.
    if (active && (prev.record !== record || !prev.active)) void reload();
  }, [record, active, reload]);
  const failure = refreshFailure ?? (result && !result.ok ? result.failure : null);
  const transient = !!failure && isUncertain(failure);
  useEffect(() => {
    // SSE는 DB-write-free 보조 채널이다. 실행 중 SSE 화면에서는 pure receipt GET만 주기적으로 읽는다.
    // 확정 뒤의 일시 조회 실패도 GET만 재시도한다. 어떤 저장/메시지도 재전송하지 않는다.
    if (!active || (!poll && !transient) || (failure && !transient)) return;
    const timer = setTimeout(() => void reload(), transient ? 4000 : 2000);
    return () => clearTimeout(timer);
  }, [active, poll, transient, failure, result, reload]);

  const calls = result?.ok ? result.data : [];
  const signal = groupResearchCalls(calls).filter(({ call }) => call.status === "succeeded" && (call.saved?.resourceType === "document" || call.saved?.resourceType === "post")).map(({ call }) => call.id).join("|");
  // 완료 조회가 늦게 도착해도 기존 '바닥 추적/읽던 위치 유지' 규칙으로 연결한다.
  useLayoutEffect(() => onChange(signal), [signal, onChange]);
  // 확정 권한/소유권 상실 또는 이전 API의 미지원이면 이전 작업 내용을 유지하지 않는다.
  if (failure?.kind === "http" && [401, 403, 404].includes(failure.status)) return null;
  if (!calls.length && !failure) return null;
  return (
    <div className="space-y-2">
      <ResearchActivityList calls={calls} />
      {failure && (
        <div className="space-y-1 text-xs text-amber-800" role="status">
          <p>저장 기록 확인 실패</p>
          <button
            type="button"
            disabled={retrying}
            className="font-medium underline hover:no-underline disabled:opacity-50"
            onClick={async () => { setRetrying(true); try { await reload(); } finally { setRetrying(false); } }}
          >
            다시 확인
          </button>
        </div>
      )}
    </div>
  );
}
