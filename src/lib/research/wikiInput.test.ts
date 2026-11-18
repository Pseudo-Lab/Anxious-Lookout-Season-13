import { describe, expect, it } from "vitest";
import { checkEvidence, readSummary, type EvidenceDraft } from "@/lib/research/wikiInput";

const emoji = (n: number) => "😀".repeat(n);
const row = (patch: Partial<EvidenceDraft>): EvidenceDraft => ({ key: 1, itemId: "a", versionId: "v", locator: "", quote: "", ...patch });

describe("readSummary (code points)", () => {
  it("accepts 300 and rejects 301 code points", () => {
    expect(readSummary(emoji(300))).toEqual({ ok: true, value: emoji(300) });
    expect(readSummary(emoji(301)).ok).toBe(false);
  });
  it("trims, treats blank as null, rejects newlines", () => {
    expect(readSummary("  요약  ")).toEqual({ ok: true, value: "요약" });
    expect(readSummary("   ")).toEqual({ ok: true, value: null });
    expect(readSummary("a\nb").ok).toBe(false);
  });
});

describe("checkEvidence (code points)", () => {
  it("locator 100 ok, 101 rejected", () => {
    expect(checkEvidence([row({ locator: emoji(100) })])).toBeNull();
    expect(checkEvidence([row({ locator: emoji(101) })])).toMatch(/위치는 100자/);
  });
  it("quote 500 ok, 501 rejected", () => {
    expect(checkEvidence([row({ quote: emoji(500) })])).toBeNull();
    expect(checkEvidence([row({ quote: emoji(501) })])).toMatch(/인용문은 500자/);
  });
  it("needs a version and a locator or quote", () => {
    expect(checkEvidence([row({ versionId: "", locator: "p.1" })])).toMatch(/버전/);
    expect(checkEvidence([row({ locator: "  " })])).toMatch(/하나는/);
  });
  it("rejects more than 20 rows and exact duplicates", () => {
    expect(checkEvidence(Array.from({ length: 21 }, (_, i) => row({ key: i, locator: `p.${i}` })))).toMatch(/20개/);
    expect(checkEvidence([row({ key: 1, locator: "p.1" }), row({ key: 2, locator: " p.1 " })])).toMatch(/두 번/);
  });
});
