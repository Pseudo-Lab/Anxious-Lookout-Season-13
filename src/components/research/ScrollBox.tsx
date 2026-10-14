"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";

interface Overflow {
  overflow: boolean;
  left: boolean;
  right: boolean;
}

const NONE: Overflow = { overflow: false, left: false, right: false };

function measure(el: HTMLElement): Overflow {
  const max = el.scrollWidth - el.clientWidth;
  if (max <= 1) return NONE;
  return { overflow: true, left: el.scrollLeft > 1, right: el.scrollLeft < max - 1 };
}

const same = (a: Overflow, b: Overflow) => a.overflow === b.overflow && a.left === b.left && a.right === b.right;

// 넓은 표·코드 블록을 감싸 그 안에서만 가로로 스크롤한다(본문·대화창 전체가 옆으로 밀리거나 잘리지 않게).
// 실제로 넘칠 때만 키보드로 초점을 받아 ←/→로 스크롤할 수 있게 한다(넘치지 않으면 Tab 순서에 끼지 않는다).
// 표는 가려진 쪽 가장자리에 그림자를 보여 옆에 내용이 더 있음을 알린다(스크롤 막대가 숨겨진 환경 포함).
export default function ScrollBox({
  as = "div",
  label,
  className,
  children,
}: {
  as?: "div" | "pre";
  label: string;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const [state, setState] = useState<Overflow>(NONE);
  const update = () => {
    const el = ref.current;
    if (!el) return;
    const next = measure(el);
    setState((prev) => (same(prev, next) ? prev : next));
  };
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 관찰을 시작할 때 한 번, 이후 크기가 바뀔 때마다 다시 잰다.
    const observer = new ResizeObserver(() => {
      const next = measure(el);
      setState((prev) => (same(prev, next) ? prev : next));
    });
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, []);
  const props = {
    className,
    onScroll: update,
    tabIndex: state.overflow ? 0 : undefined,
    role: state.overflow ? "region" : undefined,
    "aria-label": state.overflow ? `${label} (가로로 스크롤)` : undefined,
    "data-overflow": state.overflow || undefined,
  };
  if (as === "pre") {
    return (
      <pre ref={ref as RefObject<HTMLPreElement>} {...props}>
        {children}
      </pre>
    );
  }
  return (
    <div className="scroll-frame" data-more-left={state.left || undefined} data-more-right={state.right || undefined}>
      <div ref={ref as RefObject<HTMLDivElement>} {...props}>
        {children}
      </div>
    </div>
  );
}
