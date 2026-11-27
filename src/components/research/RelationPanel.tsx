"use client";

import { useEffect, useMemo, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from "react";
import Link from "next/link";
import type { FetchFailure } from "@/lib/api/client";
import {
  createRelation,
  deleteRelation,
  getRelationKinds,
  listDocuments,
  listItemRelations,
  listMaterials,
  listVersions,
  updateRelation,
  type RelationInput,
} from "@/lib/research/api";
import { checkEvidence, evidencePayload, nextDraftKey, savedPayload, toDraft, type EvidenceDraft } from "@/lib/research/wikiInput";
import { useLoad, useMutation } from "@/lib/research/hooks";
import { usePagedList } from "@/lib/research/usePagedList";
import { groupRelations, kindChoices, kindOf, readsAsSource, relationLabel, type RelationKind, type RelationKinds } from "@/lib/research/relationKinds";
import { formatDateTime } from "@/lib/research/format";
import { ITEM_TYPE_LABELS, LIMITS, type Endpoint, type ItemType, type Relation } from "@/lib/research/types";
import ErrorNotice from "@/components/research/ErrorNotice";
import { CharCount } from "@/components/research/ItemForms";
import { documentPath, materialPath } from "@/lib/routes";

export function itemHref(type: ItemType, id: string): string {
  return type === "material" ? materialPath(id) : documentPath(id);
}

function otherEnd(rel: Relation, self: Endpoint): Endpoint {
  return rel.source.type === self.type && rel.source.id === self.id ? rel.target : rel.source;
}

// 방향 기호와 설명(이 항목 기준)
function directionLabel(rel: Relation, self: Endpoint): { symbol: string; text: string } {
  if (!rel.directed || rel.direction === "bidirectional") return { symbol: "↔", text: "방향 없음" };
  return readsAsSource(rel, self) ? { symbol: "→", text: "이 항목 → 대상" } : { symbol: "←", text: "대상 → 이 항목" };
}

const fieldClass = "rounded-lg border border-stone-300 px-3 py-1.5 text-sm";

// ---- 근거 위치 ----
// 행 상태·확인은 lib/research/wikiInput(단위 테스트). 길이는 code point 기준이라 입력 칸에 maxLength를 두지 않는다.

/** 항목의 버전 선택. 값이 비어 있으면 최신 버전을 고른다. */
function VersionSelect({
  item,
  value,
  savedNumber,
  onChange,
  label,
}: {
  item: Endpoint;
  value: string;
  savedNumber?: number;
  onChange: (versionId: string) => void;
  label: string;
}) {
  const list = usePagedList(`evidence-versions:${item.type}:${item.id}`, (cursor) => listVersions(item.type, item.id, cursor));
  const latest = list.items[0];
  const onChangeRef = useRef(onChange);
  useEffect(() => {
    onChangeRef.current = onChange;
  });
  useEffect(() => {
    if (!value && latest) onChangeRef.current(latest.id);
  }, [value, latest]);
  const known = list.items.some((v) => v.id === value);
  return (
    <span className="flex flex-wrap items-center gap-2">
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className={fieldClass}>
        {!value && <option value="">버전 불러오는 중…</option>}
        {value && !known && <option value={value}>{savedNumber ? `v${savedNumber}` : "선택한 버전"}</option>}
        {list.items.map((v, i) => (
          <option key={v.id} value={v.id}>
            v{v.number}
            {i === 0 ? " (최신)" : ""} · {formatDateTime(v.createdAt)}
          </option>
        ))}
      </select>
      {list.nextCursor && !list.loading && (
        <button type="button" onClick={() => void list.loadMore()} className="text-xs text-indigo-600 hover:text-indigo-800">
          이전 버전 더 보기
        </button>
      )}
      {list.failure && <ErrorNotice failure={list.failure} prefix="버전 목록을 불러오지 못했습니다." />}
    </span>
  );
}

/**
 * 관계의 근거 위치 목록 편집. endpoints: 근거로 고를 수 있는 항목(관계의 두 끝).
 * 여러 행의 버전 초기화가 같은 렌더에서 끝나도 서로 덮어쓰지 않도록 상태는 함수형으로 갱신한다.
 */
function EvidenceEditor({
  endpoints,
  rows,
  setRows,
  disabled,
}: {
  endpoints: { item: Endpoint; role: string }[];
  rows: EvidenceDraft[];
  setRows: Dispatch<SetStateAction<EvidenceDraft[]>>;
  disabled?: boolean;
}) {
  const set = (key: number, patch: Partial<EvidenceDraft>) => setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  return (
    <fieldset className="space-y-2">
      <legend className="text-xs font-medium text-stone-900">
        근거 위치 (선택, {LIMITS.evidence}개까지) <span className="font-normal text-stone-500">· 어느 항목의 어느 버전, 어디에서 확인했는지</span>
      </legend>
      {rows.map((r, i) => {
        const target = endpoints.find((e) => e.item.id === r.itemId);
        return (
          <div key={r.key} className="space-y-2 rounded-lg bg-white p-3 ring-1 ring-stone-200">
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={r.itemId}
                onChange={(e) => set(r.key, { itemId: e.target.value, versionId: "", versionNumber: undefined })}
                aria-label={`근거 ${i + 1} 항목`}
                className={`${fieldClass} min-w-0 max-w-full`}
              >
                {endpoints.map(({ item, role }) => (
                  <option key={item.id} value={item.id}>
                    {role}: {item.title || "(제목 없음)"}
                  </option>
                ))}
              </select>
              {target && (
                <VersionSelect
                  key={target.item.id}
                  item={target.item}
                  value={r.versionId}
                  savedNumber={r.versionNumber}
                  onChange={(versionId) => set(r.key, { versionId })}
                  label={`근거 ${i + 1} 버전`}
                />
              )}
              <button
                type="button"
                onClick={() => setRows((rs) => rs.filter((x) => x.key !== r.key))}
                aria-label={`근거 ${i + 1} 빼기`}
                className="ml-auto text-xs text-stone-500 hover:text-red-700"
              >
                빼기
              </button>
            </div>
            <div className="flex items-center gap-2">
              <input
                value={r.locator}
                onChange={(e) => set(r.key, { locator: e.target.value })}
                placeholder="위치 (예: p.12, §3.2)"
                aria-label={`근거 ${i + 1} 위치`}
                className={`${fieldClass} min-w-0 flex-1`}
              />
              <CharCount value={r.locator} limit={LIMITS.locator} />
            </div>
            <div className="flex items-end gap-2">
              <textarea
                value={r.quote}
                onChange={(e) => set(r.key, { quote: e.target.value })}
                rows={2}
                placeholder="인용문 (원문 그대로)"
                aria-label={`근거 ${i + 1} 인용문`}
                className={`${fieldClass} min-w-0 flex-1`}
              />
              <CharCount value={r.quote} limit={LIMITS.quote} />
            </div>
          </div>
        );
      })}
      <button
        type="button"
        disabled={disabled || rows.length >= LIMITS.evidence || endpoints.length === 0}
        onClick={() => setRows((rs) => [...rs, { key: nextDraftKey(), itemId: endpoints[0].item.id, versionId: "", locator: "", quote: "" }])}
        className="text-xs font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
      >
        + 근거 위치 추가
      </button>
    </fieldset>
  );
}

function EvidenceList({ rel, self }: { rel: Relation; self: Endpoint }) {
  if (!rel.evidence.length) return null;
  const titleOf = (id: string) => {
    const e = [rel.source, rel.target].find((x) => x.id === id);
    if (!e) return "";
    return e.type === self.type && e.id === self.id ? "이 항목" : e.title || "(제목 없음)";
  };
  return (
    <ul aria-label="근거 위치" className="mt-1 space-y-1 border-l-2 border-stone-200 pl-3">
      {rel.evidence.map((e, i) => (
        <li key={i} className="text-xs text-stone-500">
          <span className="text-stone-900">{titleOf(e.itemId)}</span> v{e.versionNumber}
          {e.locator && <span> · {e.locator}</span>}
          {e.quote && <q className="mt-0.5 block whitespace-pre-wrap text-stone-900">{e.quote}</q>}
        </li>
      ))}
    </ul>
  );
}

// ---- 관계 한 줄 ----

function KindSelect({
  choices,
  value,
  onChange,
  optionLabel,
  placeholder,
}: {
  choices: RelationKind[];
  value: string;
  onChange: (key: string) => void;
  optionLabel: (k: RelationKind) => string;
  placeholder: string;
}) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} required aria-label="관계 유형" className={`${fieldClass} w-full`}>
      <option value="" disabled>
        {placeholder}
      </option>
      {choices.map((k) => (
        <option key={k.key} value={k.key}>
          {optionLabel(k)}
        </option>
      ))}
    </select>
  );
}

