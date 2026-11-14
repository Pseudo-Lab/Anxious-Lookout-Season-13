// #90 Codex 답변 실시간 스트림(api-contracts/issue90-chat-stream.md v1.1)의 순수 로직: SSE 프레임 해석, 턴 상태 적용, 스트리밍 Markdown 분할.
// 연결(fetch-stream)·재연결·화면 반영은 useChatStream이 한다. 스트림은 화면용 보조 채널이고 확정 기록은 언제나 GET /sessions/{id}다.

export interface StreamFrame {
  /** 이 프레임의 id(턴 로그 이벤트와 snapshot.end에만). 없으면 null. */
  id: string | null;
  event: string;
  data: string;
}

/** text/event-stream 해석기. 조각난 입력을 받아 완성된 프레임만 돌려준다. 주석(`:`)은 버린다. */
export class SseParser {
  private buf = "";
  private id: string | null = null;
  private event = "";
  private data: string[] = [];

  push(chunk: string): StreamFrame[] {
    this.buf += chunk;
    const out: StreamFrame[] = [];
    for (;;) {
      const m = /\r\n|\r|\n/.exec(this.buf);
      if (!m) break;
      // 조각 끝의 \r 뒤에 \n이 올 수 있으므로 기다린다(빈 줄로 잘못 읽지 않게).
      if (m[0] === "\r" && m.index === this.buf.length - 1) break;
      const line = this.buf.slice(0, m.index);
      this.buf = this.buf.slice(m.index + m[0].length);
      if (line === "") {
        if (this.data.length > 0) out.push({ id: this.id, event: this.event || "message", data: this.data.join("\n") });
        this.id = null;
        this.event = "";
        this.data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") this.data.push(value);
      else if (field === "event") this.event = value;
      else if (field === "id" && !value.includes("\0")) this.id = value;
    }
    return out;
  }
}

export interface LiveItem {
  itemId: string;
  /** agentMessage | commandExecution | fileChange | mcpToolCall | dynamicToolCall (모르는 값도 그대로) */
  kind: string;
  /** approval | inProgress | completed | failed | declined ... (서버 값 그대로) */
  status: string;
  text: string;
  command?: string;
  changes?: unknown;
  /** 명령 출력의 최근 조각(선택 표시). 전체 출력은 완료 뒤 기록에서 본다. */
  output?: { text: string; truncated: boolean };
}

export interface TurnOutcome {
  state: string;
  turnStatus: string;
  errorCode: string | null;
}

export interface LiveTurn {
  requestId: string;
  items: LiveItem[];
  running: boolean;
  /** stream.stopped 사유(이후 item.*는 오지 않는다). */
  stopped: string | null;
  outcome: TurnOutcome | null;
}

export interface StreamState {
  /** 마지막으로 받은 id(R:seq). 메모리에만 둔다(계약 front 의무 1). */
  cursor: string | null;
  turn: LiveTurn | null;
  /** 받는 중인 snapshot(snapshot.end 전). 끊기면 버린다. */
  snapshot: LiveTurn | null;
}

export type StreamEffect =
  // 확정 신호: GET /sessions/{id} 1회
  | { type: "refresh" }
  | { type: "runner"; status: Record<string, unknown> }
  | { type: "idle" }
  // 진행 중인 턴을 확인했다(중지 재요청 계기)
  | { type: "turnRunning"; requestId: string };

export const EMPTY_STREAM: StreamState = { cursor: null, turn: null, snapshot: null };

// 명령 출력 조각은 계약상 항목당 16 KiB까지다. 화면에도 그 이상 쌓지 않는다.
const OUTPUT_KEEP = 16 * 1024;

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);

function newTurn(requestId: string, running: boolean): LiveTurn {
  return { requestId, items: [], running, stopped: null, outcome: null };
}

function withItem(turn: LiveTurn, itemId: string, change: (item: LiveItem) => LiveItem): LiveTurn {
  const index = turn.items.findIndex((i) => i.itemId === itemId);
  if (index === -1) return turn;
  const items = turn.items.slice();
  items[index] = change(items[index]);
  return { ...turn, items };
}

