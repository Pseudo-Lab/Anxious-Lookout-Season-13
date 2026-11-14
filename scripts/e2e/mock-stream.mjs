// #90 모의 대화 스트림(api-contracts/issue90-chat-stream.md v1.1의 front 검증용 흉내). 실제 runner·API 동작 증거가 아니다.
// - 턴 로그 이벤트에는 `id: R:seq`, snapshot은 snapshot.end에만 id, runner·idle·sync는 id 없음.
// - cursor(`?after=R:n`)가 진행 중이거나 막 끝난 턴이고 n이 범위 안이면 n+1부터 다시 보내고, 아니면 snapshot을 보낸다.
// - 항목 id는 완료 뒤 기록 항목 id와 같다(검증 V1이 성립하는 경우를 흉내).

const turns = new Map(); // sessionId -> turn
const listeners = new Map(); // sessionId -> Set<res>
export const streamStats = { connects: [], open: 0 };

function write(res, f) {
  let out = "";
  if (f.id) out += `id: ${f.id}\n`;
  out += `event: ${f.event}\ndata: ${JSON.stringify(f.data)}\n\n`;
  res.write(out);
}

function track(t, event, data) {
  if (event === "item.started") t.items.set(data.itemId, { kind: data.kind, status: "inProgress", text: "" });
  else if (event === "item.delta") {
    const it = t.items.get(data.itemId);
    if (it) it.text += data.text;
  } else if (event === "item.completed" || event === "item.status") {
    const it = t.items.get(data.itemId);
    if (it) it.status = data.status;
  }
}

/** 새 턴. 이전 턴의 예약 이벤트는 버린다. */
export function beginTurn(sessionId, requestId) {
  cancelTurn(sessionId);
  turns.set(sessionId, { requestId, seq: 0, events: [], items: new Map(), running: true, completed: null, timers: [] });
}

export function emit(sessionId, event, data) {
  const t = turns.get(sessionId);
  if (!t) return;
  t.seq++;
  const f = { id: `${t.requestId}:${t.seq}`, event, data: { requestId: t.requestId, ...data } };
  t.events.push(f);
  track(t, event, f.data);
  for (const res of listeners.get(sessionId) ?? []) write(res, f);
}

export function later(sessionId, ms, fn) {
  const t = turns.get(sessionId);
  if (t) t.timers.push(setTimeout(fn, ms));
}

export function cancelTurn(sessionId) {
  const t = turns.get(sessionId);
  if (t) for (const timer of t.timers) clearTimeout(timer);
}

/** 기록을 저장한 뒤 보내는 확정 신호. */
export function endTurn(sessionId, data) {
  const t = turns.get(sessionId);
  if (!t) return;
  cancelTurn(sessionId);
  t.running = false;
  emit(sessionId, "turn.completed", data);
  t.completed = data;
}

export function liveText(sessionId, itemId) {
  return turns.get(sessionId)?.items.get(itemId)?.text ?? "";
}

function snapshot(res, t) {
  write(res, { event: "snapshot.begin", data: { requestId: t.requestId, turnState: t.running ? "running" : "completed", seq: t.seq } });
  for (const [itemId, it] of t.items) {
    write(res, { event: "snapshot.item", data: { requestId: t.requestId, itemId, kind: it.kind, status: it.status } });
    if (it.text) write(res, { event: "snapshot.item", data: { requestId: t.requestId, itemId, text: it.text } });
  }
  if (!t.running && t.completed) write(res, { event: "turn.completed", data: { requestId: t.requestId, ...t.completed } });
  write(res, { id: `${t.requestId}:${t.seq}`, event: "snapshot.end", data: { requestId: t.requestId, seq: t.seq } });
}

/** GET /sessions/{id}/events. runner는 `/codex/status`와 같은 값. */
export function openStream(req, res, sessionId, after, runner) {
  streamStats.connects.push({ sessionId, after, at: Date.now() });
  streamStats.open++;
  res.writeHead(200, { "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store", "X-Accel-Buffering": "no" });
  write(res, { event: "runner", data: runner });
  const t = turns.get(sessionId);
  const m = after ? /^(.+):(\d+)$/.exec(after) : null;
  if (t && m && m[1] === t.requestId && Number(m[2]) <= t.seq) {
    for (const f of t.events.filter((e) => Number(e.id.split(":").at(-1)) > Number(m[2]))) write(res, f);
  } else if (t && (t.running || (m && m[1] === t.requestId))) {
    snapshot(res, t);
  } else {
    write(res, { event: "idle", data: { requestId: null, turnState: null } });
  }
  let set = listeners.get(sessionId);
  if (!set) listeners.set(sessionId, (set = new Set()));
  set.add(res);
  const heartbeat = setInterval(() => res.write(": hb\n\n"), 15000);
  req.on("close", () => {
    clearInterval(heartbeat);
    set.delete(res);
    streamStats.open--;
  });
}
