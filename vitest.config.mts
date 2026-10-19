import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 단위 테스트(현재 #32 블록 문서 adapter). Docker 안에서만 실행한다(CLAUDE.md 테스트 규칙).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
