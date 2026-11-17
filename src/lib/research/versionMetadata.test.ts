import { afterEach, describe, expect, it, vi } from "vitest";
import { emptyDocument } from "@/lib/blocks/types";
import { getDocument, getMaterial, getVersion, listVersions } from "./api";
import { getPost, getPostVersion, listPostVersions } from "@/lib/posts/api";

const id = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
const at = "2026-11-17T00:00:00Z";
const oldAuthor = { kind: "pat", credentialId: id(4), name: "private name" };
const respond = (body: unknown) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })));
afterEach(() => vi.unstubAllGlobals());

describe.each(["document", "material"] as const)("#104 %s version metadata", (type) => {
  it.each([undefined, oldAuthor])("reads versions without retaining author %o", async (author) => {
    const version = { id: id(2), itemId: id(1), title: "제목", content: "본문", number: 2, createdAt: at, author };
    respond({ items: [version], nextCursor: null });
    const list = await listVersions(type, id(1));
    expect(list.ok).toBe(true);
    if (list.ok) expect(list.data.items[0]).not.toHaveProperty("author");
    respond(version);
    const detail = await getVersion(type, id(1), id(2));
    expect(detail.ok && detail.data.content).toBe("본문");
    if (detail.ok) expect(detail.data).not.toHaveProperty("author");
    respond({ id: id(1), type, title: "제목", content: "본문", createdAt: at, updatedAt: at, version: 2, archived: false,
      latestVersion: version, publication: null, sourceUrl: "https://example.com", collectedAt: at, contentKind: "excerpt" });
    const resource = await (type === "document" ? getDocument(id(1)) : getMaterial(id(1)));
    expect(resource.ok).toBe(true);
    if (resource.ok) expect(resource.data.latestVersion).not.toHaveProperty("author");
  });
});

it.each([undefined, oldAuthor])("#104 post versions omit author while public owner remains separate %o", async (author) => {
  const version = { id: id(2), postId: id(1), number: 2, createdAt: at, title: "글", content: emptyDocument(), description: "", slug: "post",
    tags: [], coverImageId: null, documentIds: [], materialIds: [], materialSources: [], author };
  respond({ items: [version], nextCursor: null });
  const list = await listPostVersions(id(1));
  expect(list.ok).toBe(true);
  if (list.ok) expect(list.data.items[0]).not.toHaveProperty("author");
  respond(version);
  const detail = await getPostVersion(id(1), id(2));
  expect(detail.ok).toBe(true);
  if (detail.ok) expect(detail.data).not.toHaveProperty("author");
  respond({ id: id(1), version: 2, archived: false, createdAt: at, updatedAt: at, author: { login: "owner" }, latestVersion: version, publication: null });
  const post = await getPost(id(1));
  expect(post.ok && post.data.author).toEqual({ login: "owner" });
  if (post.ok) expect(post.data.latestVersion).not.toHaveProperty("author");
});
