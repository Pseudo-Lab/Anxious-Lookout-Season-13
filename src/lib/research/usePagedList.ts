"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FetchFailure, FetchResult } from "@/lib/api/client";
import type { Page } from "@/lib/research/types";

interface PagedState<T> {
  key: string;
  items: T[];
  nextCursor: string | null;
  failure: FetchFailure | null;
  loadingMore: boolean;
}

/** cursor 목록. key가 바뀌면 처음부터 다시 불러오고, 이전 key의 늦은 응답은 버린다. */
export function usePagedList<T>(key: string, load: (cursor: string | null) => Promise<FetchResult<Page<T>>>) {
  const [state, setState] = useState<PagedState<T> | null>(null);
  const loadRef = useRef(load);
  const keyRef = useRef(key);

  useEffect(() => {
    loadRef.current = load;
    keyRef.current = key;
  });

  useEffect(() => {
    let active = true;
    void loadRef.current(null).then((res) => {
      if (!active) return;
      setState(
        res.ok
          ? { key, items: res.data.items, nextCursor: res.data.nextCursor, failure: null, loadingMore: false }
          : { key, items: [], nextCursor: null, failure: res.failure, loadingMore: false }
      );
    });
    return () => {
      active = false;
    };
  }, [key]);

  const current = state && state.key === key ? state : null;

  const loadMore = useCallback(async () => {
    if (!current?.nextCursor || current.loadingMore) return;
    const forKey = current.key;
    setState((s) => (s && s.key === forKey ? { ...s, loadingMore: true, failure: null } : s));
    const res = await loadRef.current(current.nextCursor);
    if (keyRef.current !== forKey) return;
    setState((s) => {
      if (!s || s.key !== forKey) return s;
      return res.ok
        ? { ...s, items: [...s.items, ...res.data.items], nextCursor: res.data.nextCursor, loadingMore: false }
        : { ...s, failure: res.failure, loadingMore: false };
    });
  }, [current]);

  return {
    items: current?.items ?? [],
    nextCursor: current?.nextCursor ?? null,
    failure: current?.failure ?? null,
    loaded: current !== null,
    loading: current === null || current.loadingMore,
    loadMore,
  };
}
