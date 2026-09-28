import { useEffect, useMemo, useRef, useState } from "react";
import CodeMirror, { EditorView, type ReactCodeMirrorRef } from "@uiw/react-codemirror";
import { python } from "@codemirror/lang-python";
import { api } from "../api";
import type { CodeRef } from "../types";

interface Props { projectId: string; ref_: CodeRef }

/** Read-only code viewer that jumps to the referenced line. No editor reflex: the
 *  spec (ch. 18) says read-only plus "open in your editor". */
export function CodeView({ projectId, ref_ }: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cm = useRef<ReactCodeMirrorRef>(null);

  useEffect(() => {
    let live = true;
    setContent(null);
    setError(null);
    api.file(projectId, ref_.file)
      .then((f) => live && setContent(f.content))
      .catch((e: Error) => live && setError(e.message));
    return () => { live = false; };
  }, [projectId, ref_.file]);

  useEffect(() => {
    const view = cm.current?.view;
    if (!view || content === null || !ref_.line) return;
    const line = view.state.doc.line(Math.min(ref_.line, view.state.doc.lines));
    view.dispatch({ selection: { anchor: line.from }, effects: EditorView.scrollIntoView(line.from, { y: "center" }) });
  }, [content, ref_.line]);

  const extensions = useMemo(() => [python(), EditorView.lineWrapping], []);

  if (error) return <div className="error">Could not load {ref_.file}: {error}</div>;
  if (content === null) return <div className="muted">Loading {ref_.file}…</div>;
  return (
    <div className="codeview">
      <CodeMirror ref={cm} value={content} height="320px" readOnly editable={false}
        extensions={extensions} basicSetup={{ lineNumbers: true, highlightActiveLine: true, foldGutter: false }} />
    </div>
  );
}
