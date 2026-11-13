"use client";

import { useCallback, useLayoutEffect, useRef, useState, type RefObject } from "react";

// #89 대화창 자동 스크롤(stick-to-bottom). 대화창 스크롤 영역([data-chat-scroll], ChatDock)은 대화 목록과 함께 쓰므로
// 대화 하나를 보는 동안에만 이 hook이 붙는다. 라이브러리(use-stick-to-bottom 등)는 스크롤 영역과 내용을 자기 구조로
// 감싸야 해서, 대화창 구조를 바꾸지 않는 작은 구현으로 둔다.
// - 바닥(THRESHOLD 이내)에 있으면 붙어 있다(stuck). 사용자가 위로 스크롤하면 떨어지고, 바닥으로 돌아오면 다시 붙는다.
// - 붙어 있을 때만 따라간다: 항목 수가 바뀔 때, 응답을 기다리는 동안(live) 내용이 커질 때, 스크롤 영역이 줄어들 때
//   (모바일 가상 키보드), 대화를 연 직후. 응답이 없을 때 사용자가 도구 상세를 펼쳐 커진 것은 따라가지 않는다(읽던 위치 유지).
// - 이동은 즉시(scrollTop) 한다. 부드러운 스크롤은 중간 scroll 이벤트가 "위로 스크롤"로 읽혀 붙음이 풀린다.

const THRESHOLD = 48;
// 대화를 연 직후 늦게 커지는 내용(글꼴·표·코드 렌더링)도 따라가 마지막 메시지가 보이게 하는 시간.
const SETTLE_MS = 1000;

export function useStickToBottom(contentRef: RefObject<HTMLElement | null>, { enabled, live, count }: { enabled: boolean; live: boolean; count: number }) {
  const [atBottom, setAtBottom] = useState(true);
  const stuck = useRef(true);
  const liveRef = useRef(live);
  const scrollerRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    liveRef.current = live;
  });

  const toBottom = useCallback(() => {
    const el = scrollerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, []);

  useLayoutEffect(() => {
    const content = contentRef.current;
    const scroller = content?.closest<HTMLElement>("[data-chat-scroll]") ?? null;
    if (!enabled || !content || !scroller) return;
    scrollerRef.current = scroller;
    const measure = () => {
      // 숨긴 대화창(닫힘)은 크기가 0이라 판단하지 않는다.
      if (scroller.clientHeight === 0) return;
      const bottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= THRESHOLD;
      stuck.current = bottom;
      setAtBottom(bottom);
    };
    // 대화를 열면 마지막 메시지가 보이게 한다.
    stuck.current = true;
    const settleUntil = performance.now() + SETTLE_MS;
    scroller.scrollTop = scroller.scrollHeight;
    scroller.addEventListener("scroll", measure, { passive: true });
    const onContent = () => {
      if (stuck.current && (liveRef.current || performance.now() < settleUntil)) scroller.scrollTop = scroller.scrollHeight;
      else measure();
    };
    // 스크롤 영역 크기 변화(대화창 다시 열기·가상 키보드·폭 조절): 붙어 있으면 바닥을 유지한다.
    const onScroller = () => {
      if (stuck.current) scroller.scrollTop = scroller.scrollHeight;
      else measure();
    };
    const contentObserver = new ResizeObserver(onContent);
    const scrollerObserver = new ResizeObserver(onScroller);
    contentObserver.observe(content);
    scrollerObserver.observe(scroller);
    return () => {
      scroller.removeEventListener("scroll", measure);
      contentObserver.disconnect();
      scrollerObserver.disconnect();
      if (scrollerRef.current === scroller) scrollerRef.current = null;
    };
  }, [contentRef, enabled]);

  // 새 항목(내 말풍선·답변·도구)은 붙어 있으면 따라간다.
  useLayoutEffect(() => {
    if (stuck.current) toBottom();
  }, [count, toBottom]);

  /** 바닥으로 가서 다시 붙는다(보내기·"최신으로"). */
  const stick = useCallback(() => {
    stuck.current = true;
    setAtBottom(true);
    toBottom();
  }, [toBottom]);

  return { atBottom, stick };
}
