import { describe, expect, it } from "vitest";
import { documentPath, materialPath, parseDetailPath, publicDocumentPath, writePostPath } from "./routes";

describe("detail routes", () => {
  const id = "3f2a1c4e-0000-4000-8000-000000000001";
  it("round-trips every detail path", () => {
    expect(parseDetailPath(materialPath(id))).toEqual({ route: "material", id });
    expect(parseDetailPath(documentPath(id))).toEqual({ route: "document", id });
    expect(parseDetailPath(writePostPath(id))).toEqual({ route: "post", id });
    expect(parseDetailPath(publicDocumentPath(id))).toEqual({ route: "public_document", id });
  });

  it("does not treat fixed screens or lists as details", () => {
    expect(parseDetailPath("/write/new/")).toBeNull();
    expect(parseDetailPath("/research/material/new/")).toBeNull();
    expect(parseDetailPath("/write/")).toBeNull();
    expect(parseDetailPath("/research/material/")).toBeNull();
  });

  it("encodes ids", () => {
    expect(writePostPath("a/b")).toBe("/write/a%2Fb/");
  });
});
