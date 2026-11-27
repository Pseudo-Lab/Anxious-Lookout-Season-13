import { describe, expect, it } from "vitest";
import { groupRelations, kindChoices, parseRelationKinds, relationLabel, type RelationKinds } from "@/lib/research/relationKinds";
import type { Relation } from "@/lib/research/types";

const raw = {
  items: [
    { key: "supports", label: "지지함", inverseLabel: "지지받음", polarity: "positive", directed: true, cito: ["supports"] },
    { key: "refutes", label: "반박함", inverseLabel: "반박됨", polarity: "negative", directed: true, cito: ["refutes"] },
    { key: "same_topic", label: "같은 주제", inverseLabel: "같은 주제", polarity: "neutral", directed: false, cito: [] },
  ],
  polarityGroups: [
    { key: "positive", label: "지지·확장" },
    { key: "negative", label: "반박·이견" },
    { key: "neutral", label: "중립" },
  ],
  legacyLabel: "기타",
};
const kinds = parseRelationKinds(raw) as RelationKinds;

const A = { type: "material" as const, id: "a", title: "A" };
const B = { type: "material" as const, id: "b", title: "B" };
function rel(id: string, kind: string, patch: Partial<Relation> = {}): Relation {
  return {
    id,
    source: A,
    target: B,
    kind,
    kindKnown: kinds.items.some((k) => k.key === kind),
    description: "why",
    directed: kind !== "same_topic",
    evidence: [],
    version: 1,
    createdAt: "",
    updatedAt: "",
    ...patch,
  };
}

describe("parseRelationKinds", () => {
  it("accepts the contract shape", () => {
    expect(kinds.items.map((k) => k.key)).toEqual(["supports", "refutes", "same_topic"]);
  });
  it("rejects bad polarity or empty vocab", () => {
    expect(parseRelationKinds({ ...raw, items: [{ ...raw.items[0], polarity: "odd" }] })).toBeNull();
    expect(parseRelationKinds({ ...raw, items: [] })).toBeNull();
  });
});

describe("relationLabel", () => {
  it("reads forward from source and inverse from target", () => {
    expect(relationLabel(kinds, rel("1", "supports", { direction: "outgoing" }), A)).toBe("지지함");
    expect(relationLabel(kinds, rel("1", "supports", { direction: "incoming" }), B)).toBe("지지받음");
    // direction 없이도 source 여부로 판단
    expect(relationLabel(kinds, rel("1", "supports"), B)).toBe("지지받음");
  });
  it("shows legacy free text as 기타 (원래 문자열)", () => {
    expect(relationLabel(kinds, rel("1", "근거", { kindKnown: false }), A)).toBe("기타 (근거)");
    // #119 J3: 영어 내부 이름은 읽는 이름으로
    expect(relationLabel(kinds, rel("1", "reference", { kindKnown: false }), A)).toBe("기타 (참고)");
    expect(relationLabel(kinds, rel("1", "Relates-To", { kindKnown: false }), A)).toBe("기타 (관련)");
  });
});

describe("groupRelations", () => {
  it("groups by polarity in server order, legacy last, skipping empty groups", () => {
    const groups = groupRelations(kinds, [
      rel("1", "same_topic"),
      rel("2", "후속", { kindKnown: false }),
      rel("3", "supports"),
      // 어휘에 없는 key인데 kindKnown이 true인 경우도 기타로 둔다
      rel("4", "extends", { kindKnown: true }),
    ]);
    expect(groups.map((g) => [g.key, g.items.map((r) => r.id)])).toEqual([
      ["positive", ["3"]],
      ["neutral", ["1"]],
      ["legacy", ["2", "4"]],
    ]);
    expect(groups.at(-1)?.label).toBe("기타 (유형 미지정)");
  });
});

describe("kindChoices", () => {
  it("limits document↔material to directed kinds and edits to the same direction", () => {
    expect(kindChoices(kinds, { mixed: true }).map((k) => k.key)).toEqual(["supports", "refutes"]);
    expect(kindChoices(kinds, { directed: false }).map((k) => k.key)).toEqual(["same_topic"]);
    expect(kindChoices(kinds).length).toBe(3);
  });
});
