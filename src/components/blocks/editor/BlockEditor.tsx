"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { EditorContent, useEditor, useEditorState, type Editor, type JSONContent } from "@tiptap/react";
import { uploadPostImage } from "@/lib/posts/api";
import { postErrorMessage } from "@/lib/posts/errors";
import { useMutation } from "@/lib/research/hooks";
import { uuidV4 } from "@/lib/research/idempotency";
import { TextSelection } from "@tiptap/pm/state";
import { CODE_LANGUAGES, FONT_SIZES, type Align } from "@/lib/blocks/types";
import { textColorStyle } from "@/lib/blocks/userColor";
import { blockExtensions, isAllowedLink, youtubeIdFrom } from "./schema";
import { EmbedWithView, PostImageWithView } from "./nodeViews";
import { ImageUploads, endUpload, setUploadHandler, imageFileProblem, pendingUploads, startUpload, uploadPosition } from "./uploads";

// #32 고정 블록 편집기(Tiptap v3 headless). 저장 형식 변환(adapter)은 이 컴포넌트 밖에서 한다.
// 작성 화면에서만 client-only로 불러온다(공개 페이지는 자체 renderer를 쓴다).

const REJECT_TEXT: Record<string, string> = {
  "list-depth": "목록은 3단계까지만 들여쓸 수 있습니다.",
  merged: "표 칸 병합은 지원하지 않습니다.",
  colwidth: "표 열 너비는 지정할 수 없습니다.",
  "header-row": "머리글 행은 표의 첫 행만 될 수 있습니다.",
  "mixed-header": "머리글은 행 단위로만 지정할 수 있습니다.",
  ragged: "표의 모든 행은 열 수가 같아야 합니다.",
  size: "표는 100행×20열까지 만들 수 있습니다.",
};

const COLORS = ["#1C1917", "#DC2626", "#EA580C", "#CA8A04", "#16A34A", "#2563EB", "#7C3AED", "#DB2777"];
const CELL_BACKGROUNDS = ["#FEF3C7", "#DCFCE7", "#DBEAFE", "#F3E8FF", "#F5F5F4"];
const COLUMN_PRESETS: { label: string; widths: number[] }[] = [
  { label: "1:1", widths: [1, 1] },
  { label: "2:1", widths: [2, 1] },
  { label: "1:2", widths: [1, 2] },
  { label: "1:1:1", widths: [1, 1, 1] },
  { label: "1:2:1", widths: [1, 2, 1] },
];
const ALIGN_TEXT: Record<Align, string> = { left: "왼쪽", center: "가운데", right: "오른쪽" };

function Btn({ label, onClick, active, disabled, children }: { label: string; onClick: () => void; active?: boolean; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      aria-pressed={active}
      disabled={disabled}
      // 버튼을 눌러도 편집기 선택(커서)이 사라지지 않게 한다
      onMouseDown={(e) => e.preventDefault()}
      onClick={onClick}
      className={`min-w-8 rounded px-2 py-1 text-sm ${active ? "bg-indigo-100 text-indigo-800" : "text-stone-900 hover:bg-stone-100"} disabled:opacity-40`}
    >
      {children}
    </button>
  );
}

const Sep = () => <span aria-hidden className="mx-1 h-5 w-px bg-stone-200" />;

/**
 * 글자 서식(굵게·기울임·밑줄·취소선·코드·링크·글자 색·글자 크기). 고정 툴바와 선택 툴바(F1)가 같은 버튼을 쓴다.
 * 코드 블록 안에서는 서식을 쓸 수 없고, 이미지·영상 설명에는 링크를 걸 수 없다(계약).
 */
