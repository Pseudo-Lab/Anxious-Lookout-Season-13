import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getResearchCalls, groupResearchCalls, parseResearchCalls, researchCallHref, researchCallLabel } from "./researchCalls";
import { ResearchActivityList } from "@/components/research/session/ResearchActivity";

vi.mock("@/lib/constants", () => ({ BASE_PATH: "/test-base", API_ENABLED: true }));
const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
const saved = {
  resourceType: "document", id: id(3), title: "문서", versionId: id(4), versionNumber: 2,
  destination: { path: "/research/document/", resourceId: id(3), versionId: id(4) },
};
const receipt = (extra: Record<string, unknown> = {}) => ({ id: id(1), requestId: id(2), operation: "research_document_save", status: "succeeded", at: "2026-11-17T01:02:03Z", saved, ...extra });
const parse = (value: unknown) => parseResearchCalls({ items: [value] });
afterEach(() => vi.unstubAllGlobals());

describe("#97 r2 receipt ingestion", () => {
  it("uses only the owned no-store GET endpoint, with no token or mutation", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify({ items: [receipt()] }), { headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetch);
    const result = await getResearchCalls(id(2));
    expect(result.ok && result.data[0].saved?.versionId).toBe(id(4));
    expect(fetch.mock.calls[0][0]).toBe(`/test-base/api/research/sessions/${id(2)}/research-calls`);
    expect(fetch.mock.calls[0][1]).toMatchObject({ cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } });
    expect(fetch.mock.calls[0][1]).not.toHaveProperty("body");
    expect(fetch.mock.calls[0][1]).not.toHaveProperty("method");
  });
  it("does not interpret shell/native/model saved JSON as a receipt list", () => {
    for (const value of [receipt(), { saved }, { content: [{ text: JSON.stringify(receipt()) }] }, { items: [{ type: "tool_call", output: { saved } }] }]) expect(parseResearchCalls(value)).toBeNull();
  });
  it.each([
    { id: "not-a-receipt-id" }, { requestId: "not-a-request" }, { at: "not-a-date" }, { status: "completed" },
    { saved: undefined }, { status: "failed" }, { operation: "research_list" },
    { saved: { ...saved, versionNumber: 0 } },
    { saved: { ...saved, destination: { ...saved.destination, resourceId: id(99) } } },
    { saved: { ...saved, destination: { ...saved.destination, versionId: id(99) } } },
    { saved: { ...saved, destination: { ...saved.destination, path: "https://outside.invalid" } } },
    { saved: { ...saved, resourceType: "post" } },
  ])("rejects malformed or inconsistent successful save metadata %o", (extra) => expect(parse(receipt(extra))).toBeNull());
  it("projects only safe receipt fields, ignoring args/stdout/process/provider metadata", () => {
    const call = parse(receipt({ command: "private command", headers: { Authorization: "secret" }, input: "secret", output: "secret", turnId: "native-turn", nativeThreadId: "private", saved: { ...saved, body: "private body" } }))![0];
    expect(call).toEqual(receipt());
    expect(researchCallHref(call)).toBe(`/test-base/research/document/?id=${id(3)}#version-history`);
  });
  it("accepts zero-count public reads without copying the read body", () => {
    const call = parse(receipt({ operation: "research_page_context", saved: undefined, count: 0, body: "private body", snapshot: { type: "public_document", resourceId: id(5), snapshotId: id(6), versionId: id(7) }, target: { materialId: id(8), versionId: id(9) } }))![0];
    expect(call.count).toBe(0);
    expect(call.snapshot?.versionId).toBe(id(7));
    expect(call).not.toHaveProperty("body");
    expect(researchCallHref(call)).toBeNull();
  });
  it("accepts relation/archive targets without fabricating saved snapshots", () => {
    const call = parse(receipt({ operation: "research_archive", saved: undefined, target: { type: "document", id: id(3), version: 4 } }))![0];
    expect(call.saved).toBeUndefined();
    expect(researchCallLabel(call)).toBe("보관");
    expect(researchCallHref(call)).toBe(`/test-base/research/document/?id=${id(3)}`);
  });
});

describe("#97 receipt identity, replay and display", () => {
  it("deduplicates receipt IDs and groups repeated confirmations of the same immutable version", () => {
    const calls = parseResearchCalls({ items: [receipt(), receipt(), receipt({ id: id(10) }), receipt({ id: id(11), saved: { ...saved, versionId: id(12), versionNumber: 3, destination: { ...saved.destination, versionId: id(12) } } })] })!;
    expect(calls).toHaveLength(3);
    const groups = groupResearchCalls([...calls, calls[0]]);
    expect(groups).toHaveLength(2);
    expect(groups.map((g) => g.confirmations)).toEqual([2, 1]);
    expect(groups.map((g) => g.call.saved?.versionNumber)).toEqual([2, 3]);
  });
  it("escapes plain titles and displays saved version/history, never null/null", () => {
    const calls = parseResearchCalls({ items: [receipt({ saved: { ...saved, title: '<img src=x onerror="attack()">' } }), receipt({ id: id(10), saved: { ...saved, title: '<img src=x onerror="attack()">' } })] })!;
    const html = renderToStaticMarkup(createElement(ResearchActivityList, { calls }));
    expect(html).toContain("&lt;img");
    expect(html).not.toContain("<img");
    expect(html).toContain("v2");
    expect(html).toContain("동일 버전 확인 2회");
    expect(html).toContain("저장됨 · 되돌리기는 버전 기록에서");
    expect(html).toContain(`/test-base/research/document/?id=${id(3)}#version-history`);
    expect(html).not.toContain("null");
  });
  it("uses a generic label for future operations and fixed failure text without raw codes", () => {
    const calls = parse(receipt({ status: "failed", operation: "private/operation/path", saved: undefined, code: "SECRET-provider-reason" }))!;
    expect(researchCallHref(calls[0])).toBeNull();
    const html = renderToStaticMarkup(createElement(ResearchActivityList, { calls }));
    expect(html).toContain("연구 작업");
    expect(html).toContain("작업 실패");
    expect(html).not.toContain("private/operation/path");
    expect(html).not.toContain("SECRET");
  });
  it.each(["material", "post"])("uses the fixed %s destination and base path", (type) => {
    const path = type === "post" ? "/write/" : "/research/material/";
    const call = parse(receipt({ operation: `research_${type}_save`, saved: { ...saved, resourceType: type, destination: { ...saved.destination, path } } }))![0];
    expect(researchCallHref(call)).toBe(`/test-base${path}?id=${id(3)}#version-history`);
  });
});
