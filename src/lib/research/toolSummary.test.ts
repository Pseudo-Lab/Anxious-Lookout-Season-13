import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ToolCall } from "@/components/research/session/SessionView";
import type { ToolCallItem } from "./types";

describe("#104 collapsed tool details", () => {
  it.each([
    ["inProgress", "명령 실행 중"], ["completed", "명령 실행함"], ["failed", "명령 실패"],
    ["rejected", "명령 거부됨"], ["declined", "명령 거부됨"], ["interrupted", "명령 중지됨"],
  ])("keeps %s visible without command/output/exit/duration", (status, summary) => {
    const item: ToolCallItem = { id: "tool", type: "tool_call", name: "shell", status,
      input: { command: "private-command-104", cwd: "/private/path" },
      output: { stdout: "private-output-104", stderr: "private-error-104", exitCode: 7, durationMs: 12345, combinedOutput: true } };
    const html = renderToStaticMarkup(createElement(ToolCall, { item }));
    expect(html).toContain(summary);
    expect(html).toContain('aria-expanded="false"');
    for (const hidden of ["private-command", "/private/path", "private-output", "private-error", "종료 코드", "실행 시간", "<pre"]) expect(html).not.toContain(hidden);
  });
  it("hides file paths and diff while preserving interruption", () => {
    const item: ToolCallItem = { id: "patch", type: "tool_call", name: "apply_patch", status: "completed", turnStatus: "interrupted",
      input: { changes: [{ path: "private-file.md", kind: "update", diff: "+private-diff" }] }, output: null };
    const html = renderToStaticMarkup(createElement(ToolCall, { item }));
    expect(html).toContain("파일 변경함");
    expect(html).toContain("중지됨");
    expect(html).not.toContain("private-file");
    expect(html).not.toContain("private-diff");
  });
});
