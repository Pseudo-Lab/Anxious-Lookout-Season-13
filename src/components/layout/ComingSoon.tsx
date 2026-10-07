import Link from "next/link";

interface ComingSoonProps {
  title: string;
  description: string;
}

// Supabase에서 새 API로 아직 대체하지 않은 기능의 안내 화면.
// 기능이나 기존 데이터를 폐기한 것이 아니라 단계적으로 다시 제공할 예정임을 알린다.
export default function ComingSoon({ title, description }: ComingSoonProps) {
  return (
    <section className="mx-auto max-w-xl rounded-2xl bg-white px-8 py-14 text-center shadow-sm ring-1 ring-stone-200/60">
      <span className="inline-block rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700">
        준비 중
      </span>
      <h1 className="mt-4 text-2xl font-bold text-stone-900">{title}</h1>
      <p className="mt-3 text-stone-600">{description}</p>
      <p className="mt-2 text-sm text-stone-500">
        새 서버로 옮기는 중이라 지금은 이용할 수 없습니다. 기존 기능은 단계적으로 다시 제공됩니다.
      </p>
      <div className="mt-8 flex justify-center gap-4 text-sm font-medium">
        <Link href="/" className="text-indigo-600 hover:text-indigo-800">
          홈으로
        </Link>
        <Link href="/status/" className="text-stone-500 hover:text-stone-700">
          서비스 상태
        </Link>
      </div>
    </section>
  );
}
