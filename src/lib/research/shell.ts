// #23 shell 명령 실행 표시(api-contracts/issue23-shell-display.md). 서버가 기록한 실행기 결과를 그대로 읽어 안내만 만든다.
// 시간 상한·출력 상한은 운영 설정이라 숫자를 고정하지 않고, 결과에 있는 wallSeconds만 쓴다.
// 신호만으로 자원 초과(OOM 등)를 추정하지 않는다.
// #68 native shell(api-contracts/issue68-native-shell-history.md): 같은 shell 모양에 표준 출력·오류가 합쳐져 오고
// (combinedOutput), 파일 수정은 apply_patch 항목으로 온다. 옛 대화(#23 실행기 결과)의 표시는 바꾸지 않는다.

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
  // #68 native: stdout에 오류 출력까지 합쳐져 있다. durationMs는 native에서만 읽는다(옛 실행기 결과에도 있을 수 있음).
  combined: boolean;
  durationMs: number | null;
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
    combined: output?.combinedOutput === true,
    durationMs: output?.combinedOutput === true ? num(output?.durationMs) : null,
  };
}

/** 출력 칸 이름. native는 표준 출력과 오류가 한 칸에 합쳐져 있다. */
export const shellOutputLabel = (v: ShellView) => (v.combined ? "출력 (표준 출력·오류 합침)" : "표준 출력");

/** native에서 출력이 null이거나 빈 문자열일 때의 안내(그 밖에는 null). 옛 기록은 기존 표시를 따른다. */
export function shellEmptyOutput(status: string, v: ShellView): string | null {
  if (!v.combined || (v.stdout !== null && v.stdout !== "")) return null;
  if (status === "rejected") return null; // 실행하지 않았다는 안내가 이미 있다.
  return status === "inProgress" ? "아직 기록된 출력이 없습니다." : "출력이 없습니다.";
}

/** 명령 칸 이름. 서버는 작업 공간 안이면 상대 경로, 밖이면 받은 절대 경로를 그대로 준다. */
export const shellCwdLabel = (cwd: string | null) => (cwd?.startsWith("/") ? `작업 디렉터리 ${cwd}` : `작업 공간 ${cwd ?? "."}`);

/** 실행 시간(ms)을 짧게 적는다. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return "1초 미만";
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}초`;
  const m = Math.floor(seconds / 60);
  const rest = Math.round(seconds - m * 60);
  return rest ? `${m}분 ${rest}초` : `${m}분`;
}

export const SHELL_STATUS_LABELS: Record<string, string> = {
  completed: "완료",
  failed: "실패",
  timed_out: "시간 초과",
  rejected: "거부됨",
  inProgress: "실행 중",
};

const CODE_TEXT: Record<string, string> = {
  tool_not_allowed: "이 질문을 보낸 범위(공개 글 읽기 등)에서는 명령을 실행할 수 없어 서버가 실행하지 않았습니다.",
  shell_invalid_request: "명령 요청 형식이 올바르지 않아 실행하지 않았습니다.",
  shell_busy: "다른 명령이 실행 중이라 실행하지 않았습니다. 명령은 한 번에 하나씩 실행됩니다.",
  shell_unavailable: "명령 실행 환경에 연결하지 못해 결과를 받지 못했습니다.",
  shell_cancelled: "명령 실행이 중간에 취소되었습니다. 출력은 멈춘 시점까지입니다.",
  // #68: runner가 명령마다 승인 계정·역할·로그인·질문 범위를 확인해 거부했다.
  shell_declined: "권한 확인에서 거부되어 서버가 이 명령을 실행하지 않았습니다.",
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

// #68 apply_patch(native 파일 수정). input.changes[]마다 경로·종류·diff가 온다(diff는 변경별, app-server 순서).
export const PATCH_TOOL = "apply_patch";

export interface PatchChange {
  path: string | null;
  kind: string | null;
  movePath: string | null;
  diff: string | null;
}

/** input.changes가 배열이 아니면 null(일반 도구처럼 표시). */
export function patchChanges(item: ToolCallItem): PatchChange[] | null {
  const changes = obj(item.input)?.changes;
  if (!Array.isArray(changes)) return null;
  return changes.map((c) => {
    const v = obj(c);
    return { path: str(v?.path), kind: str(v?.kind), movePath: str(v?.movePath), diff: str(v?.diff) };
  });
}

const PATCH_KIND_LABELS: Record<string, string> = { add: "추가", delete: "삭제", update: "수정" };

/** 변경 종류 이름. 이름을 바꾼 수정(movePath)은 "이름 바꿈", 모르는 값은 그대로 보인다. */
export function patchKindLabel(change: PatchChange): string {
  if (change.kind === "update" && change.movePath) return "이름 바꿈";
  return own(PATCH_KIND_LABELS, change.kind) ?? change.kind ?? "변경";
}

/** 머리글 요약: 첫 경로와 나머지 개수. */
export function patchSummary(changes: PatchChange[]): string {
  if (changes.length === 0) return "";
  const first = changes[0].path ?? "(경로 없음)";
  return changes.length === 1 ? first : `${first} 외 ${changes.length - 1}개`;
}

export const PATCH_STATUS_LABELS: Record<string, string> = {
  completed: "완료",
  failed: "실패",
  rejected: "거부됨",
  inProgress: "진행 중",
};

/** 성공이 아닌 파일 변경의 안내(성공·진행 중이면 null). */
export function patchNote(status: string, code: string | null | undefined): string | null {
  if (status === "rejected") {
    return code === "shell_declined"
      ? "권한 확인에서 거부되어 서버가 이 파일 변경을 적용하지 않았습니다."
      : "서버가 이 파일 변경을 적용하지 않았습니다.";
  }
  if (status === "failed") return "파일 변경을 적용하지 못했습니다.";
  return null;
}
