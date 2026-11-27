// #119 A2: 글 목록·글 상세를 불러오는 동안 "불러오는 중…" 문구 대신 같은 모양의 자리 표시를 둔다(레이아웃 이동을 줄인다).
// 화면 읽기 도구에는 상태 문구 하나만 알린다.

const bar = "animate-pulse rounded bg-stone-200/80";

export function PostCardSkeleton() {
  return (
    <div aria-hidden="true" className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-stone-200/60">
      <div className={`${bar} h-6 w-2/3`} />
      <div className={`${bar} mt-4 h-4 w-full`} />
      <div className={`${bar} mt-2 h-4 w-5/6`} />
      <div className={`${bar} mt-5 h-4 w-40`} />
    </div>
  );
}

export function PostListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="flex flex-col gap-4">
      <p role="status" className="sr-only">
        글을 불러오는 중입니다.
      </p>
      {Array.from({ length: count }, (_, i) => (
        <PostCardSkeleton key={i} />
      ))}
    </div>
  );
}

export function PostArticleSkeleton() {
  return (
    <div className="mx-auto max-w-[45rem]">
      <p role="status" className="sr-only">
        글을 불러오는 중입니다.
      </p>
      <div aria-hidden="true">
        <div className={`${bar} h-9 w-3/4`} />
        <div className={`${bar} mt-5 h-4 w-48`} />
        <div className="mt-10 space-y-3">
          {["w-full", "w-full", "w-11/12", "w-full", "w-4/5", "w-full", "w-2/3"].map((w, i) => (
            <div key={i} className={`${bar} h-5 ${w}`} />
          ))}
        </div>
      </div>
    </div>
  );
}
