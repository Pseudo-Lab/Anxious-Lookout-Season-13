import type { NextConfig } from "next";

// 빌드 산출물 종류. 기본은 k3s용 Node 서버(standalone).
// "export"는 기존 GitHub Pages workflow용 정적 산출물(out/)이며 API가 없는 배포로 취급한다.
const output = process.env.NEXT_OUTPUT ?? "standalone";
if (output !== "standalone" && output !== "export") {
  throw new Error(`NEXT_OUTPUT must be "standalone" or "export": ${JSON.stringify(output)}`);
}

// 외부 base 경로. 전용 Host 루트 배포면 빈 값, prefix 배포면 "/m2" 형식.
// NEXT_PUBLIC_* 이므로 빌드 시점에 고정되며, 클라이언트의 /api 호출 경로에도 쓰인다.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

if (basePath !== "" && !/^\/[A-Za-z0-9._~-]+(\/[A-Za-z0-9._~-]+)*$/.test(basePath)) {
  throw new Error(
    `NEXT_PUBLIC_BASE_PATH must be empty or like "/m2" (leading slash, no trailing slash): ${JSON.stringify(basePath)}`
  );
}

const nextConfig: NextConfig = {
  output,
  basePath,
  env: {
    // 같은 origin의 /api(FastAPI)가 있는 배포인지. 정적 export에서는 인증·API 조회를 하지 않는다.
    NEXT_PUBLIC_API_ENABLED: output === "standalone" ? "true" : "false",
  },
  images: {
    unoptimized: true,
  },
  trailingSlash: true,
  poweredByHeader: false,
  // 정적 export는 응답 헤더를 설정할 수 없으므로 Node 서버에서만 지정한다.
  ...(output === "standalone" && {
    async headers() {
      return [
        {
          source: "/version.json",
          headers: [{ key: "Cache-Control", value: "no-store" }],
        },
      ];
    },
  }),
};

export default nextConfig;