function FormatButtons({ editor, compact }: { editor: Editor; compact?: boolean }) {
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"),
      italic: e.isActive("italic"),
      underline: e.isActive("underline"),
      strike: e.isActive("strike"),
      code: e.isActive("code"),
      link: e.isActive("link"),
      inCode: e.isActive("codeBlock"),
      inCaption: e.isActive("postImage") || e.isActive("embed"),
      fontSize: (e.getAttributes("textStyle").fontSize as number | null | undefined) ?? null,
    }),
  });
  const c = () => editor.chain().focus();
  const sizeByPointer = useRef(false);
  function setLink() {
    if (s.link) {
      c().unsetLink().run();
      return;
    }
    const url = window.prompt("링크 주소 (http, https, mailto 또는 #앵커)");
    if (url === null) return;
    if (!isAllowedLink(url.trim())) {
      window.alert("쓸 수 없는 주소입니다. http, https, mailto 주소나 #앵커만 링크할 수 있습니다.");
      return;
    }
    c().setLink({ href: url.trim() }).run();
  }
  const off = s.inCode;
  return (
    <>
      <Btn label="굵게" active={s.bold} disabled={off} onClick={() => c().toggleBold().run()}><b>B</b></Btn>
      <Btn label="기울임" active={s.italic} disabled={off} onClick={() => c().toggleItalic().run()}><i>I</i></Btn>
      <Btn label="밑줄" active={s.underline} disabled={off} onClick={() => c().toggleUnderline().run()}><u>U</u></Btn>
      <Btn label="취소선" active={s.strike} disabled={off} onClick={() => c().toggleStrike().run()}><s>S</s></Btn>
      <Btn label="인라인 코드" active={s.code} disabled={off} onClick={() => c().toggleCode().run()}><code>{"<>"}</code></Btn>
      <Btn label={s.link ? "링크 해제" : "링크"} active={s.link} disabled={off || s.inCaption} onClick={setLink}>🔗</Btn>
      <select
        aria-label="글자 크기"
        value={s.fontSize ?? ""}
        disabled={off}
        // 목록에 포커스가 가도 편집기 선택은 state에 남아 크기는 그 선택에 바로 적용된다.
        // 키보드로 고르는 동안(WebKit은 방향키마다 change)은 포커스를 목록에 두고, Enter·Escape로 끝낼 때 편집기로 돌아간다(리뷰 full round6).
        // 마우스로 목록을 열어 고른 경우만 고르자마자 편집기로 돌아간다.
        onPointerDown={() => (sizeByPointer.current = true)}
        onKeyDown={() => (sizeByPointer.current = false)}
        // 확정·취소 키를 뗄 때 편집기로 돌아간다(누를 때 막으면 열린 목록의 선택 확정까지 막힐 수 있다)
        onKeyUp={(e) => {
          if (e.key === "Enter" || e.key === "Escape") editor.commands.focus();
        }}
        onChange={(e) => {
          const chain = sizeByPointer.current ? c() : editor.chain();
          sizeByPointer.current = false;
          if (e.target.value) chain.setTextSize(Number(e.target.value)).run();
          else chain.unsetTextSize().run();
        }}
        className="rounded border border-stone-300 px-1 py-1 text-sm"
      >
        <option value="">크기 기본</option>
        {FONT_SIZES.map((pt) => (
          <option key={pt} value={pt}>
            {pt}pt
          </option>
        ))}
      </select>
      <span className="flex items-center gap-0.5 px-1" role="group" aria-label="글자 색">
        {(compact ? COLORS.slice(0, 5) : COLORS).map((hex) => (
          <button
            key={hex}
            type="button"
            aria-label={`글자 색 ${hex}`}
            disabled={off}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => c().setTextColor(hex).run()}
            // 견본은 지금 테마에서 글자가 실제로 보일 색으로 칠한다(#38 D-38-1, 다크는 밝기 보정 색)
            className="pb-swatch h-4 w-4 rounded-full ring-1 ring-stone-300 disabled:opacity-40"
            style={textColorStyle(hex)}
          />
        ))}
        <Btn label="글자 색 지우기" disabled={off} onClick={() => c().unsetTextColor().run()}>⌀</Btn>
      </span>
    </>
  );
}

/**
 * #36 F1 선택 툴바: 글자를 끌어 선택하면 선택 영역 위(자리가 없으면 아래)에 서식 메뉴를 띄운다.
 * 손가락 입력(모바일)에서는 띄우지 않고 고정 툴바를 쓴다(OS 선택 메뉴와 겹치지 않게).
 * 마우스를 누르고 있는 동안(끄는 중)에는 숨기고, 놓은 뒤에 보인다.
 */
