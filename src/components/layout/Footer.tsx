import { SITE_NAME } from "@/lib/constants";

export default function Footer() {
  return (
    <footer className="mt-auto border-t border-stone-200 bg-white">
      {/* 아래 여백: 우하단 대화 버튼(56px + 20px)이 마지막 줄을 가리지 않게 한다. */}
      <p className="mx-auto max-w-7xl px-6 pt-6 pb-24 text-center text-sm text-stone-600">
        <span>&copy; {new Date().getFullYear()} {SITE_NAME}</span>
        <span aria-hidden="true" className="mx-2">&middot;</span>
        <span>Built with curiosity</span>
      </p>
    </footer>
  );
}