function startItem(turn: LiveTurn, d: Obj, status: string): LiveTurn {
  const itemId = str(d.itemId);
  if (!itemId || turn.items.some((i) => i.itemId === itemId)) return turn;
  const item: LiveItem = { itemId, kind: str(d.kind) ?? "unknown", status: str(d.status) ?? status, text: "" };
  const command = str(d.command);
  if (command !== null) item.command = command;
  if (d.changes !== undefined) item.changes = d.changes;
  return { ...turn, items: [...turn.items, item] };
}

function outcomeOf(d: Obj): TurnOutcome {
  return { state: str(d.state) ?? "idle", turnStatus: str(d.turnStatus) ?? "completed", errorCode: str(d.errorCode) };
}

/** 받다 만 snapshot을 버린다(연결이 끊겼을 때). cursor는 그 이전 값으로 남는다. */
export function dropPartial(state: StreamState): StreamState {
  return state.snapshot ? { ...state, snapshot: null } : state;
}

/**
 * 프레임 하나를 적용한다(순수 함수). 모르는 type·필드는 무시한다. data가 JSON 객체가 아니면 그 프레임은 버린다.
 * id가 있는 프레임을 적용하면 cursor를 그 id로 옮긴다.
 */
export function applyFrame(state: StreamState, frame: StreamFrame): { state: StreamState; effects: StreamEffect[] } {
  let data: unknown;
  try {
    data = JSON.parse(frame.data);
  } catch {
    return { state, effects: [] };
  }
  if (!isObj(data)) return { state, effects: [] };
  const d = data;
  const effects: StreamEffect[] = [];
  let next: StreamState = state;
  const requestId = str(d.requestId);

  switch (frame.event) {
    case "runner": {
      effects.push({ type: "runner", status: d });
      // runner가 꺼지면 턴 이벤트가 더 오지 않는다(확정 신호).
      if (d.available === false && state.turn?.running) {
        next = { ...state, turn: { ...state.turn, running: false } };
        effects.push({ type: "refresh" });
      }
      break;
    }
    case "idle": {
      effects.push({ type: "idle" });
      // 진행 중이라고 보던 턴이 없으면(끊긴 사이 끝남) 기록으로 확인한다.
      if (state.turn?.running) {
        next = { ...state, turn: { ...state.turn, running: false } };
        effects.push({ type: "refresh" });
      }
      break;
    }
    case "sync": {
      effects.push({ type: "refresh" });
      const reason = str(d.reason);
      // overflow·mismatch 뒤에는 서버가 닫는다. 다시 붙을 때는 cursor 없이.
      next = { ...state, snapshot: null, cursor: reason === "overflow" || reason === "mismatch" ? null : state.cursor };
      if (next.turn?.running) next = { ...next, turn: { ...next.turn, running: false } };
      break;
    }
    case "snapshot.begin": {
      if (!requestId) break;
      const snap = newTurn(requestId, str(d.turnState) !== "completed");
      if (typeof d.stopped === "string") snap.stopped = d.stopped;
      else if (d.stopped === true) snap.stopped = "stopped";
      next = { ...state, snapshot: snap };
      break;
    }
    case "snapshot.item": {
      const snap = state.snapshot;
      const itemId = str(d.itemId);
      if (!snap || !itemId || requestId !== snap.requestId) break;
      if (typeof d.kind === "string") {
        next = { ...state, snapshot: startItem(snap, d, "inProgress") };
      } else {
        const text = str(d.text);
        if (text !== null) next = { ...state, snapshot: withItem(snap, itemId, (i) => ({ ...i, text: i.text + text })) };
      }
      break;
    }
    case "snapshot.end": {
      const snap = state.snapshot;
      if (!snap || requestId !== snap.requestId) {
        next = { ...state, snapshot: null };
        break;
      }
      next = { cursor: frame.id ?? state.cursor, turn: snap, snapshot: null };
      if (snap.running) effects.push({ type: "turnRunning", requestId: snap.requestId });
      else effects.push({ type: "refresh" });
      return { state: next, effects };
    }
    case "turn.completed": {
      if (!requestId) break;
      // snapshot 안(id 없음): 이미 끝난 턴. snapshot.end에서 확정한다.
      if (state.snapshot && frame.id === null && state.snapshot.requestId === requestId) {
        next = { ...state, snapshot: { ...state.snapshot, running: false, outcome: outcomeOf(d) } };
        break;
      }
      const base = state.turn?.requestId === requestId ? state.turn : newTurn(requestId, false);
      next = { ...state, turn: { ...base, running: false, outcome: outcomeOf(d) } };
      effects.push({ type: "refresh" });
      break;
    }
    case "turn.started": {
      if (!requestId) break;
      if (state.turn?.requestId !== requestId) next = { ...state, turn: newTurn(requestId, true) };
      else if (!state.turn.running && !state.turn.outcome) next = { ...state, turn: { ...state.turn, running: true } };
      effects.push({ type: "turnRunning", requestId });
      break;
    }
    case "turn.discard": {
      if (!requestId || state.turn?.requestId !== requestId) break;
      next = { ...state, turn: { ...state.turn, items: [] } };
      break;
    }
    case "stream.stopped": {
      if (!requestId || state.turn?.requestId !== requestId) break;
      next = { ...state, turn: { ...state.turn, stopped: str(d.reason) ?? "stopped" } };
      break;
    }
    case "item.started":
    case "item.status":
    case "item.delta":
    case "item.output":
    case "item.completed": {
      const turn = state.turn;
      const itemId = str(d.itemId);
      if (!turn || !itemId || requestId !== turn.requestId || turn.stopped) break;
      let changed = turn;
      if (frame.event === "item.started") changed = startItem(turn, d, "inProgress");
      else if (frame.event === "item.delta") {
        const text = str(d.text);
        if (text !== null) changed = withItem(turn, itemId, (i) => ({ ...i, text: i.text + text }));
      } else if (frame.event === "item.output") {
        const text = str(d.text);
        if (text !== null)
          changed = withItem(turn, itemId, (i) => {
            const joined = (i.output?.text ?? "") + text;
            return {
              ...i,
              output: { text: joined.length > OUTPUT_KEEP ? joined.slice(-OUTPUT_KEEP) : joined, truncated: d.truncated === true || joined.length > OUTPUT_KEEP || (i.output?.truncated ?? false) },
            };
          });
      } else {
        const status = str(d.status);
        if (status !== null) changed = withItem(turn, itemId, (i) => ({ ...i, status }));
      }
      next = { ...state, turn: changed };
      break;
    }
    default:
      return { state, effects: [] };
  }
  if (frame.id !== null && !state.snapshot) next = { ...next, cursor: frame.id };
  return { state: next, effects };
}

