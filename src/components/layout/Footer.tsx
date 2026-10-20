import { SITE_NAME } from "@/lib/constants";

export default function Footer() {
  return (
    <footer className="mt-auto border-t border-white/30 bg-white/40 backdrop-blur-sm">
      {/* 아래 여백: 우하단 대화 버튼이 마지막 내용을 가리지 않게 한다. */}
      <div className="mx-auto max-w-7xl px-6 pt-6 pb-24 text-center text-sm text-stone-500">
        &copy; {new Date().getFullYear()} {SITE_NAME} &middot; Built with curiosity
      </div>
    </footer>
  );
}
