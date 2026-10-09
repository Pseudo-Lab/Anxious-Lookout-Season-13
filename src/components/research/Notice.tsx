import type { ReactNode } from "react";

const TONES = {
  info: "bg-stone-50 text-stone-600 ring-stone-200",
  warn: "bg-amber-50 text-amber-800 ring-amber-200",
  error: "bg-red-50 text-red-700 ring-red-200",
  success: "bg-emerald-50 text-emerald-800 ring-emerald-200",
} as const;

export default function Notice({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return (
    <p
      role={tone === "error" || tone === "warn" ? "alert" : "status"}
      className={`rounded-lg px-4 py-3 text-sm ring-1 ${TONES[tone]}`}
    >
      {children}
    </p>
  );
}
