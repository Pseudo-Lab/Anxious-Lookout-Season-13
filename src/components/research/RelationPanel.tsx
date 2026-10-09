"use client";

import { useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import {
  createRelation,
  deleteRelation,
  listDocuments,
  listItemRelations,
  listMaterials,
  updateRelation,
  type RelationInput,
} from "@/lib/research/api";
import { useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { ITEM_TYPE_LABELS, LIMITS, type Endpoint, type ItemType, type Relation } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";

export function itemHref(type: ItemType, id: string): string {
  return `/research/${type}/?id=${encodeURIComponent(id)}`;
}

function otherEnd(rel: Relation, self: Endpoint): Endpoint {
  return rel.source.type === self.type && rel.source.id === self.id ? rel.target : rel.source;
}

// 방향 없는 관계와 방향 있는 관계를 구분해 표시한다.
function directionLabel(rel: Relation, self: Endpoint): { symbol: string; text: string } {
  if (!rel.directed || rel.direction === "bidirectional") return { symbol: "↔", text: "상호 관련 (방향 없음)" };
  const outgoing =
    rel.direction === "outgoing" || (rel.direction === undefined && rel.source.type === self.type && rel.source.id === self.id);
  return outgoing ? { symbol: "→", text: "이 항목 → 대상" } : { symbol: "←", text: "대상 → 이 항목" };
}

type DirectionChoice = "out" | "in" | "none";

// 계약: 자료↔자료·문서↔문서는 방향 선택 가능, 문서→자료는 항상 문서가 source인 방향 관계.
function fixedDirection(self: ItemType, other: ItemType): DirectionChoice | null {
  if (self === other) return null;
  return self === "document" ? "out" : "in";
}

function buildInput(
  self: Endpoint,
  other: { type: ItemType; id: string },
  dir: DirectionChoice,
  kind: string,
  description: string
): RelationInput {
  const me = { type: self.type, id: self.id };
  const [source, target] = dir === "in" ? [other, me] : [me, other];
  return { source, target, kind, description, directed: dir !== "none" };
}

function RelationRow({ rel, self, onChanged }: { rel: Relation; self: Endpoint; onChanged: () => void }) {
  const other = otherEnd(rel, self);
  const dir = directionLabel(rel, self);
  const { run, busy } = useMutation();
  const [editing, setEditing] = useState(false);
  const [kind, setKind] = useState(rel.kind);
  const [description, setDescription] = useState(rel.description);
  const [failure, setFailure] = useState<FetchFailure | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const input = { kind: kind.trim(), description };
    const res = await run(`relation:update:${rel.id}:${rel.version}:${JSON.stringify(input)}`, (ctx) =>
      updateRelation(rel.id, input, rel.version, ctx)
    );
    if (res.ok) {
      setEditing(false);
      setFailure(null);
      onChanged();
    } else setFailure(res.failure);
  }

  async function remove() {
    if (!window.confirm("이 관계를 삭제할까요? 연결된 자료·문서는 그대로 남습니다.")) return;
    const res = await run(`relation:delete:${rel.id}:${rel.version}`, (ctx) => deleteRelation(rel.id, rel.version, ctx));
    if (res.ok) onChanged();
    else setFailure(res.failure);
  }

  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm">
            <span title={dir.text} aria-label={dir.text} className="mr-2 font-mono text-indigo-600">
              {dir.symbol}
            </span>
            <span className="mr-2 rounded bg-stone-100 px-1.5 py-0.5 text-xs text-stone-600">{ITEM_TYPE_LABELS[other.type]}</span>
            <Link href={itemHref(other.type, other.id)} className="font-medium text-stone-900 hover:text-indigo-700">
              {other.title || "(제목 없음)"}
            </Link>
          </p>
          {!editing && (
            <p className="mt-1 text-xs text-stone-500">
              <span className="font-medium text-stone-700">{rel.kind}</span> · {dir.text}
              {rel.description && <span className="mt-1 block whitespace-pre-wrap text-stone-600">{rel.description}</span>}
            </p>
          )}
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-2 text-xs">
            <button
              onClick={() => setEditing(true)}
              aria-label={`관계 수정: ${other.title}`}
              className="text-stone-500 hover:text-stone-800"
            >
              수정
            </button>
            <button
              onClick={() => void remove()}
              disabled={busy}
              aria-label={`관계 삭제: ${other.title}`}
              className="text-red-600 hover:text-red-800 disabled:opacity-50"
            >
              삭제
            </button>
          </div>
        )}
      </div>
      {editing && (
        <form onSubmit={save} className="space-y-2">
          <input
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            maxLength={LIMITS.kind}
            required
            aria-label="관계 종류"
            className="w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={LIMITS.description}
            rows={2}
            aria-label="관계 설명"
            className="w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
          />
          <div className="flex gap-2 text-sm">
            <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 px-3 py-1 text-white disabled:opacity-50">
              저장
            </button>
            <button type="button" onClick={() => setEditing(false)} className="rounded-lg px-3 py-1 text-stone-600 hover:bg-stone-100">
              취소
            </button>
          </div>
        </form>
      )}
      {failure && <ErrorNotice failure={failure} />}
    </li>
  );
}

