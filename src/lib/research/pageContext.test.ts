import { describe, expect, it } from "vitest";
import { describePage } from "./pageContext";

const id = "3F2A1C4E-0000-4000-8000-000000000001";
const none = new URLSearchParams();

describe("describePage (#119 B2)", () => {
  it("reads path-style detail routes", () => {
    expect(describePage(`/research/material/${id}/`, none)).toEqual({ kind: "material", resourceId: id.toLowerCase() });
    expect(describePage(`/research/document/${id}/`, none)).toEqual({ kind: "document", resourceId: id.toLowerCase() });
    expect(describePage(`/write/${id}/`, none)).toEqual({ kind: "post", resourceId: id.toLowerCase() });
    expect(describePage(`/public/document/${id}/`, none)).toEqual({ kind: "public_document", resourceId: id.toLowerCase() });
  });

  it("still reads the old ?id= form while it redirects", () => {
    expect(describePage("/research/material/", new URLSearchParams({ id }))).toEqual({ kind: "material", resourceId: id.toLowerCase() });
    expect(describePage("/write/", new URLSearchParams({ id }))).toEqual({ kind: "post", resourceId: id.toLowerCase() });
  });

  it("keeps fixed screens", () => {
    expect(describePage("/write/new/", none)).toEqual({ kind: "post_workspace" });
    expect(describePage("/research/material/new/", none)).toEqual({ kind: "material_workspace" });
    expect(describePage("/write/not-a-uuid/", none)).toEqual({ kind: "general" });
  });
});