function RelationRow({
  rel,
  self,
  kinds,
  onChanged,
}: {
  rel: Relation;
  self: Endpoint;
  // 어휘를 불러오지 못했으면 null(원래 문자열로만 보이고 수정할 수 없다)
  kinds: RelationKinds | null;
  onChanged: () => void;
}) {
  const other = otherEnd(rel, self);
  const dir = directionLabel(rel, self);
  const known = kinds ? kindOf(kinds, rel) : null;
  const label = kinds ? relationLabel(kinds, rel, self) : rel.kind;
  const { run, busy } = useMutation();
  const [editing, setEditing] = useState(false);
  const [kind, setKind] = useState(known ? rel.kind : "");
  const [description, setDescription] = useState(rel.description);
  const [rows, setRows] = useState<EvidenceDraft[]>(() => rel.evidence.map(toDraft));
  const [localError, setLocalError] = useState<string | null>(null);
  const [failure, setFailure] = useState<FetchFailure | null>(null);
  const asSource = readsAsSource(rel, self);

  function startEdit() {
    setKind(known ? rel.kind : "");
    setDescription(rel.description);
    setRows(rel.evidence.map(toDraft));
    setLocalError(null);
    setFailure(null);
    setEditing(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!kind) return setLocalError("관계 유형을 골라 주세요.");
    if (!description.trim()) return setLocalError("연결한 이유(근거)를 입력해 주세요.");
    const problem = checkEvidence(rows);
    if (problem) return setLocalError(problem);
    setLocalError(null);
    const evidence = evidencePayload(rows);
    const changedEvidence = JSON.stringify(evidence) !== JSON.stringify(savedPayload(rel.evidence));
    const input = { kind, description, ...(changedEvidence ? { evidence } : {}) };
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

  // 방향은 바꿀 수 없으므로 그 관계의 directed와 같은 유형만 고른다. 문구는 이 항목 쪽에서 읽는 쪽이다.
  const choices = kinds ? kindChoices(kinds, { directed: rel.directed }) : [];
  return (
    <li className="space-y-2 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm">
            <span title={dir.text} aria-label={dir.text} className="mr-2 font-mono text-indigo-600">
              {dir.symbol}
            </span>
            <span className="mr-2 font-medium text-stone-900">{label}</span>
            <span className="mr-2 rounded bg-stone-100 px-1.5 py-0.5 text-xs text-stone-900">{ITEM_TYPE_LABELS[other.type]}</span>
            <Link href={itemHref(other.type, other.id)} className="font-medium text-stone-900 hover:text-indigo-700">
              {other.title || "(제목 없음)"}
            </Link>
          </p>
          {!editing && (
            <>
              {rel.description ? (
                <p className="mt-1 whitespace-pre-wrap text-xs text-stone-900">{rel.description}</p>
              ) : (
                <p className="mt-1 text-xs text-amber-800">연결한 이유가 비어 있습니다.</p>
              )}
              {kinds && !known && <p className="mt-1 text-xs text-amber-800">예전 형식의 유형입니다. 수정할 때 유형을 새로 골라야 합니다.</p>}
              <EvidenceList rel={rel} self={self} />
            </>
          )}
        </div>
        {!editing && (
          <div className="flex shrink-0 gap-2 text-xs">
            {kinds && (
              <button onClick={startEdit} aria-label={`관계 수정: ${other.title}`} className="text-stone-500 hover:text-stone-800">
                수정
              </button>
            )}
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
      {editing && kinds && (
        <form onSubmit={save} className="space-y-2">
          {!known && (
            <p className="text-xs text-amber-800">
              예전 유형 ‘{rel.kind}’은(는) 더 이상 쓰지 않습니다. 이 관계의 방향({rel.directed ? "방향 있음" : "방향 없음"})에 맞는 유형을 골라 주세요.
            </p>
          )}
          <KindSelect
            choices={choices}
            value={kind}
            onChange={setKind}
            optionLabel={(k) => (asSource ? k.label : k.inverseLabel)}
            placeholder="관계 유형 고르기"
          />
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={LIMITS.description}
            rows={2}
            required
            placeholder="연결한 이유 (필수)"
            aria-label="연결한 이유"
            className={`${fieldClass} w-full`}
          />
          <EvidenceEditor
            endpoints={[
              { item: self, role: "이 항목" },
              { item: other, role: "대상" },
            ]}
            rows={rows}
            setRows={setRows}
          />
          {localError && <p role="alert" className="text-sm text-red-700">{localError}</p>}
          <div className="flex gap-2 text-sm">
            <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 px-3 py-1 text-white disabled:opacity-50">
              저장
            </button>
            <button type="button" onClick={() => setEditing(false)} className="rounded-lg px-3 py-1 text-stone-900 hover:bg-stone-100">
              취소
            </button>
          </div>
        </form>
      )}
      {failure && <ErrorNotice failure={failure} />}
    </li>
  );
}

// ---- 관계 추가 ----

type DirectionChoice = "out" | "in";

function AddRelationForm({ self, kinds, onAdded }: { self: Endpoint; kinds: RelationKinds; onAdded: () => void }) {
  const [otherType, setOtherType] = useState<ItemType>("material");
  const [otherId, setOtherId] = useState("");
  const [filter, setFilter] = useState("");
  const [kindKey, setKindKey] = useState("");
  const [choice, setChoice] = useState<DirectionChoice>("out");
  const [description, setDescription] = useState("");
  const [rows, setRows] = useState<EvidenceDraft[]>([]);
  const [localError, setLocalError] = useState<string | null>(null);
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
  const other = candidates.items.find((c) => c.id === otherId);
  const otherEndpoint: Endpoint | null = other ? { type: otherType, id: other.id, title: other.title } : null;

  // 문서↔자료는 항상 문서가 source이고 방향 있는 유형만 된다.
  const mixed = otherType !== self.type;
  const choices = kindChoices(kinds, { mixed });
  const kind = choices.find((k) => k.key === kindKey) ?? null;
  const direction: DirectionChoice | null = !kind?.directed ? null : mixed ? (self.type === "document" ? "out" : "in") : choice;

  function changeOther(type: ItemType, id: string) {
    setOtherType(type);
    setOtherId(id);
    // 바뀐 대상을 가리키던 근거는 뺀다(관계의 두 끝만 가리킬 수 있다).
    setRows((rs) => rs.filter((r) => r.itemId === self.id));
    if (type !== self.type && !kinds.items.find((k) => k.key === kindKey)?.directed) setKindKey("");
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!otherId || !kind) return;
    if (!description.trim()) return setLocalError("연결한 이유(근거)를 입력해 주세요.");
    const problem = checkEvidence(rows);
    if (problem) return setLocalError(problem);
    setLocalError(null);
    const me = { type: self.type, id: self.id };
    const them = { type: otherType, id: otherId };
    const [source, target] = direction === "in" ? [them, me] : [me, them];
    const input: RelationInput = { source, target, kind: kind.key, description, ...(rows.length ? { evidence: evidencePayload(rows) } : {}) };
    const res = await run(`relation:create:${JSON.stringify(input)}`, (ctx) => createRelation(input, ctx));
    if (res.ok) {
      setOtherId("");
      setKindKey("");
      setDescription("");
      setRows([]);
      setFailure(null);
      onAdded();
    } else setFailure(res.failure);
  }

  const endpoints = [{ item: self, role: "이 항목" }, ...(otherEndpoint ? [{ item: otherEndpoint, role: "대상" }] : [])];
  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg bg-stone-50 p-4">
      <p className="text-sm font-medium text-stone-900">관계 추가</p>
      <div className="flex flex-wrap gap-2">
        <select
          value={otherType}
          onChange={(e) => changeOther(e.target.value as ItemType, "")}
          aria-label="대상 종류"
          className={fieldClass}
        >
          <option value="material">자료</option>
          <option value="document">문서</option>
        </select>
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="제목으로 찾기"
          aria-label="대상 제목 검색"
          className={`${fieldClass} min-w-0 flex-1`}
        />
      </div>
      <select
        value={otherId}
        onChange={(e) => changeOther(otherType, e.target.value)}
        required
        aria-label="연결할 대상"
        className={`${fieldClass} w-full`}
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
      <KindSelect
        choices={choices}
        value={kind ? kind.key : ""}
        onChange={setKindKey}
        optionLabel={(k) => (k.directed ? `${k.label} / ${k.inverseLabel}` : k.label)}
        placeholder="관계 유형 고르기"
      />
      {kind &&
        (!kind.directed ? (
          <p className="text-xs text-stone-500">‘{kind.label}’은(는) 방향이 없는 관계입니다.</p>
        ) : mixed ? (
          <p className="text-xs text-stone-500">
            문서와 자료의 관계는 항상 문서 → 자료 방향입니다. 이 항목 쪽에서 읽으면 ‘{direction === "out" ? kind.label : kind.inverseLabel}’입니다.
          </p>
        ) : (
          <fieldset className="flex flex-wrap gap-4 text-sm text-stone-900">
            <legend className="sr-only">방향</legend>
            {(
              [
                ["out", `이 항목이 ${kind.label} (이 항목 → 대상)`],
                ["in", `이 항목이 ${kind.inverseLabel} (대상 → 이 항목)`],
              ] as const
            ).map(([value, text]) => (
              <label key={value} className="flex items-center gap-1.5">
                <input type="radio" name="direction" checked={choice === value} onChange={() => setChoice(value)} />
                {text}
              </label>
            ))}
          </fieldset>
        ))}
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        maxLength={LIMITS.description}
        rows={2}
        required
        placeholder="연결한 이유 (필수)"
        aria-label="연결한 이유"
        className={`${fieldClass} w-full`}
      />
      <EvidenceEditor endpoints={endpoints} rows={rows} setRows={setRows} />
      {localError && <p role="alert" className="text-sm text-red-700">{localError}</p>}
      {failure && <ErrorNotice failure={failure} prefix="관계를 추가하지 못했습니다." />}
      <button
        type="submit"
        disabled={busy || !otherId || !kind || description.trim() === ""}
        className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
      >
        {busy ? "추가 중..." : "관계 추가"}
      </button>
    </form>
  );
}

/**
 * 이 항목과 연결된 자료·문서. 대화가 끝난 뒤에도 남는 개인 관계이며 공개 화면에는 나오지 않는다.
 * #105: 극성별(지지·확장 / 반박·이견 / 중립)로 묶고 이 항목 쪽에서 읽는 문구로 보인다. 예전 자유 텍스트 유형은 맨 뒤 ‘기타’.
 */
export default function RelationPanel({ self, editable }: { self: Endpoint; editable: boolean }) {
  const [rev, setRev] = useState(0);
  const kindsLoad = useLoad("relation-kinds", getRelationKinds);
  const list = usePagedList(`relations:${self.type}:${self.id}:${rev}`, (cursor) =>
    listItemRelations(self.type, self.id, cursor)
  );
  const changed = () => setRev((r) => r + 1);
  const kinds = kindsLoad.result?.ok ? kindsLoad.result.data : null;
  const groups = kinds
    ? groupRelations(kinds, list.items)
    : list.items.length
      ? [{ key: "all", label: "관계", items: list.items }]
      : [];

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold text-stone-900">연결된 자료·문서</h2>
      {kindsLoad.result && !kindsLoad.result.ok && (
        <ErrorNotice failure={kindsLoad.result.failure} prefix="관계 유형 목록을 불러오지 못해 관계를 원래 이름으로만 보여 주며 추가·수정할 수 없습니다." />
      )}
      {list.failure && <ErrorNotice failure={list.failure} prefix="관계를 불러오지 못했습니다." />}
      {list.loaded && !list.failure && list.items.length === 0 && <p className="text-sm text-stone-500">아직 연결된 항목이 없습니다.</p>}
      {groups.map((g) => (
        <div key={g.key} role="group" aria-label={g.label} className="space-y-1">
          <h3 className="text-sm font-medium text-stone-900">
            {g.label} <span className="text-xs font-normal text-stone-500">{g.items.length}</span>
          </h3>
          <ul className="divide-y divide-stone-100 rounded-lg ring-1 ring-stone-200">
            {g.items.map((rel) => (
              <RelationRow key={`${rel.id}:${rel.version}`} rel={rel} self={self} kinds={kinds} onChanged={changed} />
            ))}
          </ul>
        </div>
      ))}
      {list.nextCursor && !list.loading && (
        <button onClick={() => void list.loadMore()} className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
          더 보기
        </button>
      )}
      {editable && kinds && <AddRelationForm self={self} kinds={kinds} onAdded={changed} />}
    </section>
  );
}
