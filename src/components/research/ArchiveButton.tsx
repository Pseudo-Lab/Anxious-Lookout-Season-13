"use client";

import { useState } from "react";
import type { FetchFailure } from "@/lib/api/client";
import { archiveItem } from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { ITEM_TYPE_LABELS, type ItemType } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";

// 보관은 비파괴: 활성 목록에서 숨기고 수정·연결·공개를 막지만 버전 기록은 남는다.
export default function ArchiveButton({
  type,
  id,
  version,
  onArchived,
}: {
  type: ItemType;
  id: string;
  version: number;
  onArchived: () => void;
}) {
  const { run, busy } = useMutation();
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function archive() {
    const label = ITEM_TYPE_LABELS[type];
    if (!window.confirm(`이 ${label}를 보관할까요? 목록에서 숨겨지고 더 이상 수정할 수 없지만 버전 기록은 보존됩니다.`)) return;
    const res = await run(`archive:${type}:${id}:${version}`, (ctx) => archiveItem(type, id, version, ctx));
    if (res.ok) onArchived();
    else setFailure(res.failure);
  }

  return (
    <>
      <button onClick={() => void archive()} disabled={busy} className="text-sm text-stone-500 hover:text-red-700 disabled:opacity-50">
        보관
      </button>
      {failure && <ErrorNotice failure={failure} prefix="보관하지 못했습니다." />}
    </>
  );
}
