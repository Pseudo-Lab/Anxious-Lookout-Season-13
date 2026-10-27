// #68 api-contracts/issue68-native-shell-history.md: native shell·apply_patch 투영을 읽고, 옛 #23 실행기 기록 표시는 그대로 둔다.
import { describe, expect, it } from "vitest";
import type { ToolCallItem } from "./types";
import {
  formatDuration,
  patchChanges,
  patchKindLabel,
  patchNote,
  patchSummary,
  shellCwdLabel,
  shellEmptyOutput,
  shellNote,
  shellOutputLabel,
  shellView,
} from "./shell";

const tool = (name: string, input: unknown, output: unknown, status: string, code?: string): ToolCallItem => ({
  id: "i1",
  type: "tool_call",
  name,
  input,
  output,
  status,
  ...(code ? { error: { code } } : {}),
});

const native = (status: string, stdout: string | null, extra: Record<string, unknown> = {}, code?: string) =>
  tool(
    "shell",
    { command: "ls", cwd: "." },
    { status, stdout, stderr: null, combinedOutput: true, exitCode: status === "completed" ? 0 : null, durationMs: 1530, ...extra },
    status,
    code
  );

describe("native shell (#68)", () => {
  it("reads combined output and duration", () => {
    const v = shellView(native("completed", "out\nerr\n"));
    expect(v.combined).toBe(true);
    expect(v.stdout).toBe("out\nerr\n");
    expect(v.durationMs).toBe(1530);
    expect(shellOutputLabel(v)).toBe("출력 (표준 출력·오류 합침)");
    expect(shellEmptyOutput("completed", v)).toBeNull();
    expect(shellNote("completed", v)).toBeNull();
  });

  it("treats null and empty output as no output", () => {
    expect(shellEmptyOutput("completed", shellView(native("completed", null)))).toBe("출력이 없습니다.");
    expect(shellEmptyOutput("failed", shellView(native("failed", "")))).toBe("출력이 없습니다.");
    expect(shellEmptyOutput("inProgress", shellView(native("inProgress", null)))).toBe("아직 기록된 출력이 없습니다.");
  });

  it("declined is explained by the authorization check, without an output note", () => {
    const item = native("rejected", null, {}, "shell_declined");
    const v = shellView(item);
    expect(v.code).toBe("shell_declined");
    expect(shellNote("rejected", v)).toContain("권한 확인에서 거부되어");
    expect(shellEmptyOutput("rejected", v)).toBeNull();
  });

  it("a native failure without exit code (e.g. timed out) is not shown as an executor timeout", () => {
    expect(shellNote("failed", shellView(native("failed", "partial")))).toBe("명령이 정상적으로 끝나지 않았습니다.");
  });

  it("shows an absolute cwd outside the workspace as a directory", () => {
    expect(shellCwdLabel(".")).toBe("작업 공간 .");
    expect(shellCwdLabel("notes")).toBe("작업 공간 notes");
    expect(shellCwdLabel(null)).toBe("작업 공간 .");
    expect(shellCwdLabel("/tmp")).toBe("작업 디렉터리 /tmp");
  });

  it("formats durations", () => {
    expect(formatDuration(12)).toBe("1초 미만");
    expect(formatDuration(1530)).toBe("1.5초");
    expect(formatDuration(42_400)).toBe("42초");
    expect(formatDuration(125_000)).toBe("2분 5초");
    expect(formatDuration(120_000)).toBe("2분");
  });
});

describe("old #23 executor records are unchanged", () => {
  const old = tool(
    "shell",
    { command: "ls", cwd: "notes" },
    { status: "completed", stdout: "", stderr: "", exitCode: 0, truncated: false, durationMs: 12, wallSeconds: 30 },
    "completed"
  );

  it("keeps the stdout label, ignores durationMs and adds no empty-output note", () => {
    const v = shellView(old);
    expect(v.combined).toBe(false);
    expect(v.durationMs).toBeNull();
    expect(shellOutputLabel(v)).toBe("표준 출력");
    expect(shellEmptyOutput("completed", v)).toBeNull();
  });

  it("keeps the executor timeout note", () => {
    const t = tool("shell", { command: "x" }, { status: "timed_out", wallSeconds: 30, error: { code: "shell_timeout" } }, "timed_out");
    expect(shellNote("timed_out", shellView(t))).toContain("시간 상한(30초)");
  });
});

describe("apply_patch (#68)", () => {
  const changes = [
    { path: "notes/a.md", kind: "update", movePath: null, diff: "@@ -1 +1 @@\n-a\n+b\n" },
    { path: "notes/b.md", kind: "add", movePath: null, diff: "new\n" },
    { path: "old.md", kind: "update", movePath: "new.md", diff: "" },
    { path: "gone.md", kind: "delete", movePath: null, diff: null },
  ];

  it("reads per-change paths, kinds and diffs", () => {
    const list = patchChanges(tool("apply_patch", { changes }, { status: "completed" }, "completed"));
    expect(list).toHaveLength(4);
    expect(list?.[0]).toEqual({ path: "notes/a.md", kind: "update", movePath: null, diff: "@@ -1 +1 @@\n-a\n+b\n" });
    expect(list?.map(patchKindLabel)).toEqual(["수정", "추가", "이름 바꿈", "삭제"]);
    expect(patchSummary(list ?? [])).toBe("notes/a.md 외 3개");
    expect(patchSummary(list?.slice(1, 2) ?? [])).toBe("notes/b.md");
  });

  it("falls back to the generic tool view when changes is not a list", () => {
    expect(patchChanges(tool("apply_patch", { patch: "raw" }, null, "completed"))).toBeNull();
    expect(patchChanges(tool("apply_patch", null, null, "completed"))).toBeNull();
  });

  it("tolerates malformed entries and unknown kinds", () => {
    const list = patchChanges(tool("apply_patch", { changes: [null, { path: 3, kind: "rename" }] }, null, "completed")) ?? [];
    expect(list[0]).toEqual({ path: null, kind: null, movePath: null, diff: null });
    expect(patchKindLabel(list[0])).toBe("변경");
    expect(patchKindLabel(list[1])).toBe("rename");
    expect(patchKindLabel({ path: null, kind: "__proto__", movePath: null, diff: null })).toBe("__proto__");
  });

  it("explains declined and failed changes", () => {
    expect(patchNote("rejected", "shell_declined")).toContain("권한 확인에서 거부되어");
    expect(patchNote("rejected", undefined)).toBe("서버가 이 파일 변경을 적용하지 않았습니다.");
    expect(patchNote("failed", undefined)).toBe("파일 변경을 적용하지 못했습니다.");
    expect(patchNote("completed", undefined)).toBeNull();
    expect(patchNote("inProgress", undefined)).toBeNull();
  });
});
