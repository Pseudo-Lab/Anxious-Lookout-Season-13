// #32 리뷰 M3: 저장하지 못한 본문 버퍼는 지금 계정의 메모리에만 있고, 로그아웃·계정 전환 뒤에는 남지 않는다.
import { describe, expect, it } from "vitest";
import { bufferTarget, clearAllBodyBuffers, loadBodyBuffer, saveBodyBuffer, setBodyBufferOwner } from "./bodyBuffer";

const buf = (text: string) => ({ revision: 1, editor: { type: "doc", content: [{ type: "paragraph", content: [{ type: "text", text }] }] } });

describe("body buffer ownership", () => {
  it("keeps a buffer for the signed-in account and its draft", () => {
    setBodyBufferOwner("A");
    saveBodyBuffer("A", "d1", buf("secret A"));
    expect(loadBodyBuffer("A", "d1")?.editor.content?.[0].content?.[0].text).toBe("secret A");
    expect(loadBodyBuffer("B", "d1")).toBeNull();
  });

  it("explicit logout drops everything and ignores late saves until the next sign-in", () => {
    setBodyBufferOwner("A");
    saveBodyBuffer("A", "d1", buf("secret A"));
    clearAllBodyBuffers();
    saveBodyBuffer("A", "d1", buf("late save after logout"));
    setBodyBufferOwner("A");
    expect(loadBodyBuffer("A", "d1")).toBeNull();
  });

  it("signing in as another account drops the previous account's buffers", () => {
    setBodyBufferOwner("A");
    saveBodyBuffer("A", "d1", buf("secret A"));
    setBodyBufferOwner("B");
    saveBodyBuffer("A", "d2", buf("late save by A"));
    expect(loadBodyBuffer("B", "d1")).toBeNull();
    setBodyBufferOwner("A");
    expect(loadBodyBuffer("A", "d1")).toBeNull();
    expect(loadBodyBuffer("A", "d2")).toBeNull();
  });

  it("re-authenticating as the same account keeps the buffer", () => {
    setBodyBufferOwner("A");
    saveBodyBuffer("A", "d1", buf("kept"));
    setBodyBufferOwner("A");
    expect(loadBodyBuffer("A", "d1")).not.toBeNull();
  });

  it("targets: draft id, post without draft, new post", () => {
    expect([bufferTarget("d", "p"), bufferTarget(null, "p"), bufferTarget(undefined, null)]).toEqual(["d", "post:p", "new"]);
  });
});
