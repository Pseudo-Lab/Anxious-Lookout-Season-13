import AuthGate from "@/components/research/AuthGate";
import PatManagement from "@/components/auth/PatManagement";
import PatSetup from "@/components/auth/PatSetup";

export default function TokensPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="space-y-2"><h1 className="text-2xl font-bold text-stone-900">API 토큰</h1><p className="text-sm text-stone-500">외부 에이전트가 내 자료·문서·글에 접근할 API 토큰을 발급합니다. 발급할 때 읽기 전용 또는 읽기+저장을 고릅니다. 이전에 발급한 토큰은 읽기+저장입니다.</p></header>
      <AuthGate><div className="space-y-6"><PatSetup /><PatManagement /></div></AuthGate>
    </div>
  );
}