function SelectionMenu({ editor }: { editor: Editor }) {
  const menu = useRef<HTMLDivElement>(null);
  const [pressing, setPressing] = useState(false);
  const [coarse] = useState(coarsePointer);
  // 메뉴 안(글자 크기 목록 등)으로 포커스가 옮겨 간 동안에도 메뉴와 선택을 유지한다(리뷰 round2 M2).
  // 편집기 선택은 state에 남아 있고, 메뉴의 명령은 focus()로 편집기에 돌아가 그 선택에 적용한다.
  const [menuFocused, setMenuFocused] = useState(false);
  const sel = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      const s = e.state.selection;
      const text = s instanceof TextSelection && !s.empty && e.isEditable;
      return text ? { from: s.from, to: s.to, focused: e.isFocused } : null;
    },
    equalityFn: (a, b) => a?.from === b?.from && a?.to === b?.to && a?.focused === b?.focused,
  });
  const range = sel && (sel.focused || menuFocused) ? sel : null;
  // 선택이 사라지면 메뉴 포커스 표시도 내린다(메뉴가 포커스를 가진 채 사라지면 blur가 오지 않는다)
  if (!sel && menuFocused) setMenuFocused(false);

  useEffect(() => {
    const dom = editor.view.dom;
    const down = () => setPressing(true);
    const up = () => setPressing(false);
    dom.addEventListener("mousedown", down);
    window.addEventListener("mouseup", up);
    return () => {
      dom.removeEventListener("mousedown", down);
      window.removeEventListener("mouseup", up);
    };
  }, [editor]);

  // 화면 위치는 DOM에 직접 쓴다(스크롤마다 다시 그리지 않는다)
  useLayoutEffect(() => {
    const el = menu.current;
    if (!range || !el) return;
    const place = () => {
      const start = editor.view.coordsAtPos(range.from);
      const end = editor.view.coordsAtPos(range.to);
      const above = start.top - el.offsetHeight - 8;
      const top = above >= 8 ? above : end.bottom + 8;
      const mid = (Math.min(start.left, end.left) + Math.max(start.right, end.right)) / 2;
      el.style.top = `${top}px`;
      el.style.left = `${Math.max(8, Math.min(mid - el.offsetWidth / 2, window.innerWidth - el.offsetWidth - 8))}px`;
      el.style.visibility = "visible";
    };
    place();
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [editor, range, pressing]);

  if (!range || pressing || coarse) return null;
  return (
    <div
      ref={menu}
      role="toolbar"
      aria-label="선택한 글자 서식"
      onFocus={() => setMenuFocused(true)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setMenuFocused(false);
      }}
      // 메뉴 안을 눌러도 편집기 선택이 풀리지 않게 한다(select는 예외: 목록을 열어야 한다)
      onMouseDown={(e) => {
        // 목록(select)은 포커스를 가져가야 열린다. 편집기 blur로 메뉴가 먼저 사라지지 않게 포커스가 옮겨 가기 전에 표시해 둔다.
        // Firefox는 목록을 누를 때 대상이 <option>일 수 있다
        if (e.target instanceof Element && e.target.closest("select")) setMenuFocused(true);
        else e.preventDefault();
      }}
      className="fixed z-30 flex max-w-[calc(100vw-1rem)] flex-wrap items-center gap-0.5 rounded-lg bg-white p-1 shadow-lg ring-1 ring-stone-300"
      style={{ visibility: "hidden", top: 0, left: 0 }}
    >
      <FormatButtons editor={editor} compact />
    </div>
  );
}

const coarsePointer = () => typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches && !window.matchMedia("(pointer: fine)").matches;