// ---- 스트리밍 Markdown ----

const FENCE = /^ {0,3}(`{3,}|~{3,})/;

// 빈 줄 뒤에도 앞 블록에 이어질 수 있는 줄의 종류. 같은 종류가 이어질 때만(느슨한 목록·여러 문단 인용·목록 항목의 들여쓴 연속) 나누지 않는다.
function lineKind(line: string): "list" | "quote" | "table" | null {
  if (/^\s/.test(line) || /^([-*+]|\d{1,9}[.)])( |$)/.test(line)) return "list";
  if (line.startsWith(">")) return "quote";
  if (line.startsWith("|")) return "table";
  return null;
}

/**
 * 받는 중인 답변을 독립 블록으로 나눈다. 끝난 블록은 다시 그리지 않고(메모) 마지막 블록만 다시 그리기 위해서다.
 * 코드 블록 안이나, 빈 줄 뒤에 같은 종류(목록·목록의 들여쓴 연속·인용·표)가 이어지는 곳에서는 나누지 않는다.
 * 마지막 블록의 코드 블록이 아직 닫히지 않았으면 화면용으로 닫아 준다(본문은 바꾸지 않는다).
 */
export function streamingBlocks(text: string): string[] {
  const lines = text.split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  let fence: string | null = null;
  let blank = false;
  let lastText = "";
  for (const line of lines) {
    const m = FENCE.exec(line);
    if (fence) {
      current.push(line);
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length && line.trim() === m[1]) fence = null;
      continue;
    }
    if (line.trim() === "") {
      current.push(line);
      blank = true;
      continue;
    }
    const kind = lineKind(line);
    if (blank && lastText !== "" && (kind === null || kind !== lineKind(lastText))) {
      blocks.push(current.join("\n"));
      current = [];
    }
    blank = false;
    lastText = line;
    current.push(line);
    if (m) fence = m[1];
  }
  let last = current.join("\n");
  if (fence) last += `\n${fence}`;
  if (last !== "" || blocks.length === 0) blocks.push(last);
  return blocks;
}
