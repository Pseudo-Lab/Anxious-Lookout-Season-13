// #89 화면 상태와 말풍선 연속성(보낸 말풍선 → 접수 항목 → 기록 항목이 같은 key로 이어진다).
import { describe, expect, it } from "vitest";
import { EMPTY_CONTINUITY, awaitingReply, chatStatus, reconcile, type Continuity, type LocalSend } from "@/lib/research/transcript";
import type { SessionItem } from "@/lib/research/types";

const user = (id: string, text: string, extra: object = {}): SessionItem => ({ id, type: "message", role: "user", text, ...extra });
const codex = (id: string, text: string): SessionItem => ({ id, type: "message", role: "assistant", text });
const pending = (rid: string, text: string): SessionItem => user(`pending-${rid}`, text, { source: "platform", status: "pending" });
const notRecorded = (id: string, text: string): SessionItem => user(id, text, { source: "platform", status: "not_recorded" });
const keys = (r: { entries: { key: string }[] }) => r.entries.map((e) => e.key);

describe("chatStatus", () => {
  it("prefers the in-flight send over the server state", () => {
    expect(chatStatus(true, "idle")).toBe("submitted");
    expect(chatStatus(true, "failed")).toBe("submitted");
    expect(chatStatus(false, "running")).toBe("waiting");
    expect(chatStatus(false, "failed")).toBe("error");
    expect(chatStatus(false, "idle")).toBe("ready");
  });
  it("streaming (#90) wins over the record state but not over an in-flight send", () => {
    expect(chatStatus(false, "running", true)).toBe("streaming");
    expect(chatStatus(false, "idle", true)).toBe("streaming");
    expect(chatStatus(true, "idle", true)).toBe("submitted");
  });
  it("awaits a reply only while sending, running or streaming", () => {
    expect(["ready", "submitted", "waiting", "streaming", "error"].map((s) => awaitingReply(s as never))).toEqual([false, true, true, true, false]);
  });
});

describe("reconcile", () => {
  const before = [user("u1", "첫 질문"), codex("a1", "첫 답")];
  const local: LocalSend = { key: "local-k", text: "둘째 질문", baseline: ["u1", "a1"] };

  it("shows the local bubble at the end until the server has it", () => {
    const r = reconcile(EMPTY_CONTINUITY, before, local);
    expect(keys(r)).toEqual(["u1", "a1", "local-k"]);
    expect(r.entries[2]).toMatchObject({ kind: "local", text: "둘째 질문" });
  });

  it("keeps one key from local bubble through pending item to the recorded item", () => {
    const first = reconcile(EMPTY_CONTINUITY, before, local);
    const running = reconcile(first.next, [...before, pending("r1", "둘째 질문")], local);
    expect(keys(running)).toEqual(["u1", "a1", "local-k"]);
    expect(running.entries.filter((e) => e.kind === "local")).toHaveLength(0);
    // 보내기 요청이 끝나 local을 지워도 접수 항목과 그 뒤 기록 항목은 같은 key다.
    const still = reconcile(running.next, [...before, pending("r1", "둘째 질문")], null);
    expect(keys(still)).toEqual(["u1", "a1", "local-k"]);
    const done = reconcile(still.next, [...before, user("u2", "둘째 질문"), codex("a2", "둘째 답")], null);
    expect(keys(done)).toEqual(["u1", "a1", "local-k", "a2"]);
    expect(done.next.anchor).toBeNull();
    // 다음 전송 뒤에도 이미 정한 key는 바뀌지 않는다(remount 없음).
    const nextLocal: LocalSend = { key: "local-2", text: "셋째", baseline: ["u1", "a1", "u2", "a2"] };
    expect(keys(reconcile(done.next, [...before, user("u2", "둘째 질문"), codex("a2", "둘째 답")], nextLocal))).toEqual([
      "u1",
      "a1",
      "local-k",
      "a2",
      "local-2",
    ]);
  });

  it("matches the recorded item directly when the pending step was never seen", () => {
    const r = reconcile(EMPTY_CONTINUITY, [...before, user("u2", " 둘째 질문 "), codex("a2", "답")], local);
    expect(keys(r)).toEqual(["u1", "a1", "local-k", "a2"]);
  });

  it("does not take an older message with the same text (replayed send)", () => {
    const same: LocalSend = { key: "local-k", text: "첫 질문", baseline: ["u1", "a1"] };
    const r = reconcile(EMPTY_CONTINUITY, before, same);
    expect(keys(r)).toEqual(["u1", "a1", "local-k"]);
    // local이 지워지면 말풍선은 하나만 남는다.
    expect(keys(reconcile(r.next, before, null))).toEqual(["u1", "a1"]);
  });

  it("skips a failed (not recorded) input with the same text", () => {
    const failedBefore = [...before, pending("old", "둘째 질문")].map((i) =>
      i.id === "pending-old" ? notRecorded("pending-old", "둘째 질문") : i
    );
    const resend: LocalSend = { key: "local-k", text: "둘째 질문", baseline: failedBefore.map((i) => i.id) };
    const after = [...before, notRecorded("unrecorded-old", "둘째 질문"), pending("new", "둘째 질문")];
    const r = reconcile(EMPTY_CONTINUITY, after, resend);
    expect(keys(r)).toEqual(["u1", "a1", "unrecorded-old", "local-k"]);
  });

  it("links pending to recorded when the conversation was opened mid-turn", () => {
    const open = reconcile(EMPTY_CONTINUITY, [...before, pending("r1", "둘째 질문")], null);
    expect(keys(open)).toEqual(["u1", "a1", "pending-r1"]);
    const done = reconcile(open.next, [...before, user("u2", "둘째 질문"), codex("a2", "답")], null);
    expect(keys(done)).toEqual(["u1", "a1", "pending-r1", "a2"]);
  });

  it("never emits duplicate keys", () => {
    const broken: Continuity = { aliases: { u1: "x", a1: "x" }, anchor: null };
    expect(keys(reconcile(broken, before, null))).toEqual(["x", "a1"]);
  });
});