function AddRelationForm({ self, onAdded }: { self: Endpoint; onAdded: () => void }) {
  const [otherType, setOtherType] = useState<ItemType>("material");
  const [otherId, setOtherId] = useState("");
  const [filter, setFilter] = useState("");
  const [choice, setChoice] = useState<DirectionChoice>("none");
  const [kind, setKind] = useState("");
  const [description, setDescription] = useState("");
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const { run, busy } = useMutation();

  const candidates = usePagedList<{ id: string; title: string }>(`candidates:${otherType}`, (cursor) =>
    otherType === "material" ? listMaterials(cursor) : listDocuments(cursor)
  );
  const visible = useMemo(
    () =>
      candidates.items.filter(
        (c) => !(otherType === self.type && c.id === self.id) && c.title.toLowerCase().includes(filter.trim().toLowerCase())
      ),
    [candidates.items, otherType, self, filter]
  );

  const fixed = fixedDirection(self.type, otherType);
  const direction = fixed ?? choice;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!otherId) return;
    const input = buildInput(self, { type: otherType, id: otherId }, direction, kind.trim(), description);
    const res = await run(`relation:create:${JSON.stringify(input)}`, (ctx) => createRelation(input, ctx));
    if (res.ok) {
      setOtherId("");
      setKind("");
      setDescription("");
      setFailure(null);
      onAdded();
    } else setFailure(res.failure);
  }

  const selectClass = "rounded-lg border border-stone-300 px-3 py-1.5 text-sm";
  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg bg-stone-50 p-4">
      <p className="text-sm font-medium text-stone-700">관계 추가</p>
      <div className="flex flex-wrap gap-2">
        <select
          value={otherType}
          onChange={(e) => {
            setOtherType(e.target.value as ItemType);
            setOtherId("");
          }}
          aria-label="대상 종류"
          className={selectClass}
        >
          <option value="material">자료</option>
          <option value="document">문서</option>
        </select>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="제목으로 찾기"
          aria-label="대상 제목 검색"
          className={`${selectClass} min-w-0 flex-1`}
        />
      </div>
      <select
        value={otherId}
        onChange={(e) => setOtherId(e.target.value)}
        required
        aria-label="연결할 대상"
        className={`${selectClass} w-full`}
        size={Math.min(6, Math.max(2, visible.length))}
      >
        {visible.map((c) => (
          <option key={c.id} value={c.id}>
            {c.title || "(제목 없음)"}
          </option>
        ))}
      </select>
      {candidates.failure && <ErrorNotice failure={candidates.failure} prefix="대상 목록을 불러오지 못했습니다." />}
      {candidates.nextCursor && !candidates.loading && (
        <button type="button" onClick={() => void candidates.loadMore()} className="text-xs text-indigo-600 hover:text-indigo-800">
          대상 더 불러오기
        </button>
      )}
      {fixed ? (
        <p className="text-xs text-stone-500">문서와 자료의 관계는 항상 문서 → 자료 방향입니다.</p>
      ) : (
        <fieldset className="flex flex-wrap gap-4 text-sm text-stone-700">
          <legend className="sr-only">방향</legend>
          {(
            [
              ["none", "상호 관련 (방향 없음)"],
              ["out", "이 항목 → 대상"],
              ["in", "대상 → 이 항목"],
            ] as const
          ).map(([value, label]) => (
            <label key={value} className="flex items-center gap-1.5">
              <input type="radio" name="direction" checked={choice === value} onChange={() => setChoice(value)} />
              {label}
            </label>
          ))}
        </fieldset>
      )}
      <input
        value={kind}
        onChange={(e) => setKind(e.target.value)}
        maxLength={LIMITS.kind}
        required
        placeholder="관계 종류 (예: 근거, 반박, 후속 연구)"
        aria-label="관계 종류"
        className={`${selectClass} w-full`}
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        maxLength={LIMITS.description}
        rows={2}
        placeholder="설명 (선택)"
        aria-label="관계 설명"
        className={`${selectClass} w-full`}
      />
      {failure && <ErrorNotice failure={failure} prefix="관계를 추가하지 못했습니다." />}
      <button
        type="submit"
        disabled={busy || !otherId || kind.trim() === ""}
        className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        {busy ? "추가 중..." : "관계 추가"}
      </button>
    </form>
  );
}

/** 이 항목과 연결된 자료·문서. 대화가 끝난 뒤에도 남는 개인 관계이며 공개 화면에는 나오지 않는다. */
export default function RelationPanel({ self, editable }: { self: Endpoint; editable: boolean }) {
  const [rev, setRev] = useState(0);
  const list = usePagedList(`relations:${self.type}:${self.id}:${rev}`, (cursor) =>
    listItemRelations(self.type, self.id, cursor)
  );
  const changed = () => setRev((r) => r + 1);

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-stone-800">연결된 자료·문서</h2>
      {list.failure && <ErrorNotice failure={list.failure} prefix="관계를 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-sm text-stone-500">아직 연결된 항목이 없습니다.</p>}
      {list.items.length > 0 && (
        <ul className="divide-y divide-stone-100 rounded-lg ring-1 ring-stone-200">
          {list.items.map((rel) => (
            <RelationRow key={`${rel.id}:${rel.version}`} rel={rel} self={self} onChanged={changed} />
          ))}
        </ul>
      )}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
      {editable && <AddRelationForm self={self} onAdded={changed} />}
    </section>
  );
}
