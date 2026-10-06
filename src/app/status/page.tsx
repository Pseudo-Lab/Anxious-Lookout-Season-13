import type { Metadata } from "next";
import StatusPanel from "./StatusPanel";

export const metadata: Metadata = {
  title: "서비스 상태",
};

export default function StatusPage() {
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="mb-2 text-2xl font-bold text-stone-900">서비스 상태</h1>
      <p className="mb-8 text-sm text-stone-500">
        웹과 API 서버의 연결, 배포 버전, 로그인 상태를 확인합니다.
      </p>
      <StatusPanel />
    </div>
  );
}
