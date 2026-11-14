// #90 스트림 순수 로직: SSE 해석, 계약 v1.1 프레임 적용(id 규칙·snapshot·확정 신호), 스트리밍 Markdown 분할.
import { describe, expect, it } from "vitest";
import { EMPTY_STREAM, SseParser, applyFrame, dropPartial, streamingBlocks, type StreamFrame, type StreamState } from "@/lib/research/stream";

const R = "11111111-1111-4111-8111-111111111111";
const R2 = "22222222-2222-4222-8222-222222222222";
const f = (event: string, data: object, id: string | null = null): StreamFrame => ({ id, event, data: JSON.stringify(data) });

function run(frames: StreamFrame[], start: StreamState = EMPTY_STREAM) {
  let state = start;
  const effects: string[] = [];
  for (const frame of frames) {
    const r = applyFrame(state, frame);
    state = r.state;
    effects.push(...r.effects.map((e) => e.type));
  }
  return { state, effects };
}

describe("SseParser", () => {
  it("parses frames split across chunks and skips comments", () => {
    const p = new SseParser();
    expect(p.push(": hb\n\nid: a:1\nevent: item.delta\nda")).toEqual([]);
    expect(p.push('ta: {"x":1}\n\n: t 3 9\n\nevent: idle\ndata: {}\r')).toEqual([{ id: "a:1", event: "item.delta", data: '{"x":1}' }]);
    // \r 다음 조각의 \n은 같은 줄바꿈이다.
    expect(p.push("\n\r\n")).toEqual([{ id: null, event: "idle", data: "{}" }]);
  });
  it("joins multiple data lines and resets id per frame", () => {
    const p = new SseParser();
    expect(p.push("id: r:2\ndata: a\ndata: b\n\ndata: c\n\n")).toEqual([
      { id: "r:2", event: "message", data: "a\nb" },
      { id: null, event: "message", data: "c" },
    ]);
  });
});

