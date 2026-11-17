// #105 자료 서지정보(CSL-JSON 부분집합). api-contracts/issue105-research-wiki-v1.1.md 4.1.
// 응답 파싱, 입력 폼 값 ↔ 저장값 변환, 짧은 표시 문구를 둔다. 길이는 모두 Unicode code point 기준이다.

import { own } from "@/lib/research/own";
import type { Bibliography, CslName } from "@/lib/research/types";

export const cpLength = (s: string) => [...s].length;

// 계약의 CslType과 표시 이름(계약 4.1 순서)
export const CSL_TYPE_LABELS: Record<string, string> = {
  "article-journal": "학술지 논문",
  "paper-conference": "학회 논문",
  article: "프리프린트",
  book: "책",
  chapter: "책의 장",
  report: "보고서",
  thesis: "학위논문",
  webpage: "웹페이지",
  "post-weblog": "블로그 글",
  "article-newspaper": "신문 기사",
  "article-magazine": "잡지 기사",
  dataset: "데이터셋",
  software: "소프트웨어",
  motion_picture: "영상",
  interview: "인터뷰",
  legislation: "법령",
  document: "기타 문서",
};
export const CSL_TYPES = Object.keys(CSL_TYPE_LABELS);

export const cslTypeLabel = (type: string) => own(CSL_TYPE_LABELS, type) ?? type;

// 문자열 필드와 최대 길이(계약 4.1)
const TEXT_FIELDS = {
  title: 1000,
  "container-title": 1000,
  publisher: 500,
  volume: 50,
  issue: 50,
  page: 50,
  number: 100,
  DOI: 256,
  ISBN: 32,
  ISSN: 32,
} as const;
type TextField = keyof typeof TEXT_FIELDS;
const TEXT_KEYS = Object.keys(TEXT_FIELDS) as TextField[];

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const isNonEmpty = (v: unknown): v is string => typeof v === "string" && v !== "";

function parseName(v: unknown): CslName | null {
  if (!isObj(v)) return null;
  if (isNonEmpty(v.literal) && v.family === undefined && v.given === undefined) return { literal: v.literal };
  if (!isNonEmpty(v.family) || v.literal !== undefined) return null;
  if (v.given !== undefined && !isNonEmpty(v.given)) return null;
  return isNonEmpty(v.given) ? { family: v.family, given: v.given } : { family: v.family };
}

function parseDateParts(v: unknown): number[] | null {
  if (!isObj(v) || !Array.isArray(v["date-parts"]) || v["date-parts"].length !== 1) return null;
  const parts = v["date-parts"][0];
  if (!Array.isArray(parts) || parts.length < 1 || parts.length > 3 || !parts.every((n) => Number.isInteger(n))) return null;
  return parts as number[];
}

/** 응답의 서지정보. null은 서지정보 없음, undefined는 형식 오류(호출 측이 응답 전체를 invalid로 둔다). */
export function parseBibliography(v: unknown): Bibliography | null | undefined {
  if (v === null) return null;
  if (!isObj(v) || !isNonEmpty(v.type)) return undefined;
  const out: Bibliography = { type: v.type };
  for (const key of TEXT_KEYS) {
    const value = v[key];
    if (value === undefined) continue;
    if (!isNonEmpty(value)) return undefined;
    out[key] = value;
  }
  if (v.author !== undefined) {
    if (!Array.isArray(v.author)) return undefined;
    const names = v.author.map(parseName);
    if (names.some((n) => n === null)) return undefined;
    out.author = names as CslName[];
  }
  if (v.issued !== undefined) {
    const parts = parseDateParts(v.issued);
    if (!parts) return undefined;
    out.issued = { "date-parts": [parts] };
  }
  return out;
}

// ---- 입력 폼 ----

/** 폼 값. type이 ""이면 서지정보 없음. 저자는 한 줄에 한 명(‘성, 이름’ 또는 기관·전체 이름). */
export interface BibliographyForm {
  type: string;
  title: string;
  authors: string;
  issued: string;
  "container-title": string;
  publisher: string;
  volume: string;
  issue: string;
  page: string;
  number: string;
  DOI: string;
  ISBN: string;
  ISSN: string;
}

const pad = (n: number) => String(n).padStart(2, "0");

export function formatIssued(parts: number[]): string {
  const [y, m, d] = parts;
  return [String(y), ...(m !== undefined ? [pad(m)] : []), ...(d !== undefined ? [pad(d)] : [])].join("-");
}

// family만 있는 이름은 끝에 쉼표를 붙여 다시 읽을 때 기관 이름(literal)으로 바뀌지 않게 한다.
const nameLine = (n: CslName) => ("literal" in n ? n.literal : n.given ? `${n.family}, ${n.given}` : `${n.family},`);

export function toBibliographyForm(b: Bibliography | null): BibliographyForm {
  return {
    type: b?.type ?? "",
    title: b?.title ?? "",
    authors: (b?.author ?? []).map(nameLine).join("\n"),
    issued: b?.issued ? formatIssued(b.issued["date-parts"][0]) : "",
    "container-title": b?.["container-title"] ?? "",
    publisher: b?.publisher ?? "",
    volume: b?.volume ?? "",
    issue: b?.issue ?? "",
    page: b?.page ?? "",
    number: b?.number ?? "",
    DOI: b?.DOI ?? "",
    ISBN: b?.ISBN ?? "",
    ISSN: b?.ISSN ?? "",
  };
}

export type FormResult = { ok: true; value: Bibliography | null } | { ok: false; error: string };

