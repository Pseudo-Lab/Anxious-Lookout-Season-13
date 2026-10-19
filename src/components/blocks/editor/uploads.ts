import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey, type EditorState } from "@tiptap/pm/state";
import { Decoration, DecorationSet, type EditorView } from "@tiptap/pm/view";
import { IMAGE_MAX_BYTES, IMAGE_TYPES } from "@/lib/posts/api";

// #32 이미지 붙여넣기·끌어놓기 업로드. 올리는 동안은 문서에 노드를 넣지 않고 그 자리에 표시(decoration)만 둔다.
// 업로드가 끝나면 표시가 옮겨 간 위치에 imageId로 블록을 넣는다(blob/data URL은 저장되지 않는다).

type Action = { add: { id: string; pos: number } } | { remove: { id: string } };

export const uploadKey = new PluginKey<DecorationSet>("pb-uploads");

function placeholder() {
  const el = document.createElement("div");
  el.className = "my-2 rounded-lg bg-stone-100 px-3 py-6 text-center text-sm text-stone-500";
  el.textContent = "이미지를 올리는 중...";
  el.setAttribute("role", "status");
  return el;
}

/** 진행 중 업로드 표시가 지금 문서의 어디에 있는지(그 사이 지워졌으면 null). */
export function uploadPosition(state: EditorState, id: string): number | null {
  const found = uploadKey.getState(state)?.find(undefined, undefined, (spec) => spec.id === id);
  return found && found.length ? found[0].from : null;
}

export function pendingUploads(state: EditorState): number {
  return uploadKey.getState(state)?.find().length ?? 0;
}

export const startUpload = (view: EditorView, id: string, pos: number) =>
  view.dispatch(view.state.tr.setMeta(uploadKey, { add: { id, pos } } satisfies Action));
export const endUpload = (view: EditorView, id: string) =>
  view.dispatch(view.state.tr.setMeta(uploadKey, { remove: { id } } satisfies Action));

/** 파일 형식·크기 사전 검사(서버도 다시 검사한다). 문제가 없으면 null. */
export function imageFileProblem(f: File): string | null {
  if (!(IMAGE_TYPES as readonly string[]).includes(f.type)) return "PNG, JPEG, WebP 이미지만 올릴 수 있습니다.";
  if (f.size > IMAGE_MAX_BYTES) return "5 MiB 이하 이미지만 올릴 수 있습니다.";
  return null;
}

type OnFiles = (files: File[], pos: number) => void;

// 업로드 처리기는 편집기를 만든 뒤 storage에 연결한다(편집기 생성 시점의 콜백에 묶이지 않게).
export const ImageUploads = Extension.create<object, { onFiles: OnFiles | null }>({
  name: "imageUploads",
  addStorage: () => ({ onFiles: null }),
  addProseMirrorPlugins() {
    const storage = this.storage;
    const onFiles: OnFiles = (files, pos) => storage.onFiles?.(files, pos);
    const images = (list: FileList | null | undefined) => Array.from(list ?? []).filter((f) => f.type.startsWith("image/"));
    return [
      new Plugin<DecorationSet>({
        key: uploadKey,
        state: {
          init: () => DecorationSet.empty,
          apply(tr, set) {
            let next = set.map(tr.mapping, tr.doc);
            const action = tr.getMeta(uploadKey) as Action | undefined;
            if (action && "add" in action) {
              const deco = Decoration.widget(action.add.pos, placeholder, { id: action.add.id, side: -1 });
              next = next.add(tr.doc, [deco]);
            } else if (action && "remove" in action) {
              next = next.remove(next.find(undefined, undefined, (spec) => spec.id === action.remove.id));
            }
            return next;
          },
        },
        props: {
          decorations: (state) => uploadKey.getState(state),
          handlePaste(view, event) {
            const files = images(event.clipboardData?.files);
            if (files.length === 0) return false;
            onFiles(files, view.state.selection.from);
            return true;
          },
          handleDrop(view, event) {
            const files = images(event.dataTransfer?.files);
            if (files.length === 0) return false;
            const at = view.posAtCoords({ left: event.clientX, top: event.clientY });
            onFiles(files, at?.pos ?? view.state.selection.from);
            event.preventDefault();
            return true;
          },
        },
      }),
    ];
  },
});

export function setUploadHandler(editor: Editor, onFiles: OnFiles) {
  editor.storage.imageUploads.onFiles = onFiles;
}

declare module "@tiptap/core" {
  interface Storage {
    imageUploads: { onFiles: OnFiles | null };
  }
}
