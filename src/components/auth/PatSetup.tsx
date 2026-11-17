"use client";

import { useSyncExternalStore } from "react";
import { apiUrl } from "@/lib/api/client";
import { BASE_PATH } from "@/lib/constants";

const subscribe = () => () => {};
const exampleOrigin = () => "https://platform.example";
const currentOrigin = () => window.location.protocol === "https:" && (!window.location.port || window.location.port === "443") ? window.location.origin : exampleOrigin();

export default function PatSetup() {
  const origin = useSyncExternalStore(subscribe, currentOrigin, exampleOrigin);
  const quote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;
  const client = `python3 -I scripts/pat-client.py --origin ${quote(origin)}${BASE_PATH ? ` --base-path ${quote(BASE_PATH)}` : ""}`;
  const authenticated = `${client} --credential-file /private/research.pat`;
  return (
    <section aria-label="외부 에이전트 연결 안내" className="space-y-3 rounded-xl bg-stone-50 p-4 text-sm text-stone-900">
      <h2 className="font-semibold">외부 에이전트 연결</h2>
      <p>연구 API 주소: <code className="break-all">{origin}{BASE_PATH}/api/research/agent/v1/</code></p>
      <p>Python 3가 필요합니다. HTTPS 운영 사이트에서 사용하세요. 예시의 사이트 주소와 비밀 파일 경로는 사용 환경에 맞게 바꾸세요.</p>
      <ol className="list-decimal space-y-2 pl-5">
        <li>에이전트의 <code>anxious-research-pat</code> skill 폴더에 <a href={apiUrl("/research/client/SKILL.md")} download="SKILL.md" className="text-indigo-600 underline">SKILL.md</a>를 저장하고, 그 안의 <code>scripts/</code> 폴더에 <a href={apiUrl("/research/client/pat-client.py")} download="pat-client.py" className="text-indigo-600 underline">pat-client.py</a>를 저장하세요. <a href={apiUrl("/research/client/operations.json")} download="operations.json" className="text-indigo-600 underline">작업별 입력 형식</a>도 확인할 수 있습니다.</li>
        <li>발급한 값을 비공개 파일에 단독으로 저장하세요. 파일 권한은 <code>0600</code>, 상위 폴더는 본인만 접근 가능해야 합니다. 토큰을 명령 인자·환경 변수·프롬프트에 넣지 마세요.</li>
        <li>작업 형식을 확인한 뒤 조회하거나 저장하세요. 결과가 불확실하면 현재 버전 기록을 확인하고 직접 재시도 여부를 정하세요.</li>
      </ol>
      <pre className="overflow-auto whitespace-pre rounded-lg bg-stone-900 p-3 text-xs text-stone-100">{`${authenticated} schema research_list\nprintf '%s' '{"type":"document","limit":30}' | ${authenticated} research_list\n${client} public --path /api/public/documents`}</pre>
      <p className="text-xs text-stone-500">토큰 없이 공개 콘텐츠를 읽을 수 있습니다. 비공개 데이터는 토큰 소유자 본인 것만 접근하며, 게시·철회는 웹에서 직접 해야 합니다. 토큰이 폐기·만료되거나 권한이 바뀌면 호출을 멈추고 이 화면에서 확인하세요.</p>
    </section>
  );
}