const FIELD_NAMES: Record<TextField, string> = {
  title: "원문 표제",
  "container-title": "게재처",
  publisher: "출판사",
  volume: "권",
  issue: "호",
  page: "쪽",
  number: "번호",
  DOI: "DOI",
  ISBN: "ISBN",
  ISSN: "ISSN",
};

function parseAuthorLine(line: string): CslName | string {
  const comma = line.indexOf(",");
  if (comma < 0) return cpLength(line) > 300 ? "저자 이름은 300자 이하로 입력해 주세요." : { literal: line };
  const family = line.slice(0, comma).trim();
  const given = line.slice(comma + 1).trim();
  if (!family) return `저자 ‘${line}’의 성을 입력해 주세요(‘성, 이름’ 형식).`;
  if (cpLength(family) > 200 || cpLength(given) > 200) return "저자의 성과 이름은 각각 200자 이하로 입력해 주세요.";
  return given ? { family, given } : { family };
}

function parseIssued(text: string): number[] | string {
  const m = /^(\d{1,4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$/.exec(text);
  const err = "발행일은 2024, 2024-03, 2024-03-15 형식으로 입력해 주세요.";
  if (!m) return err;
  const parts = m.slice(1).filter((x) => x !== undefined).map(Number);
  const [y, mo, d] = parts;
  if (y < 1 || (mo !== undefined && (mo < 1 || mo > 12))) return err;
  if (d !== undefined) {
    const date = new Date(Date.UTC(y, mo - 1, d));
    if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return "발행일이 실제 날짜가 아닙니다.";
  }
  return parts;
}

// 흔히 붙여 넣는 DOI 접두사는 떼고 저장한다(계약: https://doi.org/ 접두사 없이).
export function normalizeDoi(text: string): string {
  return text.replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "");
}

/** 폼 값을 저장값으로. 종류를 고르지 않고 다른 칸도 비었으면 null(서지정보 없음). */
export function fromBibliographyForm(f: BibliographyForm): FormResult {
  const trimmed = {
    title: f.title.trim(),
    "container-title": f["container-title"].trim(),
    publisher: f.publisher.trim(),
    volume: f.volume.trim(),
    issue: f.issue.trim(),
    page: f.page.trim(),
    number: f.number.trim(),
    DOI: normalizeDoi(f.DOI.trim()),
    ISBN: f.ISBN.trim(),
    ISSN: f.ISSN.trim(),
  } satisfies Record<TextField, string>;
  const authorLines = f.authors.split("\n").map((l) => l.trim()).filter(Boolean);
  const issued = f.issued.trim();
  const anyFilled = authorLines.length > 0 || issued !== "" || TEXT_KEYS.some((k) => trimmed[k] !== "");
  if (!f.type) return anyFilled ? { ok: false, error: "서지정보 종류를 골라 주세요. 서지정보를 지우려면 다른 칸도 모두 비워 주세요." } : { ok: true, value: null };

  const out: Bibliography = { type: f.type };
  for (const key of TEXT_KEYS) {
    const value = trimmed[key];
    if (!value) continue;
    if (cpLength(value) > TEXT_FIELDS[key]) return { ok: false, error: `${FIELD_NAMES[key]}은(는) ${TEXT_FIELDS[key]}자 이하로 입력해 주세요.` };
    out[key] = value;
  }
  if (out.DOI && !/^10\.\S+\/\S+$/.test(out.DOI)) return { ok: false, error: "DOI는 10.으로 시작하는 형식(예: 10.1000/xyz123)으로 입력해 주세요." };
  if (authorLines.length > 100) return { ok: false, error: "저자는 100명까지 입력할 수 있습니다." };
  if (authorLines.length) {
    const names: CslName[] = [];
    for (const line of authorLines) {
      const name = parseAuthorLine(line);
      if (typeof name === "string") return { ok: false, error: name };
      names.push(name);
    }
    out.author = names;
  }
  if (issued) {
    const parts = parseIssued(issued);
    if (typeof parts === "string") return { ok: false, error: parts };
    out.issued = { "date-parts": [parts] };
  }
  return { ok: true, value: out };
}

// ---- 표시 ----

const displayName = (n: CslName) => ("literal" in n ? n.literal : n.given ? `${n.family}, ${n.given}` : n.family);

/** 저자 목록 표시: 세 명까지 보이고 나머지는 ‘외 N명’. */
export function formatAuthors(names: CslName[] | undefined): string {
  if (!names?.length) return "";
  const shown = names.slice(0, 3).map(displayName).join("; ");
  return names.length > 3 ? `${shown} 외 ${names.length - 3}명` : shown;
}

/** 한 줄 인용 표시: 저자 (연도). 제목. 게재처, 권(호), 쪽. 식별자 */
export function formatBibliography(b: Bibliography): string {
  const head = [formatAuthors(b.author), b.issued ? `(${b.issued["date-parts"][0][0]})` : ""].filter(Boolean).join(" ");
  const volume = b.volume ? `${b.volume}${b.issue ? `(${b.issue})` : ""}` : b.issue ? `(${b.issue})` : "";
  const venue = [b["container-title"], b.publisher, volume, b.page].filter(Boolean).join(", ");
  const ids = [b.DOI && `DOI ${b.DOI}`, b.number, b.ISBN && `ISBN ${b.ISBN}`, b.ISSN && `ISSN ${b.ISSN}`].filter(Boolean).join(" · ");
  return [head, b.title, venue, ids].filter(Boolean).join(". ");
}
