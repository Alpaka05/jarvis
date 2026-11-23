// Zerlegt Text in Stücke für die Anzeige: Absätze, Aufzählungen, **fett**, *kursiv*, `code`,
// Links und Beleg-Marken wie [3]. Die Stücke werden als Text eingesetzt, nie als HTML – so bleibt
// alles wirkungslos, was aus einer Webseite oder Mail in eine Antwort oder Karte geraten ist.

export type Inline =
  | { t: "text" | "b" | "i" | "code"; v: string }
  | { t: "url"; v: string }
  | { t: "cite"; ns: number[] };

export type Block = { t: "p"; inline: Inline[] } | { t: "list"; ordered: boolean; items: Inline[][] };

const INLINE =
  /\*\*([^*\n]+)\*\*|\*([^*\s][^*\n]*)\*|`([^`\n]+)`|(https?:\/\/[^\s<>"')\]}]+)|\[(\d{1,4}(?:\s*[,;]\s*\d{1,4})*)\]/g;
const BULLET = /^\s*([-•*]|\d{1,2}[.)])\s+(.*)$/;

export function inline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ t: "text", v: text.slice(last, at) });
    last = at + m[0].length;
    if (m[1] !== undefined) out.push({ t: "b", v: m[1] });
    else if (m[2] !== undefined) out.push({ t: "i", v: m[2] });
    else if (m[3] !== undefined) out.push({ t: "code", v: m[3] });
    else if (m[4] !== undefined) {
      const url = m[4].replace(/[.,;:!?]+$/, ""); // Satzzeichen am Ende gehören nicht zum Link
      out.push({ t: "url", v: url });
      last -= m[4].length - url.length;
    } else out.push({ t: "cite", ns: m[5].split(/[,;]/).map((n) => Number(n.trim())) });
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

export function blocks(text: string): Block[] {
  const out: Block[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push({ t: "p", inline: inline(para.join("\n")) });
    para = [];
  };
  for (const line of (text || "").replace(/\r/g, "").split("\n")) {
    const bullet = line.match(BULLET);
    if (bullet) {
      flush();
      const ordered = /\d/.test(bullet[1]);
      const prev = out[out.length - 1];
      if (prev?.t === "list" && prev.ordered === ordered) prev.items.push(inline(bullet[2]));
      else out.push({ t: "list", ordered, items: [inline(bullet[2])] });
    } else if (!line.trim()) flush();
    else para.push(line);
  }
  flush();
  return out;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
