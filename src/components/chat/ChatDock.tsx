"use client";

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import dynamic from "next/dynamic";
import { useAuth } from "@/hooks/useAuth";
import { API_ENABLED } from "@/lib/constants";
import { useChat } from "@/components/chat/ChatProvider";
import { PANEL_DEFAULT, PANEL_STEP, PANEL_WIDE, clampPanelWidth, panelBounds } from "@/components/chat/panelWidth";

// 대화창 내용은 처음 열 때 불러온다(모든 페이지의 초기 로딩·조회 부담을 줄인다).
const ChatPanel = dynamic(() => import("@/components/chat/ChatPanel"), {
  ssr: false,
  loading: () => <p className="p-4 text-sm text-stone-500">대화창을 불러오는 중...</p>,
});

const MOBILE_QUERY = "(max-width: 767px)";

function subscribeMedia(onChange: () => void) {
  const mq = window.matchMedia(MOBILE_QUERY);
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

function useIsMobile() {
  return useSyncExternalStore(
    subscribeMedia,
    () => window.matchMedia(MOBILE_QUERY).matches,
    () => false
  );
}

function subscribeResize(onChange: () => void) {
  window.addEventListener("resize", onChange);
  return () => window.removeEventListener("resize", onChange);
}

// 스크롤 막대를 뺀 화면 폭. 창 크기·확대 비율이 바뀌면 다시 읽는다.
function useViewportWidth() {
  return useSyncExternalStore(
    subscribeResize,
    () => document.documentElement.clientWidth,
    () => 1280
  );
}

// 작은 화면에서 입력 키보드가 올라오면 보이는 영역 높이에 맞춘다(지원하지 않으면 100dvh).
function useVisibleHeight(active: boolean) {
  const [height, setHeight] = useState<number | null>(null);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!active || !vv) return;
    const update = () => setHeight(vv.height);
    update();
    vv.addEventListener("resize", update);
    return () => {
      vv.removeEventListener("resize", update);
      setHeight(null);
    };
  }, [active]);
  return height;
}

const PANEL_ID = "chat-panel";

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.getClientRects().length > 0);
}