function Toolbar({ editor, onPickImage, uploading }: { editor: Editor; onPickImage: () => void; uploading: boolean }) {
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      block: e.isActive("heading", { level: 2 }) ? "h2" : e.isActive("heading", { level: 3 }) ? "h3" : e.isActive("heading", { level: 4 }) ? "h4" : e.isActive("heading") ? "h" : e.isActive("codeBlock") ? "code" : "p",
      bullet: e.isActive("bulletList"),
      ordered: e.isActive("orderedList"),
      quote: e.isActive("blockquote"),
      inCell: e.isActive("tableCell") || e.isActive("tableHeader"),
      inTable: e.isActive("table"),
      inColumns: e.isActive("columns"),
      columnCount: (() => {
        const { $from } = e.state.selection;
        for (let d = $from.depth; d > 0; d--) if ($from.node(d).type.name === "columns") return $from.node(d).childCount;
        return 0;
      })(),
      align: ((e.isActive("tableCell") || e.isActive("tableHeader")
        ? (e.getAttributes("tableCell").align ?? e.getAttributes("tableHeader").align)
        : (e.getAttributes("paragraph").textAlign ?? e.getAttributes("heading").textAlign)) ?? "left") as Align,
      language: (e.getAttributes("codeBlock").language as string | undefined) ?? "plain",
      canUndo: e.can().undo(),
      canRedo: e.can().redo(),
    }),
  });
  const c = () => editor.chain().focus();

  function setBlock(v: string) {
    if (v === "p") c().setParagraph().run();
    else if (v === "code") c().setCodeBlock({ language: "plain" }).run();
    else c().setHeading({ level: Number(v.slice(1)) as 2 | 3 | 4 }).run();
  }
  function setAlign(a: Align) {
    if (s.inCell) c().setCellAttribute("align", a).run();
    else c().setTextAlign(a).run();
  }
  function insertYouTube() {
    const input = window.prompt("YouTube 주소 또는 영상 ID");
    if (input === null) return;
    const id = youtubeIdFrom(input);
    if (!id) {
      window.alert("YouTube 영상 주소를 알아보지 못했습니다(시작 시각은 지원하지 않습니다).");
      return;
    }
    c().insertYouTube(id).run();
  }

  return (
    <div role="toolbar" aria-label="서식" className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 rounded-t-lg border-b border-stone-200 bg-white/95 p-1.5 backdrop-blur">
      <select aria-label="블록 종류" value={s.block} onChange={(e) => setBlock(e.target.value)} className="rounded border border-stone-300 px-1 py-1 text-sm" disabled={s.inCell}>
        <option value="p">본문</option>
        <option value="h2">제목 2</option>
        <option value="h3">제목 3</option>
        <option value="h4">제목 4</option>
        {s.block === "h" && <option value="h">다른 제목</option>}
        <option value="code">코드</option>
      </select>
      {s.block === "code" && (
        <select aria-label="코드 언어" value={s.language} onChange={(e) => c().updateAttributes("codeBlock", { language: e.target.value }).run()} className="rounded border border-stone-300 px-1 py-1 text-sm">
          {CODE_LANGUAGES.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      )}
      <Sep />
      <FormatButtons editor={editor} />
      <Sep />
      {(["left", "center", "right"] as Align[]).map((a) => (
        <Btn key={a} label={`${ALIGN_TEXT[a]} 정렬`} active={s.align === a} onClick={() => setAlign(a)}>
          {a === "left" ? "⇤" : a === "center" ? "↔" : "⇥"}
        </Btn>
      ))}
      <Sep />
      <Btn label="글머리 목록" active={s.bullet} disabled={s.inCell} onClick={() => c().toggleBulletList().run()}>•</Btn>
      <Btn label="번호 목록" active={s.ordered} disabled={s.inCell} onClick={() => c().toggleOrderedList().run()}>1.</Btn>
      <Btn label="들여쓰기" disabled={!(s.bullet || s.ordered)} onClick={() => c().sinkListItem("listItem").run()}>→</Btn>
      <Btn label="내어쓰기" disabled={!(s.bullet || s.ordered)} onClick={() => c().liftListItem("listItem").run()}>←</Btn>
      <Btn label="인용" active={s.quote} disabled={s.inCell} onClick={() => c().toggleBlockquote().run()}>❝</Btn>
      <Btn label="구분선" disabled={s.inCell} onClick={() => c().setHorizontalRule().run()}>—</Btn>
      <Sep />
      {/* #44: 이미지 패널을 없앴으므로 이미지 넣기는 이 버튼(파일 선택 → 업로드 → 커서 위치)·붙여넣기·끌어놓기로 한다 */}
      <Btn label="이미지 올리기" disabled={uploading || s.inCell} onClick={onPickImage}>🖼 이미지</Btn>
      <Btn label="YouTube 영상" disabled={s.inCell} onClick={insertYouTube}>▶</Btn>
      <Btn label="표 넣기" disabled={s.inTable || s.inColumns} onClick={() => c().insertTableAfterBlock({ rows: 3, cols: 3, withHeaderRow: true }).run()}>▦</Btn>
      {!s.inColumns && !s.inTable && (
        <select aria-label="단 나누기" value="" onChange={(e) => c().insertColumns(COLUMN_PRESETS[Number(e.target.value)].widths).run()} className="rounded border border-stone-300 px-1 py-1 text-sm">
          <option value="" disabled>단 나누기</option>
          {COLUMN_PRESETS.map((p, i) => (
            <option key={p.label} value={i}>
              {p.label}
            </option>
          ))}
        </select>
      )}
      <Sep />
      <Btn label="블록 위로" onClick={() => c().moveBlock(-1).run()}>↑</Btn>
      <Btn label="블록 아래로" onClick={() => c().moveBlock(1).run()}>↓</Btn>
      <Btn label="되돌리기" disabled={!s.canUndo} onClick={() => c().undo().run()}>↶</Btn>
      <Btn label="다시 하기" disabled={!s.canRedo} onClick={() => c().redo().run()}>↷</Btn>
      {s.inTable && (
        <div role="group" aria-label="표" className="flex w-full flex-wrap items-center gap-0.5 border-t border-stone-100 pt-1 text-xs">
          <span className="px-1 text-stone-500">표</span>
          <Btn label="아래에 행 추가" onClick={() => c().addRowAfter().run()}>+행</Btn>
          <Btn label="행 삭제" onClick={() => c().deleteRow().run()}>−행</Btn>
          <Btn label="오른쪽에 열 추가" onClick={() => c().addColumnAfterKeepWidths().run()}>+열</Btn>
          <Btn label="열 삭제" onClick={() => c().deleteColumnKeepWidths().run()}>−열</Btn>
          {/* #36 F6: 열 폭(가중치). 표 칸 오른쪽 테두리를 끌어도 된다 */}
          <Btn label="열 넓게" onClick={() => c().resizeColumn(1).run()}>↔+</Btn>
          <Btn label="열 좁게" onClick={() => c().resizeColumn(-1).run()}>↔−</Btn>
          <Btn label="열 폭 같게" onClick={() => c().evenColumns().run()}>↔=</Btn>
          <Btn label="첫 행 머리글" onClick={() => c().toggleHeaderRow().run()}>머리글</Btn>
          <span className="flex items-center gap-0.5 px-1" role="group" aria-label="칸 배경">
            {CELL_BACKGROUNDS.map((hex) => (
              <button key={hex} type="button" aria-label={`칸 배경 ${hex}`} onMouseDown={(e) => e.preventDefault()} onClick={() => c().setCellAttribute("background", hex).run()} className="h-4 w-4 rounded ring-1 ring-stone-300" style={{ backgroundColor: hex }} />
            ))}
            <Btn label="칸 배경 지우기" onClick={() => c().setCellAttribute("background", null).run()}>⌀</Btn>
          </span>
          <Btn label="표 삭제" onClick={() => c().deleteTable().run()}>표 삭제</Btn>
        </div>
      )}
      {s.inColumns && (
        <div role="group" aria-label="단" className="flex w-full flex-wrap items-center gap-0.5 border-t border-stone-100 pt-1 text-xs">
          <span className="px-1 text-stone-500">단 비율</span>
          {COLUMN_PRESETS.filter((p) => p.widths.length === s.columnCount).map((p) => (
            <Btn key={p.label} label={`단 비율 ${p.label}`} onClick={() => c().setColumnWidths(p.widths).run()}>{p.label}</Btn>
          ))}
        </div>
      )}
    </div>
  );
}

