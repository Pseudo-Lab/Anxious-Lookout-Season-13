import { describe, expect, it } from "vitest";
import {
  cpLength,
  formatBibliography,
  fromBibliographyForm,
  parseBibliography,
  toBibliographyForm,
  type BibliographyForm,
} from "@/lib/research/bibliography";

const empty = toBibliographyForm(null);
const form = (patch: Partial<BibliographyForm>): BibliographyForm => ({ ...empty, ...patch });

describe("parseBibliography", () => {
  it("keeps null as no bibliography", () => {
    expect(parseBibliography(null)).toBeNull();
  });
  it("parses the contract subset", () => {
    const v = {
      type: "article-journal",
      title: "T",
      author: [{ family: "Kim", given: "Minji" }, { literal: "WHO" }],
      issued: { "date-parts": [[2024, 3]] },
      "container-title": "J",
      DOI: "10.1/x",
    };
    expect(parseBibliography(v)).toEqual(v);
  });
  it("rejects malformed values", () => {
    expect(parseBibliography({})).toBeUndefined();
    expect(parseBibliography({ type: "book", title: 3 })).toBeUndefined();
    expect(parseBibliography({ type: "book", author: [{ family: "A", literal: "B" }] })).toBeUndefined();
    expect(parseBibliography({ type: "book", issued: { "date-parts": [[2024, 1, 2, 3]] } })).toBeUndefined();
  });
});

describe("fromBibliographyForm", () => {
  it("returns null when nothing is filled", () => {
    expect(fromBibliographyForm(empty)).toEqual({ ok: true, value: null });
  });
  it("requires a type once any field is filled", () => {
    expect(fromBibliographyForm(form({ title: "x" })).ok).toBe(false);
  });
  it("splits authors into family/given or literal", () => {
    const r = fromBibliographyForm(form({ type: "book", authors: "Kim, Minji\n\nOECD\nLee,\n" }));
    expect(r).toEqual({ ok: true, value: { type: "book", author: [{ family: "Kim", given: "Minji" }, { literal: "OECD" }, { family: "Lee" }] } });
  });
  it("parses issued dates and rejects impossible ones", () => {
    expect(fromBibliographyForm(form({ type: "report", issued: "2024-02-29" }))).toEqual({
      ok: true,
      value: { type: "report", issued: { "date-parts": [[2024, 2, 29]] } },
    });
    expect(fromBibliographyForm(form({ type: "report", issued: "2023-02-29" })).ok).toBe(false);
    expect(fromBibliographyForm(form({ type: "report", issued: "2024-13" })).ok).toBe(false);
    expect(fromBibliographyForm(form({ type: "report", issued: "0" })).ok).toBe(false);
  });
  it("strips DOI URL prefixes and checks the DOI shape", () => {
    expect(fromBibliographyForm(form({ type: "article", DOI: "https://doi.org/10.1000/xyz" }))).toEqual({
      ok: true,
      value: { type: "article", DOI: "10.1000/xyz" },
    });
    expect(fromBibliographyForm(form({ type: "article", DOI: "xyz" })).ok).toBe(false);
  });
  it("counts length in code points", () => {
    expect(cpLength("😀😀")).toBe(2);
    expect(fromBibliographyForm(form({ type: "book", volume: "😀".repeat(50) })).ok).toBe(true);
    expect(fromBibliographyForm(form({ type: "book", volume: "😀".repeat(51) })).ok).toBe(false);
  });
  it("accepts years 1–99 and checks leap days without the Date.UTC 1900 shift", () => {
    expect(fromBibliographyForm(form({ type: "book", issued: "0099-01-01" }))).toEqual({ ok: true, value: { type: "book", issued: { "date-parts": [[99, 1, 1]] } } });
    expect(fromBibliographyForm(form({ type: "book", issued: "100-01-01" })).ok).toBe(true);
    expect(fromBibliographyForm(form({ type: "book", issued: "0004-02-29" })).ok).toBe(true);
    expect(fromBibliographyForm(form({ type: "book", issued: "0100-02-29" })).ok).toBe(false);
    expect(fromBibliographyForm(form({ type: "book", issued: "2000-02-29" })).ok).toBe(true);
    expect(fromBibliographyForm(form({ type: "book", issued: "1900-02-29" })).ok).toBe(false);
    expect(fromBibliographyForm(form({ type: "book", issued: "9999-12-31" })).ok).toBe(true);
  });
  it("reads =prefixed lines as literal names (commas allowed)", () => {
    expect(fromBibliographyForm(form({ type: "report", authors: "=Research, Inc." }))).toEqual({
      ok: true,
      value: { type: "report", author: [{ literal: "Research, Inc." }] },
    });
    expect(toBibliographyForm({ type: "report", author: [{ literal: "Research, Inc." }, { literal: "OECD" }] }).authors).toBe("=Research, Inc.\nOECD");
  });
  it("keeps stored names that a line cannot express when the line is untouched", () => {
    const stored = { type: "book", author: [{ family: "Smith, Jr.", given: "John" }, { literal: "Research, Inc." }, { family: "Lee" }] };
    const f = toBibliographyForm(stored);
    expect(fromBibliographyForm(f, stored)).toEqual({ ok: true, value: stored });
    // 다른 칸만 고쳐도 이름은 그대로
    expect(fromBibliographyForm({ ...f, publisher: "P" }, stored)).toEqual({ ok: true, value: { type: "book", publisher: "P", author: stored.author } });
    // 한 줄만 고치면 그 줄만 새로 읽는다
    const edited = { ...f, authors: f.authors.replace("Lee,", "Lee, Ana") };
    expect(fromBibliographyForm(edited, stored)).toEqual({
      ok: true,
      value: { type: "book", author: [{ family: "Smith, Jr.", given: "John" }, { literal: "Research, Inc." }, { family: "Lee", given: "Ana" }] },
    });
  });
  it("round-trips through the form", () => {
    const b = {
      type: "chapter",
      author: [{ family: "Kim", given: "M" }, { family: "Lee" }, { literal: "UN" }],
      issued: { "date-parts": [[2020, 1, 5]] },
      page: "1-2",
    };
    expect(fromBibliographyForm(toBibliographyForm(b))).toEqual({ ok: true, value: b });
  });
});

describe("formatBibliography", () => {
  it("builds a short citation line", () => {
    expect(
      formatBibliography({
        type: "article-journal",
        author: [{ family: "A" }, { family: "B" }, { family: "C" }, { literal: "D" }],
        issued: { "date-parts": [[2021]] },
        title: "Title",
        "container-title": "Journal",
        volume: "3",
        issue: "2",
        DOI: "10.1/x",
      })
    ).toBe("A; B; C 외 1명 (2021). Title. Journal, 3(2). DOI 10.1/x");
  });
});
