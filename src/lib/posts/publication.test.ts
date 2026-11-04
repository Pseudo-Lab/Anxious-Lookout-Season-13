// #80 F5: 최초 발행일(publishedAt)·마지막 (재)발행(updatedAt) 응답 해석과 표시 판단(api-contracts/post-publications.md).
import { afterEach, describe, expect, it, vi } from "vitest";
import { getPublicPost, listPosts, listPublicPosts } from "./api";
import { revisedDate, wasRepublished } from "./format";

const ID = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
const PUBLISHED = "2026-03-19T00:00:00Z";
const UPDATED = "2026-11-05T03:14:07.123456Z";

function publicPost(extra: Record<string, unknown> = {}) {
  return {
    id: ID(1), postId: ID(2), versionId: ID(3), slug: "s", title: "t", description: "", textProjection: "",
    content: { format: "post-blocks", version: 1, blocks: [] },
    tags: [], coverImageUrl: null, author: { login: "a", name: "a" }, documents: [], materials: [],
    publishedAt: PUBLISHED, markdownProjection: "", formatSummary: { lossy: false, omittedProperties: [], message: "" },
    ...extra,
  };
}

function privatePost(publication: unknown) {
  return {
    id: ID(2), version: 3, archived: false, createdAt: PUBLISHED, updatedAt: "2026-11-06T00:00:00Z", author: { login: "a" },
    latestVersion: {
      id: ID(3), postId: ID(2), number: 1, createdAt: PUBLISHED, title: "t", description: "", slug: "s", tags: [],
      coverImageId: null, documentIds: [], materialIds: [], materialSources: [],
    },
    publication,
  };
}

function respond(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })));
}
afterEach(() => vi.unstubAllGlobals());

describe("#80 public post time fields", () => {
  it("reads updatedAt when present", async () => {
    respond(publicPost({ updatedAt: UPDATED }));
    const r = await getPublicPost("s");
    expect(r.ok && r.data).toMatchObject({ publishedAt: PUBLISHED, updatedAt: UPDATED });
  });

  it.each([["missing", {}], ["null", { updatedAt: null }]])("updatedAt %s: still valid, shown as publishedAt only", async (_n, extra) => {
    respond(publicPost(extra));
    const r = await getPublicPost("s");
    expect(r.ok).toBe(true);
    expect(r.ok && "updatedAt" in r.data).toBe(false);
  });

  it("a non-string updatedAt is a contract mismatch", async () => {
    respond(publicPost({ updatedAt: 1 }));
    expect((await getPublicPost("s")).ok).toBe(false);
  });

  it("list items carry updatedAt too", async () => {
    const item: Record<string, unknown> = publicPost({ updatedAt: UPDATED });
    delete item.content;
    respond({ items: [item], nextCursor: null });
    const r = await listPublicPosts();
    expect(r.ok && r.data.items[0].updatedAt).toBe(UPDATED);
  });

  it("nullable contract fields (author.name, material sourceUrl) do not break the post", async () => {
    respond(publicPost({ author: { login: null, name: null }, materials: [{ id: ID(9), title: "m", sourceUrl: null }] }));
    const r = await getPublicPost("s");
    expect(r.ok && r.data.author).toEqual({ login: null, name: "" });
    expect(r.ok && r.data.materials).toEqual([{ id: ID(9), title: "m", sourceUrl: "" }]);
  });
});

describe("#80 publication ref on the private post", () => {
  const ref = { id: ID(4), versionId: ID(3), slug: "s", publishedAt: PUBLISHED };

  it("reads publication.updatedAt (not the post's own updatedAt)", async () => {
    respond({ items: [privatePost({ ...ref, updatedAt: UPDATED })], nextCursor: null });
    const r = await listPosts();
    expect(r.ok && r.data.items[0].publication).toEqual({ ...ref, updatedAt: UPDATED });
    expect(r.ok && r.data.items[0].updatedAt).toBe("2026-11-06T00:00:00Z");
  });

  it("an older ref without updatedAt still parses", async () => {
    respond({ items: [privatePost(ref)], nextCursor: null });
    const r = await listPosts();
    expect(r.ok && r.data.items[0].publication).toEqual(ref);
  });
});

describe("#80 revised date display", () => {
  it("shows a revised date only on a different day", () => {
    expect(revisedDate(PUBLISHED, undefined)).toBeNull();
    expect(revisedDate(PUBLISHED, "2026-03-19T00:00:30Z")).toBeNull();
    expect(revisedDate(PUBLISHED, UPDATED)).not.toBeNull();
  });

  it("compares instants, not strings (stamp() drops zero microseconds)", () => {
    expect(wasRepublished("2026-11-05T03:14:07Z", "2026-11-05T03:14:07.000000Z")).toBe(false);
    expect(wasRepublished("2026-11-05T03:14:07Z", "2026-11-05T03:14:07.001000Z")).toBe(true);
    expect(wasRepublished(PUBLISHED, undefined)).toBe(false);
  });
});
