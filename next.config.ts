import type { NextConfig } from "next";

// 외부 base 경로. 전용 Host 루트 배포면 빈 값, prefix 배포면 "/m2" 형식.
// NEXT_PUBLIC_* 이므로 빌드 시점에 고정되며, 클라이언트의 /api 호출 경로에도 쓰인다.
const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

if (basePath !== "" && !/^\/[A-Za-z0-9._~-]+(\/[A-Za-z0-9._~-]+)*$/.test(basePath)) {
  throw new Error(
    `NEXT_PUBLIC_BASE_PATH must be empty or like "/m2" (leading slash, no trailing slash): ${JSON.stringify(basePath)}`
  );
}

const nextConfig: NextConfig = {
  output: "standalone",
  basePath,
  images: {
    unoptimized: true,
  },
  trailingSlash: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/version.json",
        headers: [{ key: "Cache-Control", value: "no-store" }],
      },
    ];
  },
};

export default nextConfig;
