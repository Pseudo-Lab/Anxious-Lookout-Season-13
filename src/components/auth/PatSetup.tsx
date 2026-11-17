"use client";

import { useId, useRef, useState, useSyncExternalStore } from "react";
import { BASE_PATH } from "@/lib/constants";

const subscribe = () => () => {};
const currentOrigin = () => window.location.origin;
const serverOrigin = () => "";

function Address({ label, value }: { label: string; value: string }) {
  const id = useId();
  const field = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  return <div className="space-y-1">
    <label htmlFor={id} className="block text-xs text-stone-500">{label}</label>
    <div className="flex gap-2">
      <input id={id} ref={field} readOnly value={value} className="min-w-0 flex-1 rounded border border-stone-200 bg-white p-2 font-mono text-xs" />
      <button type="button" disabled={!value} aria-label={`${label} 복사`} className="text-sm text-indigo-600 underline" onClick={async () => {
        try { await navigator.clipboard.writeText(value); setNote("복사했습니다."); }
        catch { field.current?.select(); setNote("직접 복사하세요."); }
      }}>복사</button>
    </div>
    {note && <p role="status" className="text-xs text-stone-500">{note}</p>}
  </div>;
}

export default function PatSetup() {
  const origin = useSyncExternalStore(subscribe, currentOrigin, serverOrigin);
  return <section aria-label="연결 주소" className="space-y-3">
    <Address label="API 주소" value={origin ? `${origin}${BASE_PATH}/api/research/agent/v1` : ""} />
    <Address label="llm.md 주소" value={origin ? `${origin}${BASE_PATH}/api/research/llm.md` : ""} />
  </section>;
}
