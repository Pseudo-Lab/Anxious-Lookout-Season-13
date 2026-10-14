"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import dynamic from "next/dynamic";
import { useAuth } from "@/hooks/useAuth";
import { API_ENABLED } from "@/lib/constants";
import { useChat } from "@/components/chat/ChatProvider";

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

  // 열고 닫을 때 포커스: 열면 대화창 제목, 닫으면 버튼으로 돌려준다(처음 그릴 때는 옮기지 않는다).
  const previous = useRef(chat.open);
  useEffect(() => {
    if (previous.current === chat.open) return;
    previous.current = chat.open;
    if (chat.open) headingRef.current?.focus();
    else buttonRef.current?.focus();
  }, [chat.open]);

  // 넓은 화면: 대화창이 본문을 가리지 않게 오른쪽 여백을 둔다(globals.css).
  // 작은 화면: 대화창이 화면 전체를 쓰므로 뒤 페이지를 조작·스크롤할 수 없게 한다.
  useEffect(() => {
    const root = document.documentElement;
    if (!chat.open) return;
    if (!isMobile) {
      root.dataset.chatOpen = chat.wide ? "wide" : "normal";
      return () => {
        delete root.dataset.chatOpen;
      };
    }
    const behind = Array.from(document.querySelectorAll<HTMLElement>("body > header, body > main, body > footer"));
    behind.forEach((el) => el.setAttribute("inert", ""));
    root.style.overflow = "hidden";
    return () => {
      behind.forEach((el) => el.removeAttribute("inert"));
      root.style.overflow = "";
    };
  }, [chat.open, chat.wide, isMobile]);

  // 작은 화면의 모달 대화창: Tab/Shift+Tab을 대화창 안에서 순환하고, 포커스가 밖(body 등)으로 나갔어도
  // Tab은 대화창으로 되돌리고 Escape는 닫는다(문서 단위로 받는다).
  const { setOpen } = chat;
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

  if (!API_ENABLED) return null;

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
          style={isMobile && visibleHeight ? { height: visibleHeight } : undefined}
          className={`fixed z-50 flex flex-col bg-white shadow-2xl ring-1 ring-stone-200 ${
            isMobile ? "inset-x-0 top-0 h-[100dvh]" : `inset-y-0 right-0 ${chat.wide ? "w-[min(48rem,60vw)]" : "w-[26rem]"}`
          }`}
        >
          <div className="flex items-center gap-2 border-b border-stone-200 px-4 py-2">
            <h2 id="chat-panel-title" ref={headingRef} tabIndex={-1} className="flex-1 text-sm font-semibold text-stone-800 outline-none">
              Codex 대화
            </h2>
            {!isMobile && (
              <button
                type="button"
                onClick={() => chat.setWide(!chat.wide)}
                className="min-h-11 rounded-lg px-3 text-xs text-stone-500 hover:bg-stone-100 hover:text-stone-800"
              >
                {chat.wide ? "좁게 보기" : "넓게 보기"}
              </button>
            )}
            <button
              type="button"
              onClick={() => chat.setOpen(false)}
              aria-label="대화창 닫기"
              className="flex h-11 w-11 items-center justify-center rounded-lg text-stone-500 hover:bg-stone-100 hover:text-stone-800"
            >
              <svg aria-hidden="true" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
            {/* 계정이 바뀌면 이전 계정의 조회 결과·입력·늦은 응답을 버리고 새로 그린다. */}
            <ChatPanel key={accountKey} />
          </div>
        </section>
      )}
    </>
  );
}
