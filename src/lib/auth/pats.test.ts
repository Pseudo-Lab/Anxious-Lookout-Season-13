import { afterEach, describe, expect, it, vi } from "vitest";
import { issuePat, listPats, parseIssuedPat, parsePat, parsePatPage, patFailureMessage, patStatus, revokePat } from "./pats";

const ID = "00000000-0000-4000-8000-000000000097";
const SECRET = `rpat1.${ID}.${"a".repeat(43)}`; // 모의 값. 실제 발급/자격이 아니다.
const PAT = { id: ID, name: "외부 도우미", scope: "write", issuedAt: "2026-11-17T00:00:00Z", expiresAt: "2026-12-17T00:00:00Z", revokedAt: null, lastUsedAt: null, effectiveScope: "write", scopeLimitedByRole: false };
const INPUT = { name: "외부 도우미", scope: "write" as const, expiryDays: 30 };
const FIRST = { credential: PAT, token: SECRET, secretAvailable: true, replayed: false };
const REPLAY = { credential: PAT, token: null, secretAvailable: false, replayed: true };
function respond(body: unknown, status = 200) {
  const fetch = vi.fn<typeof globalThis.fetch>(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
afterEach(() => vi.unstubAllGlobals());

describe("#97 B8 cookie PAT management", () => {
  it("list DTO never retains a secret/hash/prefix even if extra fields are present", async () => {
    const fetch = respond({ items: [{ ...PAT, token: SECRET, hash: "private", prefix: "private" }], nextCursor: "opaque" });
    const result = await listPats("next/page");
    expect(result.ok && result.data).toEqual({ items: [PAT], nextCursor: "opaque" });
    expect(JSON.stringify(result)).not.toContain(SECRET);
    expect(fetch.mock.calls[0][0]).toContain("cursor=next%2Fpage");
    expect(fetch.mock.calls[0][1]).toMatchObject({ credentials: "include", cache: "no-store" });
    expect(fetch.mock.calls[0][1]?.headers).not.toHaveProperty("Authorization");
  });
  it("first 201 value is accepted only with matching credential identity and flags", async () => {
    const fetch = respond(FIRST, 201);
    const result = await issuePat(INPUT, "csrf", ID);
    expect(result.ok && result.data).toEqual(FIRST);
    const init = fetch.mock.calls[0][1]!;
    expect(init).toMatchObject({ credentials: "include", method: "POST", headers: { "X-CSRF-Token": "csrf", "Idempotency-Key": ID } });
    expect(JSON.parse(init.body as string)).toEqual(INPUT);
    expect(init.body).not.toContain(SECRET);
  });
  it("replay returns the existing id with no recoverable value", async () => {
    respond(REPLAY);
    const result = await issuePat(INPUT, "csrf", ID);
    expect(result.ok && result.data).toEqual(REPLAY);
  });
  it.each([
    { ...REPLAY, token: SECRET }, { ...FIRST, token: null }, { ...FIRST, replayed: true },
    { ...FIRST, token: "invalid" }, { ...FIRST, token: SECRET.replace(ID, "00000000-0000-4000-8000-000000000098") },
  ])("rejects inconsistent one-time/replay response %o", (body) => expect(parseIssuedPat(body)).toBeNull());
  it("does not render a secret under a replay HTTP status", async () => {
    respond(FIRST, 200);
    const result = await issuePat(INPUT, "csrf", ID);
    expect(result.ok).toBe(false);
    expect(JSON.stringify(result)).not.toContain(SECRET);
  });
  it("does not automatically retry a lost issue response or a rate limit", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async () => { throw new Error("lost"); });
    vi.stubGlobal("fetch", fetch);
    expect((await issuePat(INPUT, "csrf", ID)).ok).toBe(false);
    expect(fetch).toHaveBeenCalledTimes(1);
    const limited = respond({ error: { code: "rate_limited" } }, 429);
    expect((await issuePat(INPUT, "csrf", ID)).ok).toBe(false);
    expect(limited).toHaveBeenCalledTimes(1);
  });
  it("revoke uses only cookie+CSRF, no Bearer or secret body", async () => {
    const revoked = { ...PAT, revokedAt: "2026-11-18T00:00:00Z" };
    const fetch = respond({ credential: revoked });
    const result = await revokePat(ID, "csrf");
    expect(result.ok && result.data.credential).toEqual(revoked);
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: "DELETE", credentials: "include", headers: { "X-CSRF-Token": "csrf" } });
    expect(fetch.mock.calls[0][1]).not.toHaveProperty("body");
    expect(fetch.mock.calls[0][1]?.headers).not.toHaveProperty("Authorization");
  });
  it("keeps selected scope separate from commenter or approval limits", () => {
    expect(parsePat({ ...PAT, effectiveScope: "public", scopeLimitedByRole: true })).toMatchObject({ scope: "write", effectiveScope: "public", scopeLimitedByRole: true });
    expect(parsePat({ ...PAT, effectiveScope: "none", scopeLimitedByRole: true })).toMatchObject({ scope: "write", effectiveScope: "none" });
  });
  it.each([{ scope: "admin" }, { effectiveScope: "admin" }, { expiresAt: "bad" }, { lastUsedAt: 1 }, { scopeLimitedByRole: "yes" }, { name: "x\ny" }])("rejects invalid metadata %o", (extra) => expect(parsePat({ ...PAT, ...extra })).toBeNull());
  it("requires a valid page envelope", () => {
    expect(parsePatPage({ items: [PAT], nextCursor: null })).toEqual({ items: [PAT], nextCursor: null });
    expect(parsePatPage({ items: [PAT, null], nextCursor: null })).toBeNull();
  });
  it("distinguishes expiry and revocation independently from effective role scope", () => {
    const pat = parsePat({ ...PAT, effectiveScope: "none", scopeLimitedByRole: true })!;
    expect(patStatus(pat, Date.parse("2026-11-20T00:00:00Z"))).toBe("active");
    expect(patStatus(pat, Date.parse(PAT.expiresAt))).toBe("expired");
    expect(patStatus({ ...pat, revokedAt: PAT.issuedAt }, Date.parse(PAT.expiresAt))).toBe("revoked");
  });
  it("uses fixed error copy without copying untrusted body or credentials", () => {
    expect(patFailureMessage({ kind: "http", status: 409, code: "pat_limit" })).toContain("10개");
    expect(patFailureMessage({ kind: "http", status: 500, code: SECRET })).not.toContain(SECRET);
  });
});
