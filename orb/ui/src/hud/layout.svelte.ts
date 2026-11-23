// Anordnung der Panels: zwei Spalten links und rechts vom Orb. Im Bearbeiten-Modus lassen sich
// Panels verschieben, in die andere Spalte setzen, einklappen und ausblenden; gemerkt wird das
// im Fenster (localStorage) – ohne Speicher gilt einfach der Standard.

export type Side = "left" | "right";

export const PANELS: Record<string, string> = {
  clock: "Zeit",
  weather: "Wetter",
  calendar: "Termine",
  homeassistant: "Smart Home",
  spotify: "Musik",
  mail: "Post",
  cards: "Anzeige",
  sources: "Quellen",
  activity: "Aktivität",
};

/** Panels, die Daten von einem Widget brauchen (ohne Widget gibt es nichts zu zeigen). */
export const WIDGET_PANELS = ["weather", "calendar", "homeassistant", "spotify", "mail"];

const DEFAULT = {
  left: ["clock", "weather", "calendar", "homeassistant", "spotify", "mail"],
  right: ["cards", "sources", "activity"],
  collapsed: {} as Record<string, boolean>,
  hidden: [] as string[],
};
const KEY = "hud.layout.v1";
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));

function load() {
  const fresh = clone(DEFAULT);
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "null");
    if (!saved) return fresh;
    const known = (list: unknown) => (Array.isArray(list) ? list.filter((id) => id in PANELS) : []);
    const left = known(saved.left), right = known(saved.right), hidden = known(saved.hidden);
    // Panels, die es beim Speichern noch nicht gab, an ihrem Standardplatz ergänzen
    for (const side of ["left", "right"] as Side[])
      for (const id of DEFAULT[side])
        if (![...left, ...right, ...hidden].includes(id)) (side === "left" ? left : right).push(id);
    return { left, right, hidden, collapsed: { ...(saved.collapsed || {}) } };
  } catch {
    return fresh;
  }
}

export const layout = $state({ ...load(), editing: false });

function save() {
  try {
    const { left, right, collapsed, hidden } = layout;
    localStorage.setItem(KEY, JSON.stringify({ left, right, collapsed, hidden }));
  } catch {
    /* nicht kritisch */
  }
}

function sideOf(id: string): Side | null {
  return layout.left.includes(id) ? "left" : layout.right.includes(id) ? "right" : null;
}

export function move(id: string, delta: -1 | 1) {
  const side = sideOf(id);
  if (!side) return;
  const list = layout[side], i = list.indexOf(id), j = i + delta;
  if (j < 0 || j >= list.length) return;
  [list[i], list[j]] = [list[j], list[i]];
  save();
}

export function swapSide(id: string) {
  const side = sideOf(id);
  if (!side) return;
  layout[side] = layout[side].filter((p) => p !== id);
  layout[side === "left" ? "right" : "left"].push(id);
  save();
}

export function toggleCollapsed(id: string) {
  layout.collapsed[id] = !layout.collapsed[id];
  save();
}

export function hide(id: string) {
  const side = sideOf(id);
  if (!side) return;
  layout[side] = layout[side].filter((p) => p !== id);
  layout.hidden.push(id);
  save();
}

export function show(id: string) {
  layout.hidden = layout.hidden.filter((p) => p !== id);
  const side: Side = DEFAULT.left.includes(id) ? "left" : "right";
  layout[side].push(id);
  save();
}

export function resetLayout() {
  Object.assign(layout, clone(DEFAULT));
  save();
}
