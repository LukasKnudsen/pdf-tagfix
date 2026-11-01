import { useEffect, useState } from "react";
import TreeView from "./TreeView";
import type { TreeResponse } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? res.statusText);
  return data as T;
}

export default function App() {
  const [files, setFiles] = useState<string[]>([]);
  const [folder, setFolder] = useState("");
  const [file, setFile] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [showMcids, setShowMcids] = useState(false);

  useEffect(() => {
    getJson<{ folder: string; files: string[] }>("/api/files")
      .then((d) => {
        setFolder(d.folder);
        setFiles(d.files);
        if (d.files.length) setFile(d.files[0]);
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!file) return;
    setTree(null);
    setError(null);
    setPage(1);
    getJson<TreeResponse>(`/api/tree?file=${encodeURIComponent(file)}`)
      .then(setTree)
      .catch((e: Error) => setError(e.message));
  }, [file]);

  return (
    <div className="app">
      <header>
        <h1>pdf-tagfix</h1>
        <label>
          File{" "}
          <select value={file ?? ""} onChange={(e) => setFile(e.target.value)}>
            {files.map((f) => (
              <option key={f}>{f}</option>
            ))}
          </select>
        </label>
        <label>
          <input type="checkbox" checked={showMcids} onChange={(e) => setShowMcids(e.target.checked)} />{" "}
          Show MCIDs
        </label>
        <span className="folder">{folder}</span>
      </header>

      {tree && (
        <div className="summary">
          <span>{tree.pages} pages</span>
          <span className={tree.lang ? "" : "warn"}>language: {tree.lang ?? "not set"}</span>
          <span className={tree.missing_mcids ? "error" : ""}>MCIDs without content: {tree.missing_mcids}</span>
          <span className={tree.unreferenced_mcids ? "warn" : ""}>
            untagged content: {tree.unreferenced_mcids}
          </span>
        </div>
      )}

      <main>
        <section className="tree" aria-label="Tag tree">
          {error && <p className="error-box">{error}</p>}
          {!error && !tree && file && <p>Loading…</p>}
          {!error && !files.length && <p>No PDFs in {folder}.</p>}
          {tree && <TreeView root={tree.root} showMcids={showMcids} onSelectPage={setPage} />}
        </section>
        <section className="viewer" aria-label="PDF">
          {file && (
            <iframe
              key={`${file}#${page}`}
              title={file}
              src={`/api/pdf?file=${encodeURIComponent(file)}#page=${page}`}
            />
          )}
        </section>
      </main>
    </div>
  );
}