export interface BlockEditorProps {
  initialContent?: JSONContent;
  onChange?: (doc: JSONContent) => void;
  onUploading?: (uploading: boolean) => void;
  onReady?: (editor: Editor) => void;
  readOnly?: boolean;
}

export default function BlockEditor({ initialContent, onChange, onUploading, onReady, readOnly }: BlockEditorProps) {
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const { run } = useMutation();
  const runRef = useRef(run);
  const onChangeRef = useRef(onChange);
  const onUploadingRef = useRef(onUploading);
  useEffect(() => {
    runRef.current = run;
    onChangeRef.current = onChange;
    onUploadingRef.current = onUploading;
  });
  const editorRef = useRef<Editor | null>(null);

  async function uploadFiles(files: File[], pos: number) {
    const editor = editorRef.current;
    if (!editor) return;
    for (const f of files) {
      const problem = imageFileProblem(f);
      if (problem) {
        setNotice(problem);
        continue;
      }
      const id = uuidV4();
      startUpload(editor.view, id, pos);
      setUploading((n) => n + 1);
      const res = await runRef.current(`post-image:upload:${f.name}:${f.size}:${f.lastModified}:${f.type}`, (ctx) => uploadPostImage(f, ctx));
      setUploading((n) => n - 1);
      if (editor.isDestroyed) return;
      const at = uploadPosition(editor.state, id);
      endUpload(editor.view, id);
      if (!res.ok) {
        setNotice(postErrorMessage(res.failure));
        continue;
      }
      if (at === null || !editor.isEditable) {
        // #44: 내 이미지 목록이 없으므로 다시 올리도록 안내한다
        setNotice("올렸지만 넣을 자리가 없어져 본문에 넣지 않았습니다. 이미지를 다시 올려 주세요.");
        continue;
      }
      const alt = f.name.replace(/\.[^.]+$/, "").replace(/[\r\n]+/g, " ").trim().slice(0, 100);
      editor.chain().insertPostImage({ imageId: res.data.id, alt }, at).run();
      pos = uploadPosition(editor.state, id) ?? editor.state.selection.from;
    }
  }

  useEffect(() => {
    onUploadingRef.current?.(uploading > 0);
  }, [uploading]);

  const editor = useEditor({
    immediatelyRender: false,
    editable: !readOnly,
    extensions: [
      ...blockExtensions({ placeholder: "본문을 쓰세요", onReject: (r) => setNotice(REJECT_TEXT[r] ?? "지원하지 않는 구조입니다.") }).map((e) =>
        e.name === "postImage" ? PostImageWithView : e.name === "embed" ? EmbedWithView : e
      ),
      ImageUploads,
    ],
    content: initialContent ?? { type: "doc", content: [{ type: "paragraph" }] },
    editorProps: {
      attributes: { class: "pb-editor prose max-w-none min-h-[24rem] px-4 py-3 focus:outline-none", "aria-label": "본문", "aria-multiline": "true", role: "textbox" },
    },
    onUpdate: ({ editor: e }) => onChangeRef.current?.(e.getJSON()),
  });

  useEffect(() => {
    editorRef.current = editor;
    if (!editor) return;
    setUploadHandler(editor, (files, pos) => void uploadFiles(files, pos));
    onReady?.(editor);
  });
  useEffect(() => {
    editor?.setEditable(!readOnly);
  }, [editor, readOnly]);

  if (!editor) return <p className="text-sm text-stone-500">편집기를 불러오는 중...</p>;
  return (
    <div className="rounded-lg bg-white ring-1 ring-stone-300">
      {!readOnly && <Toolbar editor={editor} uploading={pendingUploads(editor.state) > 0} onPickImage={() => fileInput.current?.click()} />}
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        aria-label="본문 이미지 파일"
        className="sr-only"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          e.target.value = "";
          if (files.length) void uploadFiles(files, editor.state.selection.from);
        }}
      />
      {notice && (
        <p role="status" className="flex items-center justify-between gap-2 border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
          {notice}
          <button type="button" onClick={() => setNotice(null)} className="text-amber-700 hover:text-amber-900">
            닫기
          </button>
        </p>
      )}
      <EditorContent editor={editor} />
      {!readOnly && <SelectionMenu editor={editor} />}
    </div>
  );
}