describe("applyFrame", () => {
  it("builds a live turn from events and advances the cursor only on id frames", () => {
    const { state, effects } = run([
      f("runner", { available: true, reason: null }),
      f("idle", { requestId: null, turnState: null }),
      f("turn.started", { requestId: R }, `${R}:1`),
      f("item.started", { requestId: R, itemId: "m1", kind: "agentMessage" }, `${R}:2`),
      f("item.delta", { requestId: R, itemId: "m1", text: "안녕" }, `${R}:3`),
      f("item.delta", { requestId: R, itemId: "m1", text: "하세요" }, `${R}:4`),
      f("item.started", { requestId: R, itemId: "c1", kind: "commandExecution", command: "ls" }, `${R}:5`),
      f("item.status", { requestId: R, itemId: "c1", status: "approval" }, `${R}:6`),
      f("item.output", { requestId: R, itemId: "c1", text: "a.txt\n", truncated: false, droppedBytes: 0 }, `${R}:7`),
    ]);
    expect(effects).toEqual(["runner", "idle", "turnRunning"]);
    expect(state.cursor).toBe(`${R}:7`);
    expect(state.turn?.running).toBe(true);
    expect(state.turn?.items.map((i) => [i.itemId, i.kind, i.status, i.text])).toEqual([
      ["m1", "agentMessage", "inProgress", "안녕하세요"],
      ["c1", "commandExecution", "approval", ""],
    ]);
    expect(state.turn?.items[1].command).toBe("ls");
    expect(state.turn?.items[1].output).toEqual({ text: "a.txt\n", truncated: false });
  });

  it("turn.completed is a confirmation signal (refresh once)", () => {
    const { state, effects } = run([f("turn.started", { requestId: R }, `${R}:1`), f("turn.completed", { requestId: R, state: "idle", turnStatus: "interrupted", errorCode: null }, `${R}:2`)]);
    expect(effects).toEqual(["turnRunning", "refresh"]);
    expect(state.turn).toMatchObject({ running: false, outcome: { turnStatus: "interrupted" } });
  });

  it("rebuilds the turn from a snapshot and drops previous state for it", () => {
    const before = run([f("turn.started", { requestId: R }, `${R}:1`), f("item.started", { requestId: R, itemId: "old", kind: "agentMessage" }, `${R}:2`)]).state;
    const { state, effects } = run(
      [
        f("snapshot.begin", { requestId: R, turnState: "running", seq: 9 }),
        f("snapshot.item", { requestId: R, itemId: "m1", kind: "agentMessage", status: "inProgress" }),
        f("snapshot.item", { requestId: R, itemId: "m1", text: "앞" }),
        f("snapshot.item", { requestId: R, itemId: "m1", text: "부분" }),
        f("snapshot.end", { requestId: R, seq: 9 }, `${R}:9`),
        f("item.delta", { requestId: R, itemId: "m1", text: "!" }, `${R}:10`),
      ],
      before
    );
    expect(effects).toEqual(["turnRunning"]);
    expect(state.turn?.items.map((i) => [i.itemId, i.text])).toEqual([["m1", "앞부분!"]]);
    expect(state.cursor).toBe(`${R}:10`);
  });

  it("a finished-turn snapshot carries turn.completed without id and confirms on snapshot.end", () => {
    const { state, effects } = run([
      f("snapshot.begin", { requestId: R, turnState: "completed", seq: 4 }),
      f("turn.completed", { requestId: R, state: "idle", turnStatus: "completed", errorCode: null }),
      f("snapshot.end", { requestId: R, seq: 4 }, `${R}:4`),
    ]);
    expect(effects).toEqual(["refresh"]);
    expect(state.turn).toMatchObject({ running: false, outcome: { state: "idle" } });
    expect(state.cursor).toBe(`${R}:4`);
  });

  it("a half-received snapshot is dropped and the cursor stays", () => {
    const mid = run([f("turn.started", { requestId: R }, `${R}:1`), f("snapshot.begin", { requestId: R, turnState: "running", seq: 5 })]).state;
    expect(dropPartial(mid)).toEqual({ ...mid, snapshot: null });
    expect(dropPartial(mid).cursor).toBe(`${R}:1`);
  });

  it("switches to a new turn's snapshot", () => {
    const { state } = run([
      f("turn.started", { requestId: R }, `${R}:1`),
      f("snapshot.begin", { requestId: R2, turnState: "running", seq: 1 }),
      f("snapshot.end", { requestId: R2, seq: 1 }, `${R2}:1`),
    ]);
    expect(state.turn?.requestId).toBe(R2);
  });

  it("stream.stopped ignores later item events; turn.discard clears text", () => {
    const stopped = run([
      f("turn.started", { requestId: R }, `${R}:1`),
      f("item.started", { requestId: R, itemId: "m1", kind: "agentMessage" }, `${R}:2`),
      f("stream.stopped", { requestId: R, reason: "budget" }, `${R}:3`),
      f("item.delta", { requestId: R, itemId: "m1", text: "x" }, `${R}:4`),
    ]).state;
    expect(stopped.turn?.stopped).toBe("budget");
    expect(stopped.turn?.items[0].text).toBe("");
    const discarded = run(
      [f("item.delta", { requestId: R, itemId: "m1", text: "y" }, `${R}:2`), f("turn.discard", { requestId: R, reason: "reroute" }, `${R}:3`)],
      run([f("turn.started", { requestId: R }, `${R}:1`), f("item.started", { requestId: R, itemId: "m1", kind: "agentMessage" }, `${R}:1`)]).state
    ).state;
    expect(discarded.turn?.items).toEqual([]);
  });

  it("sync refreshes; overflow/mismatch forget the cursor", () => {
    const base = run([f("turn.started", { requestId: R }, `${R}:1`)]).state;
    expect(run([f("sync", { reason: "expired" })], base)).toMatchObject({ effects: ["refresh"], state: { cursor: `${R}:1` } });
    expect(run([f("sync", { reason: "overflow" })], base).state.cursor).toBeNull();
  });

  it("runner unavailable or idle while a turn runs ends it via the record", () => {
    const base = run([f("turn.started", { requestId: R }, `${R}:1`)]).state;
    expect(run([f("runner", { available: false, reason: "idle_stopped" })], base).effects).toEqual(["runner", "refresh"]);
    // idle은 refresh를 직접 내지 않는다(화면이 진행 중 상태일 때만 1회 확인 — PM 결정 #90).
    const idle = applyFrame(base, f("idle", { requestId: null, turnState: null }));
    expect(idle.effects).toEqual([{ type: "idle", wasRunning: true }]);
    expect(idle.state.turn?.running).toBe(false);
    expect(applyFrame(EMPTY_STREAM, f("idle", { requestId: null, turnState: null })).effects).toEqual([{ type: "idle", wasRunning: false }]);
  });

  it("ignores unknown events, other turns and bad JSON", () => {
    const base = run([f("turn.started", { requestId: R }, `${R}:1`)]).state;
    expect(run([{ id: `${R}:2`, event: "item.delta", data: "{bad" }], base).state).toBe(base);
    expect(run([f("future.event", { requestId: R }, `${R}:2`)], base).state).toBe(base);
    expect(run([f("item.started", { requestId: R2, itemId: "x", kind: "agentMessage" }, `${R2}:2`)], base).state.turn?.items).toEqual([]);
  });
});

describe("streamingBlocks", () => {
  it("splits paragraphs but keeps lists, quotes and indented lines together", () => {
    expect(streamingBlocks("# 제목\n\n문단 하나\n\n- a\n\n- b\n\n> 인용\n\n> 계속\n\n끝")).toEqual(["# 제목\n", "문단 하나\n", "- a\n\n- b\n", "> 인용\n\n> 계속\n", "끝"]);
  });
  it("never splits inside a fenced code block and closes an open one for display", () => {
    expect(streamingBlocks("앞\n\n```js\nconst a = 1;\n\nconst b = 2;")).toEqual(["앞\n", "```js\nconst a = 1;\n\nconst b = 2;\n```"]);
    expect(streamingBlocks("```\nx\n```\n\n뒤")).toEqual(["```\nx\n```\n", "뒤"]);
  });
  it("joined blocks reproduce the text (except the display-only fence close)", () => {
    const text = "a\n\nb\n\n1. x\n\n2. y\n\n```\ncode\n```";
    expect(streamingBlocks(text).join("\n")).toBe(text);
    expect(streamingBlocks("")).toEqual([""]);
  });
});