/** 모든 페이지 우하단의 대화 버튼과 대화창. */
export default function ChatDock() {
  const { status, user } = useAuth();
  const chat = useChat();
  const isMobile = useIsMobile();
  const viewport = useViewportWidth();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  // 한 번 연 뒤에는 닫아도 대화창을 그대로 두어(숨김) 진행 중 대화·입력·확인 상태가 이어지게 한다.
  // 계정이 바뀌면 내리고, 그 계정에서 열 때 새로 그린다(닫힌 채로 다른 계정의 조회를 하지 않게).
  const accountKey = user?.accountId ?? status;
  const [mountedFor, setMountedFor] = useState<string | null>(chat.open ? accountKey : null);
  if (chat.open && mountedFor !== accountKey) setMountedFor(accountKey);
  const mounted = mountedFor === accountKey;
  const visibleHeight = useVisibleHeight(chat.open && isMobile);

  // 넓은 화면 폭: 끄는 동안은 화면에만 반영하고(live) 놓을 때 저장한다. 저장한 폭은 지금 화면에 맞춰 줄여 그린다.
  // 끌기는 시작한 계정에 묶는다. 끄는 도중 같은 탭의 계정이 바뀌면 그 끌기는 버리고, 늦게 오는 놓기·포커스 이탈도
  // 새 계정의 저장 폭을 바꾸지 못하게 한다.
  const [live, setLive] = useState<{ width: number; account: string } | null>(null);
  const drag = useRef<{ pointerId: number; account: string; startX: number; startWidth: number; current: number } | null>(null);
  // 계정이 바뀌거나 대화창을 닫거나 작은 화면이 되면 진행 중이던 폭 조절은 버린다(조절 상태가 남지 않게).
  if (live !== null && (live.account !== accountKey || !chat.open || isMobile || !mounted)) setLive(null);
  const resizing = live !== null;
  const bounds = panelBounds(viewport);
  const width = clampPanelWidth(live?.width ?? chat.width ?? PANEL_DEFAULT, viewport);
  const wideWidth = clampPanelWidth(PANEL_WIDE, viewport);
  const canWiden = wideWidth > clampPanelWidth(PANEL_DEFAULT, viewport);
  const isWide = canWiden && width >= wideWidth;

  // 열고 닫을 때 포커스: 열면 대화창 제목, 닫으면 버튼으로 돌려준다(처음 그릴 때는 옮기지 않는다).
  const previous = useRef(chat.open);
  useEffect(() => {
    if (previous.current === chat.open) return;
    previous.current = chat.open;
    if (chat.open) headingRef.current?.focus();
    else buttonRef.current?.focus();
  }, [chat.open]);

  // 넓은 화면: 대화창이 본문을 가리지 않게 대화창 폭만큼 오른쪽 여백을 둔다(globals.css). 폭과 같은 프레임에 맞춘다.
  useLayoutEffect(() => {
    if (!chat.open || isMobile) return;
    const root = document.documentElement;
    root.dataset.chatOpen = "";
    root.style.setProperty("--chat-panel-width", `${width}px`);
    return () => {
      delete root.dataset.chatOpen;
      root.style.removeProperty("--chat-panel-width");
    };
  }, [chat.open, isMobile, width]);

  // 작은 화면: 대화창이 화면 전체를 쓰므로 뒤 페이지를 조작·스크롤할 수 없게 한다.
  useEffect(() => {
    if (!chat.open || !isMobile) return;
    const root = document.documentElement;
    const behind = Array.from(document.querySelectorAll<HTMLElement>("body > header, body > main, body > footer"));
    behind.forEach((el) => el.setAttribute("inert", ""));
    root.style.overflow = "hidden";
    return () => {
      behind.forEach((el) => el.removeAttribute("inert"));
      root.style.overflow = "";
    };
  }, [chat.open, isMobile]);

  // 작은 화면의 모달 대화창: Tab/Shift+Tab을 대화창 안에서 순환하고, 포커스가 밖(body 등)으로 나갔어도
  // Tab은 대화창으로 되돌리고 Escape는 닫는다(문서 단위로 받는다).
  const { setOpen, setWidth } = chat;
  useEffect(() => {
    if (!chat.open || !isMobile) return;
    const onKey = (e: KeyboardEvent) => {
      const panel = panelRef.current;
      if (!panel) return;
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables(panel);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (!active || !panel.contains(active)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      } else if (e.shiftKey && (active === first || active === headingRef.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [chat.open, isMobile, setOpen]);

  // 끌기가 아직 살아 있는지: 시작한 계정이 지금 계정이고, 대화창 닫기·작은 화면 전환·계정 변경으로 live가 지워지지 않았다.
  // 이미 취소된 끌기의 늦은 이동·놓기·포인터 해제·창 포커스 이탈은 아무것도 바꾸지 않는다.
  const dragAlive = (d: { account: string }) => live !== null && live.account === d.account && d.account === accountKey;

  // 폭 조절 끝내기. commit이면 마지막 폭을 저장하고, 아니면(취소) 시작 폭으로 되돌린다.
  // 살아 있는 끌기만 저장한다(setWidth는 지금 계정의 상태를 바꾼다).
  function finishResize(commit: boolean) {
    const d = drag.current;
    drag.current = null;
    setLive(null);
    if (d && commit && dragAlive(d) && d.current !== d.startWidth) setWidth(d.current);
  }
  const finishRef = useRef(finishResize);
  useLayoutEffect(() => {
    finishRef.current = finishResize;
  });

  // 끄는 동안: 문서 전체 커서·글자 선택 막기(globals.css), Escape는 취소(대화창은 닫지 않음), 창을 벗어나면 그 폭으로 끝낸다.
  useEffect(() => {
    if (!resizing) return;
    const root = document.documentElement;
    root.dataset.chatResizing = "";
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      finishRef.current(false);
    };
    const onBlur = () => finishRef.current(true);
    document.addEventListener("keydown", onKey, true);
    window.addEventListener("blur", onBlur);
    return () => {
      delete root.dataset.chatResizing;
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("blur", onBlur);
    };
  }, [resizing]);

  function onResizeStart(e: ReactPointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { pointerId: e.pointerId, account: accountKey, startX: e.clientX, startWidth: width, current: width };
    setLive({ width, account: accountKey });
  }
  function onResizeMove(e: ReactPointerEvent<HTMLDivElement>) {
    const d = drag.current;
    // 누른 채 끄는 포인터만 따른다(마우스를 올리기만 한 이동은 무시).
    if (!d || e.pointerId !== d.pointerId || !e.currentTarget.hasPointerCapture(e.pointerId)) return;
    // 취소된 끌기(다른 계정에서 시작, 닫기·작은 화면 전환)는 이어 가지 않고 버린다(놓을 때도 저장하지 않음).
    if (!dragAlive(d)) {
      drag.current = null;
      return;
    }
    // 대화창은 오른쪽에 붙어 있으므로 경계를 왼쪽으로 끌면 넓어진다.
    d.current = clampPanelWidth(d.startWidth + d.startX - e.clientX, viewport);
    setLive({ width: d.current, account: d.account });
  }
  function onResizeKey(e: ReactKeyboardEvent<HTMLDivElement>) {
    const step = e.shiftKey ? PANEL_STEP * 4 : PANEL_STEP;
    const next =
      e.key === "ArrowLeft" ? width + step
      : e.key === "ArrowRight" ? width - step
      : e.key === "Home" ? bounds.min
      : e.key === "End" ? bounds.max
      : null;
    if (next === null) return;
    e.preventDefault();
    setWidth(clampPanelWidth(next, viewport));
  }

  if (!API_ENABLED) return null;

  const iconButton =
    "flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-stone-500 hover:bg-stone-100 hover:text-stone-800 md:h-8 md:w-8";

  return (
    <>
      {!chat.open && (
        <button
          ref={buttonRef}
          type="button"
          onClick={() => chat.setOpen(true)}
          aria-label="Codex 대화창 열기"
          aria-expanded={false}
          aria-controls={PANEL_ID}
          className="fixed bottom-5 right-5 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-indigo-600 text-white shadow-lg ring-4 ring-white/70 hover:bg-indigo-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700"
        >
          <svg aria-hidden="true" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth={1.8} viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M8.625 9.75h6.75M8.625 13.5h4.5M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z"
            />
          </svg>
        </button>
      )}
      {mounted && (
        <section
          id={PANEL_ID}
          ref={panelRef}
          hidden={!chat.open}
          role={isMobile ? "dialog" : "complementary"}
          aria-modal={isMobile ? true : undefined}
          aria-labelledby="chat-panel-title"
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              chat.setOpen(false);
            }
          }}
          style={isMobile ? (visibleHeight ? { height: visibleHeight } : undefined) : { width }}
          className={`fixed z-50 flex flex-col bg-white ${
            isMobile ? "inset-x-0 top-0 h-[100dvh]" : "inset-y-0 right-0 border-l border-stone-200 shadow-[-8px_0_24px_-16px_rgba(28,25,23,0.25)]"
          }`}
        >
          {!isMobile && (
            // 왼쪽 경계: 끌어서(포인터) 또는 ←/→·Home/End 키로 폭 조절, 두 번 누르면 기본 폭.
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="대화창 폭 조절"
              aria-controls={PANEL_ID}
              aria-valuemin={bounds.min}
              aria-valuemax={bounds.max}
              aria-valuenow={width}
              aria-valuetext={`${width}픽셀`}
              tabIndex={0}
              title="끌거나 ←/→ 키로 폭 조절 · 두 번 누르면 기본 폭"
              onPointerDown={onResizeStart}
              onPointerMove={onResizeMove}
              onPointerUp={() => finishResize(true)}
              onPointerCancel={() => finishResize(false)}
              onLostPointerCapture={() => finishResize(true)}
              onKeyDown={onResizeKey}
              onDoubleClick={() => setWidth(null)}
              data-resizing={resizing || undefined}
              className="group absolute inset-y-0 -left-2 z-10 w-4 cursor-col-resize touch-none outline-none"
            >
              <span
                aria-hidden="true"
                className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-transparent transition-colors group-hover:bg-indigo-300 group-focus-visible:bg-indigo-500 group-data-[resizing]:bg-indigo-500"
              />
              <span
                aria-hidden="true"
                className="absolute left-1/2 top-1/2 h-10 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-stone-300 transition-colors group-hover:bg-indigo-400 group-focus-visible:bg-indigo-600 group-data-[resizing]:bg-indigo-600"
              />
            </div>
          )}
          <div className="flex shrink-0 items-center gap-1 border-b border-stone-100 py-1 pl-4 pr-2">
            <h2 id="chat-panel-title" ref={headingRef} tabIndex={-1} className="flex-1 text-sm font-semibold text-stone-900 outline-none">
              Codex 대화
            </h2>
            {!isMobile && canWiden && (
              <button
                type="button"
                onClick={() => setWidth(isWide ? null : wideWidth)}
                aria-label={isWide ? "대화창 기본 폭으로" : "대화창 넓게"}
                title={isWide ? "기본 폭으로" : "넓게"}
                className={iconButton}
              >
                <svg aria-hidden="true" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                  {isWide ? (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h6m12 0h-6M6 9l3 3-3 3m12-6-3 3 3 3" />
                  ) : (
                    <path strokeLinecap="round" strokeLinejoin="round" d="M3 12h18M7 8l-4 4 4 4m10-8 4 4-4 4" />
                  )}
                </svg>
              </button>
            )}
            <button type="button" onClick={() => chat.setOpen(false)} aria-label="대화창 닫기" title="닫기" className={iconButton}>
              <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          {/* 세로로만 스크롤한다. 넓은 표·코드는 각자 안에서 가로로 스크롤한다. */}
          <div data-chat-scroll className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-4">
            {/* 계정이 바뀌면 이전 계정의 조회 결과·입력·늦은 응답을 버리고 새로 그린다. */}
            <ChatPanel key={accountKey} />
          </div>
        </section>
      )}
    </>
  );
}
