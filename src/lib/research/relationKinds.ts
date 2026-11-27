// #105 관계 어휘(api-contracts/issue105-research-wiki-v1.1.md 2절). 어휘는 서버
// `GET /api/research/relation-kinds` 응답만 따른다(front에 사본을 두지 않는다, PM 결정).

import type { Endpoint, Relation } from "@/lib/research/types";

export type Polarity = "positive" | "negative" | "neutral";

export interface RelationKind {
  key: string;
  // source 쪽에서 읽을 때
  label: string;
  // target 쪽에서 읽을 때(방향 없는 kind는 label과 같다)
  inverseLabel: string;
  polarity: Polarity;
  directed: boolean;
  cito: string[];
}

export interface RelationKinds {
  items: RelationKind[];
  polarityGroups: { key: Polarity; label: string }[];
  legacyLabel: string;
}

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isText = (v: unknown): v is string => typeof v === "string" && v !== "";
const isPolarity = (v: unknown): v is Polarity => v === "positive" || v === "negative" || v === "neutral";

export function parseRelationKinds(v: unknown): RelationKinds | null {
  if (!isObj(v) || !Array.isArray(v.items) || !Array.isArray(v.polarityGroups) || !isText(v.legacyLabel)) return null;
  const items: RelationKind[] = [];
  for (const k of v.items) {
    if (!isObj(k) || !isText(k.key) || !isText(k.label) || !isText(k.inverseLabel) || !isPolarity(k.polarity) || typeof k.directed !== "boolean")
      return null;
    if (!Array.isArray(k.cito) || !k.cito.every((c) => typeof c === "string")) return null;
    items.push({ key: k.key, label: k.label, inverseLabel: k.inverseLabel, polarity: k.polarity, directed: k.directed, cito: k.cito as string[] });
  }
  const groups: RelationKinds["polarityGroups"] = [];
  for (const g of v.polarityGroups) {
    if (!isObj(g) || !isPolarity(g.key) || !isText(g.label)) return null;
    groups.push({ key: g.key, label: g.label });
  }
  if (!items.length) return null;
  return { items, polarityGroups: groups, legacyLabel: v.legacyLabel };
}

/** 어휘에 있는 kind. 기존 자유 텍스트(kindKnown:false)나 이 화면이 받은 어휘에 없는 key면 null. */
export function kindOf(kinds: RelationKinds, rel: Pick<Relation, "kind" | "kindKnown">): RelationKind | null {
  if (!rel.kindKnown) return null;
  return kinds.items.find((k) => k.key === rel.kind) ?? null;
}

/** 이 항목이 관계의 source 쪽인가. 방향 없는 관계는 source로 본다(label을 쓴다). */
export function readsAsSource(rel: Relation, self: Pick<Endpoint, "type" | "id">): boolean {
  if (rel.direction) return rel.direction !== "incoming";
  if (!rel.directed) return true;
  return rel.source.type === self.type && rel.source.id === self.id;
}

// #119 J3(D6): 예전 자유 텍스트 유형 중 영어 내부 이름으로 남은 것은 읽는 이름으로 보인다(저장 값은 바꾸지 않는다).
const LEGACY_NAMES: Record<string, string> = {
  reference: "참고",
  references: "참고",
  related: "관련",
  relates_to: "관련",
  source: "출처",
  cites: "인용",
  supports: "뒷받침",
  contradicts: "반박",
  extends: "확장",
  example: "예시",
  background: "배경",
};

/** 예전 유형의 읽는 이름(모르는 영어 이름·자유 텍스트는 그대로). */
export function legacyName(kind: string): string {
  return LEGACY_NAMES[kind.trim().toLowerCase().replace(/[\s-]+/g, "_")] ?? kind;
}

/** 이 항목 쪽에서 읽는 관계 문구. 기존 자유 텍스트는 ‘기타 (읽는 이름)’. */
export function relationLabel(kinds: RelationKinds, rel: Relation, self: Pick<Endpoint, "type" | "id">): string {
  const kind = kindOf(kinds, rel);
  if (!kind) return `${kinds.legacyLabel} (${legacyName(rel.kind)})`;
  return readsAsSource(rel, self) ? kind.label : kind.inverseLabel;
}

export interface RelationGroup {
  // 극성 key 또는 "legacy"
  key: Polarity | "legacy";
  label: string;
  items: Relation[];
}

/** 극성별 묶음(서버가 준 순서), 그 뒤에 기타(유형 미지정). 빈 묶음은 뺀다. 묶음 안 순서는 받은 순서다. */
export function groupRelations(kinds: RelationKinds, rels: Relation[]): RelationGroup[] {
  const groups: RelationGroup[] = kinds.polarityGroups.map((g) => ({ key: g.key, label: g.label, items: [] }));
  const legacy: RelationGroup = { key: "legacy", label: `${kinds.legacyLabel} (유형 미지정)`, items: [] };
  for (const rel of rels) {
    const kind = kindOf(kinds, rel);
    const group = kind && groups.find((g) => g.key === kind.polarity);
    (group || legacy).items.push(rel);
  }
  return [...groups, legacy].filter((g) => g.items.length > 0);
}

/**
 * 고를 수 있는 kind.
 * - mixed: 문서↔자료 관계(항상 문서가 source, 방향 있는 kind만)
 * - directed: 기존 관계 수정(방향을 바꿀 수 없어 그 관계의 directed와 같은 kind만)
 */
export function kindChoices(kinds: RelationKinds, opts: { mixed?: boolean; directed?: boolean } = {}): RelationKind[] {
  return kinds.items.filter((k) => (opts.mixed ? k.directed : true) && (opts.directed === undefined || k.directed === opts.directed));
}
