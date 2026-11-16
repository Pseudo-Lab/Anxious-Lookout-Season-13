import { afterEach, describe, expect, it, vi } from "vitest";
import { BASE_PATH } from "@/lib/constants";
import { emptyDocument } from "@/lib/blocks/types";
import { getDocument, getMaterial, getVersion, listVersions } from "@/lib/research/api";
import { getPost, getPostVersion, listPostVersions } from "@/lib/posts/api";
import { parseVersionAuthor, versionAuthorLabel, versionConversationHref } from "./versionAuthor";

const ID = (n: number) => `00000000-0000-4000-8000-${n.toString().padStart(12, "0")}`;
const AT = "2026-11-16T00:00:00Z";
const CODEX = { kind: "codex", conversationId: ID(97) };
const HUMAN = { kind: "human", conversationId: null };

function respond(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })));
}
afterEach(() => vi.unstubAllGlobals());

describe("#97 version authors", () => {
  it("treats only the absent old-API field as human", () => {
    expect(parseVersionAuthor(undefined)).toEqual(HUMAN);
    expect(versionAuthorLabel()).toBe("사람");
    for (const value of [null, [], "codex", { kind: "future-agent", conversationId: ID(97) }]) {
      const author = parseVersionAuthor(value);
      expect(versionAuthorLabel(author)).toBe("작성자 미상");
      expect(versionConversationHref(author)).toBeNull();
    }
  });
  it("links Codex to the platform conversation with the configured base path", () => {
    const author = parseVersionAuthor(CODEX);
    expect(versionAuthorLabel(author)).toBe("Codex");
    expect(versionConversationHref(author)).toBe(`${BASE_PATH}/research/session/?id=${ID(97)}`);
  });
  it.each(["javascript:alert(1)", "//outside.invalid/path", "../../private", `${ID(97)}&other=1`, null])("does not turn malformed conversation %s into a link", (conversationId) => {
    expect(versionConversationHref(parseVersionAuthor({ kind: "codex", conversationId }))).toBeNull();
  });
  it("drops non-Codex conversation ids and extra provider fields", () => {
    expect(parseVersionAuthor({ ...CODEX, kind: "human", nativeThreadId: "private" })).toEqual(HUMAN);
  });
});

describe.each(["document", "material"] as const)("#97 %s author API ingestion", (type) => {
  const version = { id: ID(2), itemId: ID(1), title: "제목", content: "본문", number: 2, createdAt: AT, author: CODEX };
  it("keeps attribution on version list and detail", async () => {
    respond({ items: [version], nextCursor: null });
    const list = await listVersions(type, ID(1));
    expect(list.ok && list.data.items[0].author).toEqual(CODEX);
    respond(version);
    const detail = await getVersion(type, ID(1), ID(2));
    expect(detail.ok && detail.data.author).toEqual(CODEX);
  });
  it("keeps attribution on latestVersion", async () => {
    respond({
      id: ID(1), type, title: "제목", content: "본문", createdAt: AT, updatedAt: AT, version: 2, archived: false,
      latestVersion: { id: ID(2), number: 2, createdAt: AT, author: CODEX },
      publication: null, sourceUrl: "https://example.com", collectedAt: AT, contentKind: "excerpt",
    });
    const resource = await (type === "document" ? getDocument(ID(1)) : getMaterial(ID(1)));
    expect(resource.ok && resource.data.latestVersion.author).toEqual(CODEX);
  });
  it("old and future attribution never breaks reading the stored body", async () => {
    for (const author of [undefined, { kind: "future" }]) {
      respond({ ...version, author });
      const detail = await getVersion(type, ID(1), ID(2));
      expect(detail.ok && detail.data.content).toBe("본문");
      expect(detail.ok && detail.data.author?.kind).toBe(author ? "unknown" : "human");
    }
  });
});

describe("#97 post authors are separate from the owner", () => {
  const version = {
    id: ID(2), postId: ID(1), number: 2, createdAt: AT, title: "글", content: emptyDocument(), description: "", slug: "post",
    tags: [], coverImageId: null, documentIds: [], materialIds: [], materialSources: [], author: CODEX,
  };
  it("keeps author on list, detail and latestVersion without changing owner login", async () => {
    respond({ items: [version], nextCursor: null });
    const list = await listPostVersions(ID(1));
    expect(list.ok && list.data.items[0].author).toEqual(CODEX);
    respond(version);
    const detail = await getPostVersion(ID(1), ID(2));
    expect(detail.ok && detail.data.author).toEqual(CODEX);
    respond({ id: ID(1), version: 2, archived: false, createdAt: AT, updatedAt: AT, author: { login: "owner" }, latestVersion: version, publication: null });
    const post = await getPost(ID(1));
    expect(post.ok && post.data.author).toEqual({ login: "owner" });
    expect(post.ok && post.data.latestVersion.author).toEqual(CODEX);
  });
  it.each([undefined, { kind: "future" }])("continues reading post versions for old/future author %o", async (author) => {
    respond({ ...version, author });
    const result = await getPostVersion(ID(1), ID(2));
    expect(result.ok && result.data.author?.kind).toBe(author ? "unknown" : "human");
  });
});
