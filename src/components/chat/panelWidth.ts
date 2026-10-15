// 넓은 화면(768px 이상) 대화창 폭 규칙. 대화창은 오른쪽에 붙고 본문은 그만큼 오른쪽 여백을 둔다.
// 사용자가 고른 폭은 그대로 보관하고, 화면에 그릴 때만 지금 화면 폭에 맞춰 줄인다(창을 다시 넓히면 고른 폭으로 돌아간다).

/** 대화창 최소 폭: 입력창·버튼·표 스크롤이 쓸 수 있는 폭. */
export const PANEL_MIN = 320;
/** 처음 열 때의 폭. */
export const PANEL_DEFAULT = 416;
/** "넓게" 버튼의 폭(화면이 좁으면 최대 폭까지). */
export const PANEL_WIDE = 720;
/** 대화창 옆에 남기는 본문 최소 폭: 탐색·본문·입력창이 쓸 수 있게 남긴다. */
export const CONTENT_MIN = 360;
/** 키보드 한 번에 바꾸는 폭(Shift: 4배). */
export const PANEL_STEP = 16;

export function panelBounds(viewport: number): { min: number; max: number } {
  return { min: PANEL_MIN, max: Math.max(PANEL_MIN, Math.floor(viewport - CONTENT_MIN)) };
}

export function clampPanelWidth(width: number, viewport: number): number {
  const { min, max } = panelBounds(viewport);
  return Math.round(Math.min(max, Math.max(min, width)));
}
