import { useState } from "react";
import { isNode, type McidRef, type StructNode, type TextRun } from "./types";

interface Props {
  root: StructNode;
  showMcids: boolean;
  onSelectPage: (page: number) => void;
}

export default function TreeView({ root, showMcids, onSelectPage }: Props) {
  return (
    <ul className="tree-list" role="tree">
      {root.kids.filter(isNode).map((kid, i) => (
        <Node key={i} node={kid} showMcids={showMcids} onSelectPage={onSelectPage} />
      ))}
    </ul>
  );
}

function sizeLabel(sizes: number[]): string {
  return sizes.map((s) => `${+s.toFixed(1)}pt`).join("/");
}

function problems(node: StructNode): string[] {
  const out: string[] = [];
  const kind = node.role ?? node.type;
  if (kind === "Figure" && !node.alt) out.push(node.alt === "" ? "empty Alt" : "no Alt");
  const missing = node.mcids.filter((m) => !m.found).length;
  if (missing) out.push(`${missing} MCID${missing > 1 ? "s" : ""} not found`);
  return out;
}

function Node({ node, showMcids, onSelectPage }: { node: StructNode } & Omit<Props, "root">) {
  const [open, setOpen] = useState(true);
  const kids = node.kids.filter(isNode);
  const hasKids = kids.length > 0 || (showMcids && node.mcids.length > 0);
  const issues = problems(node);
  const heading = /^H[1-6]?$/.test(node.role ?? node.type);

  return (
    <li role="treeitem" aria-expanded={hasKids ? open : undefined}>
      <div
        className={`row${issues.length ? " has-issue" : ""}`}
        onClick={() => node.page && onSelectPage(node.page)}
        title={node.page ? `Go to page ${node.page}` : undefined}
      >
        <button
          className="toggle"
          aria-label={open ? "Collapse" : "Expand"}
          style={{ visibility: hasKids ? "visible" : "hidden" }}
          onClick={(e) => {
            e.stopPropagation();
            setOpen(!open);
          }}
        >
          {open ? "▾" : "▸"}
        </button>
        <span className={`tag${heading ? " heading" : ""}`}>
          {node.type}
          {node.role && <span className="role"> → {node.role}</span>}
        </span>
        {node.page && <span className="page">p.{node.page}</span>}
        {node.text && <span className="text">{node.text}</span>}
        {node.font_sizes.length > 0 && <span className="chip">{sizeLabel(node.font_sizes)}</span>}
        {node.bold && <span className={`chip${node.bold === "bold" ? " bold" : ""}`}>{node.bold}</span>}
        {node.alt && <span className="attr">alt="{node.alt}"</span>}
        {node.actual_text && <span className="attr">actualtext="{node.actual_text}"</span>}
        {node.lang && <span className="attr">lang="{node.lang}"</span>}
        {issues.map((p) => (
          <span key={p} className="issue">
            {p}
          </span>
        ))}
      </div>
      {open && hasKids && (
        <ul role="group">
          {showMcids && node.mcids.map((m, i) => <Mcid key={`m${i}`} mcid={m} />)}
          {kids.map((kid, i) => (
            <Node key={i} node={kid} showMcids={showMcids} onSelectPage={onSelectPage} />
          ))}
        </ul>
      )}
    </li>
  );
}

function runSummary(runs: TextRun[]): string {
  const sizes = [...new Set(runs.map((r) => `${+r.size.toFixed(1)}pt`))].join("/");
  const fonts = [...new Set(runs.map((r) => r.font))].join(", ");
  return [sizes, fonts].filter(Boolean).join(" · ");
}

function Mcid({ mcid }: { mcid: McidRef }) {
  const text = mcid.runs.map((r) => r.text).join("").trim();
  return (
    <li className={`mcid${mcid.found ? "" : " missing"}`}>
      MCID {mcid.mcid}
      {mcid.page && ` (p.${mcid.page})`}:{" "}
      {mcid.found ? (
        <>
          <span className="text">{text ? `"${text}"` : "(empty)"}</span>{" "}
          <span className="muted">{runSummary(mcid.runs)}</span>
        </>
      ) : (
        "not found in page content"
      )}
    </li>
  );
}
