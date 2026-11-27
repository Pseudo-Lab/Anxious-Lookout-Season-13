"use client";

import { useSyncExternalStore } from "react";

// #119 A2: 로그인 확인(/api/auth/me)이 끝나기 전에도 상단 메뉴가 지난번과 같은 항목으로 자리를 잡게,
// 마지막으로 확인한 메뉴 노출 여부만 이 브라우저에 남긴다. 화면 배치용 힌트일 뿐 권한 판단에는 쓰지 않는다
// (확인이 끝나면 실제 상태로 바뀌고, 실제 권한은 서버가 판단한다). 계정 정보·토큰은 남기지 않는다.

export interface NavHint {
  signedIn: boolean;
  research: boolean;
  write: boolean;
}

const KEY = "anxious:nav-hint";
const EVENT = "anxious:nav-hint";

function read(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

export function parseNavHint(raw: string | null): NavHint | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<NavHint> | null;
    if (typeof v?.signedIn !== "boolean" || typeof v.research !== "boolean" || typeof v.write !== "boolean") return null;
    return { signedIn: v.signedIn, research: v.research, write: v.write };
  } catch {
    return null;
  }
}

/** 서버 렌더와 hydration 중에는 null(힌트 없음), 그 뒤에는 저장된 힌트. */
export function useNavHint(): NavHint | null {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  return parseNavHint(raw);
}

export function saveNavHint(hint: NavHint) {
  const raw = JSON.stringify(hint);
  try {
    if (window.localStorage.getItem(KEY) === raw) return;
    window.localStorage.setItem(KEY, raw);
  } catch {
    return;
  }
  window.dispatchEvent(new Event(EVENT));
}
