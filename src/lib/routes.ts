// #119 B2(D2): 화면 주소는 경로형이다. 예전 쿼리 주소(`?id=`)는 같은 화면의 새 주소로 넘긴다(LegacyIdRedirect).
// next/link·router에 넘기는 값이다(basePath는 Next가 붙인다). 서버가 만드는 공개 주소(publicUrl)는 예전 형식일 수 있다.

const enc = encodeURIComponent;

export const materialPath = (id: string) => `/research/material/${enc(id)}/`;
export const documentPath = (id: string) => `/research/document/${enc(id)}/`;
export const writePostPath = (id: string) => `/write/${enc(id)}/`;
export const publicDocumentPath = (id: string) => `/public/document/${enc(id)}/`;

export const researchTabPath = (tab: "materials" | "documents") => `/research/?tab=${tab}`;

export type DetailRoute = "material" | "document" | "post" | "public_document";

export const DETAIL_PATH: Record<DetailRoute, (id: string) => string> = {
  material: materialPath,
  document: documentPath,
  post: writePostPath,
  public_document: publicDocumentPath,
};

// 경로의 마지막 조각(id). /research/material/<id>/ 형태가 아니면 null.
const DETAIL_PATTERNS: [DetailRoute, RegExp][] = [
  ["material", /^\/research\/material\/([^/]+)\/$/],
  ["document", /^\/research\/document\/([^/]+)\/$/],
  ["post", /^\/write\/([^/]+)\/$/],
  ["public_document", /^\/public\/document\/([^/]+)\/$/],
];

// 같은 자리의 고정 화면(new 등)은 id가 아니다.
const RESERVED = new Set(["new"]);

/** 경로형 상세 주소를 해석한다(대화창 문맥 등). 새 화면(new)·목록은 null. */
export function parseDetailPath(pathname: string): { route: DetailRoute; id: string } | null {
  for (const [route, re] of DETAIL_PATTERNS) {
    const m = re.exec(pathname);
    if (!m || RESERVED.has(m[1])) continue;
    try {
      return { route, id: decodeURIComponent(m[1]) };
    } catch {
      return null;
    }
  }
  return null;
}
