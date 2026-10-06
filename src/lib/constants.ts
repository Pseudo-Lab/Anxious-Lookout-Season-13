export const SITE_NAME = "초조한 전망대";
export const SITE_DESCRIPTION =
  "스터디 그룹의 관찰과 기록 — 함께 읽고, 정리하고, 공유합니다.";

// next.config.ts의 basePath와 같은 값. fetch나 <a>처럼 next/link를 거치지 않는 경로에 붙인다.
export const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";
