// Mirrors the JSON from pdf_tagfix.dump_tree.tree_to_json.

export interface TextRun {
  text: string;
  size: number;
  font: string;
  bold: boolean;
}

export interface McidRef {
  page: number | null;
  mcid: number;
  runs: TextRun[];
  found: boolean;
}

export interface ObjRef {
  objr: string;
  page: number | null;
}

export interface StructNode {
  type: string;
  role: string | null;
  page: number | null;
  text: string;
  font_sizes: number[];
  bold: "bold" | "not bold" | "partly bold" | null;
  alt: string | null;
  actual_text: string | null;
  lang: string | null;
  title: string | null;
  kids: (StructNode | ObjRef)[];
  mcids: McidRef[];
}

export interface TreeResponse {
  file: string;
  pages: number;
  lang: string | null;
  root: StructNode;
  missing_mcids: number;
  unreferenced_mcids: number;
}

export function isNode(kid: StructNode | ObjRef): kid is StructNode {
  return "type" in kid;
}
