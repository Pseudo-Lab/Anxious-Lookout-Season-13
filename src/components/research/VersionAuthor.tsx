import { versionAuthorLabel, versionConversationHref, type VersionAuthor as Author } from "@/lib/research/versionAuthor";

export default function VersionAuthor({ author }: { author?: Author }) {
  const href = versionConversationHref(author);
  return (
    <p className="text-xs text-stone-500">
      작성: {versionAuthorLabel(author)}
      {href && <> · <a href={href} className="text-indigo-600 underline hover:no-underline">작성 대화로 이동</a></>}
    </p>
  );
}
