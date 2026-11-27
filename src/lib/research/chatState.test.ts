import { describe, expect, it } from "vitest";
import { titleFromMessage } from "./chatState";

describe("titleFromMessage (#119 N1)", () => {
  it("uses the first non-empty line, collapsed", () => {
    expect(titleFromMessage("\n  삼성전자   실적 발표\n두 번째 줄")).toBe("삼성전자 실적 발표");
  });
  it("caps at 60 characters including the ellipsis", () => {
    const out = titleFromMessage("가".repeat(80));
    expect(Array.from(out)).toHaveLength(60);
    expect(out.endsWith("…")).toBe(true);
  });
  it("falls back when empty", () => {
    expect(titleFromMessage("   \n ")).toBe("새 대화");
  });
});
