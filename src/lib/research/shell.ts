// #23 shell 명령 실행 표시(api-contracts/issue23-shell-display.md). 서버가 기록한 실행기 결과를 그대로 읽어 안내만 만든다.
// 시간 상한·출력 상한은 운영 설정이라 숫자를 고정하지 않고, 결과에 있는 wallSeconds만 쓴다.
// 신호만으로 자원 초과(OOM 등)를 추정하지 않는다.

import type { ToolCallItem } from "@/lib/research/types";
import { own } from "@/lib/research/own";

export const SHELL_TOOL = "shell";

export interface ShellView {
  command: string | null;
  cwd: string | null;
  stdout: string | null;
  stderr: string | null;
  exitCode: number | null;
  truncated: boolean;
  wallSeconds: number | null;
  code: string | null;
}

const obj = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown) => (typeof v === "string" ? v : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

export function shellView(item: ToolCallItem): ShellView {
  const input = obj(item.input);
  const output = obj(item.output);
  return {
    command: str(input?.command),
    cwd: str(input?.cwd),
    stdout: str(output?.stdout),
    stderr: str(output?.stderr),
    exitCode: num(output?.exitCode),
    truncated: output?.truncated === true,
    wallSeconds: num(output?.wallSeconds),
    code: item.error?.code ?? str(obj(output?.error)?.code),
  };
}

export const SHELL_STATUS_LABELS: Record<string, string> = {
  completed: "완료",
  failed: "실패",
  timed_out: "시간 초과",
  rejected: "거부됨",
};

const CODE_TEXT: Record<string, string> = {
  tool_not_allowed: "이 질문을 보낸 범위(공개 글 읽기 등)에서는 명령을 실행할 수 없어 서버가 실행하지 않았습니다.",
  shell_invalid_request: "명령 요청 형식이 올바르지 않아 실행하지 않았습니다.",
  shell_busy: "다른 명령이 실행 중이라 실행하지 않았습니다. 명령은 한 번에 하나씩 실행됩니다.",
  shell_unavailable: "명령 실행 환경에 연결하지 못해 결과를 받지 못했습니다.",
  shell_cancelled: "명령 실행이 중간에 취소되었습니다. 출력은 멈춘 시점까지입니다.",
};

/** 성공이 아닌 결과의 안내(성공이면 null). */
export function shellNote(status: string, v: ShellView): string | null {
  if (status === "timed_out" || v.code === "shell_timeout") {
    const limit = v.wallSeconds !== null ? `(${v.wallSeconds}초)` : "";
    return `시간 상한${limit}을 넘어 명령을 멈췄습니다. 출력은 멈춘 시점까지입니다. 작업을 나누거나 더 짧게 끝나는 방법으로 다시 요청하세요.`;
  }
  const known = v.code ? own(CODE_TEXT, v.code) : undefined;
  if (known) return known;
  if (status === "rejected") return "서버가 이 명령을 실행하지 않았습니다.";
  if (status === "failed") {
    return v.exitCode !== null ? `명령이 종료 코드 ${v.exitCode}로 끝났습니다(실패).` : "명령이 정상적으로 끝나지 않았습니다.";
  }
  return null;
}
