import AuthGate from "@/components/research/AuthGate";
import PatManagement from "@/components/auth/PatManagement";
import PatSetup from "@/components/auth/PatSetup";

export default function TokensPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="space-y-2"><h1 className="text-2xl font-bold text-stone-900">API 토큰</h1><p className="text-sm text-stone-500">외부 에이전트가 내 자료·문서·글을 읽거나 저장할 수 있도록 API 토큰을 발급합니다.</p></header>
      <AuthGate><div className="space-y-6"><PatSetup /><PatManagement /></div></AuthGate>
    </div>
  );
}
