import { describe, expect, it } from "vitest";
import { excerpt, longDate } from "./format";

describe("excerpt", () => {
  it("keeps short text whole", () => {
    expect(excerpt("  짧은   글입니다.  ")).toBe("짧은 글입니다.");
  });

  it("takes whole sentences within the limit", () => {
    const text = "첫 문장입니다. 두 번째 문장입니다! 세 번째는 아주 길어서 넘칩니다.";
    expect(excerpt(text, 25)).toBe("첫 문장입니다. 두 번째 문장입니다!");
  });

  it("does not split at a dot inside a word", () => {
    const text = "버전 1.2를 소개합니다. 다음 문장은 길게 이어지는 내용입니다.";
    expect(excerpt(text, 20)).toBe("버전 1.2를 소개합니다.");
  });

  it("keeps a long first sentence up to the hard cap without cutting", () => {
    const first = "가".repeat(30) + " " + "나".repeat(30);
    expect(excerpt(first + ". 다음.", 20, 100)).toBe(first + ".");
  });

  it("cuts at the last space only beyond the hard cap", () => {
    const text = "가나다 ".repeat(50).trim();
    const out = excerpt(text, 20, 30);
    expect(out.endsWith("가나다…")).toBe(true);
    expect(Array.from(out).length).toBeLessThanOrEqual(31);
  });
});

describe("longDate", () => {
  it("formats a spoken date and passes invalid input through", () => {
    expect(longDate("2026-11-18T03:00:00Z")).toMatch(/^2026년 11월 1[78]일$/);
    expect(longDate("nope")).toBe("nope");
  });
});
