"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api/client";
import { API_ENABLED } from "@/lib/constants";
import { EMPTY_STREAM, SseParser, applyFrame, dropPartial, type LiveTurn, type StreamEffect, type StreamFrame } from "@/lib/research/stream";

// #90 대화 스트림 연결(api-contracts/issue90-chat-stream.md v1.1). fetch + ReadableStream으로 받는다.
// EventSource는 오류 응답의 status를 읽지 못해 404(stream_disabled)·429·400을 구분할 수 없다. 재연결은 `?after=`로만 한다.
// - cursor는 이 화면의 메모리에만 둔다(새로고침·탭 재오픈은 cursor 없이 snapshot부터).
// - 브라우저 연결을 닫아도 턴은 취소되지 않는다(중지는 POST /interrupt로만).
// - 보이는 동안만 유지한다: 탭이 숨거나 대화창을 닫은 지 30초가 지나면 닫고, 다시 보이면 cursor로 이어 붙는다.
// - 404(플래그 꺼짐·이전 API)·401·403이면 이 화면에서는 스트림을 쓰지 않는다(#89 polling). 429·5xx·끊김은 polling으로
//   돌아가 있다가 늘려 가는 간격으로 다시 연결한다. 끊기면 그 사이 끝난 턴을 놓치지 않게 기록을 1회 다시 불러온다.

const HIDDEN_CLOSE_MS = 30_000;
const RETRY_MIN_MS = 2_000;
const RETRY_MAX_MS = 60_000;
const LIMIT_RETRY_MS = 30_000;

/** connecting: 연결 중(아직 polling) / live: 연결됨(polling 멈춤) / retrying: 일시 실패·끊김(polling, 다시 연결 예정) /
 *  paused: 숨김·닫힘(polling) / off: 이 화면에서 스트림 안 씀(polling만) */
export type StreamMode = "connecting" | "live" | "retrying" | "paused" | "off";

export interface StreamHandlers {
  /** 확정 신호(turn.completed·sync·끊김·runner 꺼짐): GET /sessions/{id} 1회 */
  onRefresh: () => void;
  onRunner: (status: Record<string, unknown>) => void;
  /** 진행 중인 턴 없음. wasRunning: 스트림이 진행 중으로 보던 턴이 있었다. */
  onIdle: (wasRunning: boolean) => void;
  onTurnRunning: (requestId: string) => void;
}

export function useChatStream(sessionId: string, active: boolean, handlers: StreamHandlers) {
  const [mode, setMode] = useState<StreamMode>(API_ENABLED ? "connecting" : "off");
  const [turn, setTurn] = useState<LiveTurn | null>(null);
  // 이 화면에서 스트림 응답(200)을 한 번이라도 받았는지(중지 기능이 서버에 있는지).
  const [supported, setSupported] = useState(false);
  const state = useRef(EMPTY_STREAM);
  const off = useRef(!API_ENABLED);
  const latest = useRef(handlers);
  useLayoutEffect(() => {
    latest.current = handlers;
  });

  // 보이는 동안 유지하고, 안 보이게 된 뒤 30초까지는 그대로 둔다.
  const [keep, setKeep] = useState(active);
  if (active && !keep) setKeep(true);
  useEffect(() => {
    if (active) return;
    const timer = setTimeout(() => setKeep(false), HIDDEN_CLOSE_MS);
    return () => clearTimeout(timer);
  }, [active]);

  useEffect(() => {
    if (!keep || off.current) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const dispatch = (frames: StreamFrame[]) => {
      let next = state.current;
      const effects: StreamEffect[] = [];
      for (const frame of frames) {
        const r = applyFrame(next, frame);
        next = r.state;
        effects.push(...r.effects);
      }
      state.current = next;
      setTurn(next.turn);
      const h = latest.current;
      for (const e of effects) {
        if (e.type === "refresh") h.onRefresh();
        else if (e.type === "runner") h.onRunner(e.status);
        else if (e.type === "idle") h.onIdle(e.wasRunning);
        else h.onTurnRunning(e.requestId);
      }
    };

    const retry = (base?: number) => {
      failures++;
      setMode("retrying");
      const delay = (base ?? Math.min(RETRY_MIN_MS * 2 ** (failures - 1), RETRY_MAX_MS)) * (0.85 + Math.random() * 0.3);
      timer = setTimeout(() => void connect(), delay);
    };

    const connect = async (): Promise<void> => {
      if (cancelled) return;
      controller = new AbortController();
      const cursor = state.current.cursor;
      const url = apiUrl(`/research/sessions/${encodeURIComponent(sessionId)}/events`) + (cursor ? `?after=${encodeURIComponent(cursor)}` : "");
      let res: Response;
      try {
        res = await fetch(url, { cache: "no-store", credentials: "same-origin", headers: { Accept: "text/event-stream" }, signal: controller.signal });
      } catch {
        if (!cancelled) retry();
        return;
      }
      if (cancelled) return;
      if (!res.ok) {
        void res.body?.cancel().catch(() => {});
        // cursor 형식 오류: cursor 없이 다시 연결한다(snapshot부터).
        if (res.status === 400 && cursor) {
          state.current = { ...state.current, cursor: null };
          return connect();
        }
        if (res.status === 429) return retry(LIMIT_RETRY_MS);
        if (res.status >= 500) return retry();
        // 404(stream_disabled·이전 API·없는 대화)·401·403·그 밖: 이 화면은 polling만 쓴다(인증·권한 안내는 기존 조회가 한다).
        off.current = true;
        setMode("off");
        return;
      }
      if (!res.body || !(res.headers.get("content-type") ?? "").toLowerCase().includes("text/event-stream")) {
        void res.body?.cancel().catch(() => {});
        off.current = true;
        setMode("off");
        return;
      }
      failures = 0;
      setSupported(true);
      setMode("live");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseParser();
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const frames = parser.push(decoder.decode(value, { stream: true }));
          if (frames.length > 0 && !cancelled) dispatch(frames);
        }
      } catch {
        // 끊김·중단
      }
      if (cancelled) return;
      // 서버가 닫았거나 끊겼다: 받다 만 snapshot은 버리고(cursor는 그 이전), 그 사이 끝난 턴을 기록으로 확인한 뒤 다시 붙는다.
      state.current = dropPartial(state.current);
      latest.current.onRefresh();
      retry();
    };

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      clearTimeout(timer);
      state.current = dropPartial(state.current);
      if (!off.current) setMode("paused");
    };
  }, [sessionId, keep]);

  return { mode, live: mode === "live", turn, supported };
}
