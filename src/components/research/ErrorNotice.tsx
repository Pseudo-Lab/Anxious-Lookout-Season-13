import { describeFailure, type FetchFailure } from "@/lib/api/client";
import { researchErrorMessage } from "@/lib/research/api";
import Notice from "@/components/research/Notice";

// 계약 오류 코드는 안내 문구로, 그 밖의 실패(네트워크·비JSON 등)는 일반 설명으로 표시한다.
// describe: 화면 고유 문구(예: #28 글). null이면 공통 문구를 쓴다.
export default function ErrorNotice({
  failure,
  prefix,
  describe = researchErrorMessage,
}: {
  failure: FetchFailure;
  prefix?: string;
  describe?: (failure: FetchFailure) => string | null;
}) {
  const message = describe(failure) ?? describeFailure(failure);
  const tone = failure.kind === "http" && failure.code === "policy_pending" ? "warn" : "error";
  const fields = failure.kind === "http" ? failure.fields : undefined;
  return (
    <Notice tone={tone}>
      {prefix && <span className="font-medium">{prefix} </span>}
      {message}
      {fields && (
        <span className="mt-1 block">
          {fields.map((f) => (
            <span key={f.path} className="block text-xs">
              {f.path}: {f.message}
            </span>
          ))}
        </span>
      )}
    </Notice>
  );
}
