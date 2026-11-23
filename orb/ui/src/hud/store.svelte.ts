// Zustand des Dashboards und die Verbindung zu Jarvis (ws://127.0.0.1, siehe core/hud.py).
import type { Card, Confirm, OrbState, Status, Turn } from "./protocol";

const params = new URLSearchParams(location.search);
const PORT = Number((window as any).ORB_PORT || params.get("port") || 8765);
const TOKEN = String((window as any).ORB_TOKEN || params.get("token") || "");
const RETRY_MS = 2000;
const NOTICE_MS = 7000;
const MAX_TURNS = 60;

const NO_STATUS: Status = {
  provider: "", fallback: "", busy: false, voice_mode: false, voice_loading: false, tts: false,
  tts_available: false, cost: null, usage: "", untrusted: false, notes: [], user: "",
};

export interface Notice { id: number; level: string; text: string }

export const hud = $state({
  connected: false,
  orbState: "idle" as OrbState,
  status: { ...NO_STATUS } as Status,
  turns: [] as Turn[],
  cards: [] as Card[],
  widgets: {} as Record<string, any>,
  widgetNames: [] as string[],
  confirm: null as Confirm | null,
  notices: [] as Notice[],
  /** Runde, deren Quellen das Quellen-Panel zeigt ("" = die neueste mit Quellen). */
  selected: "",
});

/** Pegel und Blitzer gehen am reaktiven Zustand vorbei direkt an den Orb (30 Werte pro Sekunde). */
export const orbHooks = {
  state: (_: string) => {},
  level: (_: number) => {},
  tool: (_: string) => {},
  error: () => {},
};

/** Letztes Element bzw. letzter Treffer – ohne Array.at/findLast, die ältere WebViews nicht kennen. */
export function last<T>(list: T[], test: (item: T) => boolean = () => true): T | undefined {
  for (let i = list.length - 1; i >= 0; i--) if (test(list[i])) return list[i];
  return undefined;
}

let ws: WebSocket | null = null;
let noticeId = 0;

export function send(msg: Record<string, unknown>): boolean {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
    return true;
  }
  return false;
}

export function notify(text: string, level = "info") {
  const id = ++noticeId;
  hud.notices.push({ id, level, text });
  setTimeout(() => dismissNotice(id), NOTICE_MS);
}

export function dismissNotice(id: number) {
  const i = hud.notices.findIndex((n) => n.id === id);
  if (i >= 0) hud.notices.splice(i, 1);
}

/** Link im Browser öffnen – Jarvis prüft, ob er ihn selbst angezeigt hat. */
export function openUrl(url: string) {
  send({ type: "open_url", url });
}

const turn = (id: string): Turn | undefined => last(hud.turns, (t) => t.id === id);

function apply(msg: any) {
  switch (msg.type) {
    case "state":
      hud.orbState = msg.state;
      orbHooks.state(msg.state);
      break;
    case "level":
      orbHooks.level(Number(msg.value) || 0);
      break;
    case "tool":
      orbHooks.tool(String(msg.name || ""));
      break;
    case "error":
      orbHooks.error();
      if (msg.message) notify(String(msg.message), "error");
      break;
    case "hello":
      hud.turns = msg.turns ?? [];
      hud.cards = msg.cards ?? [];
      hud.widgets = msg.widgets ?? {};
      hud.widgetNames = msg.widget_names ?? [];
      hud.confirm = msg.confirm ?? null;
      hud.status = { ...NO_STATUS, ...(msg.status ?? {}) };
      hud.selected = "";
      break;
    case "status":
      hud.status = { ...NO_STATUS, ...msg };
      break;
    case "turn.start":
      hud.turns.push({
        id: msg.id, query: msg.query, text: "", tools: [], sources: [], done: false,
        error: false, cancelled: false, usage: "", time: Date.now() / 1000,
      });
      if (hud.turns.length > MAX_TURNS) hud.turns.splice(0, hud.turns.length - MAX_TURNS);
      hud.selected = "";
      break;
    case "delta": {
      const t = turn(msg.id);
      if (t) t.text += msg.text;
      break;
    }
    case "tool.start": {
      const t = turn(msg.id);
      if (t) t.tools.push({ call: msg.call, name: msg.name, args: msg.args ?? "", ok: null, summary: "", time: Date.now() / 1000 });
      break;
    }
    case "tool.end": {
      const item = turn(msg.id)?.tools.find((c) => c.call === msg.call && c.ok === null);
      if (item) {
        item.ok = !!msg.ok;
        item.summary = msg.summary ?? "";
      }
      break;
    }
    case "turn.end": {
      const t = turn(msg.id);
      if (t) Object.assign(t, { text: msg.text, sources: msg.sources ?? [], usage: msg.usage ?? "", error: !!msg.error, cancelled: !!msg.cancelled, done: true });
      break;
    }
    case "reset":
      hud.turns = [];
      hud.cards = [];
      hud.selected = "";
      break;
    case "card": {
      const i = hud.cards.findIndex((c) => c.id === msg.card.id);
      if (i >= 0) hud.cards.splice(i, 1);
      hud.cards.push(msg.card);
      break;
    }
    case "card.remove":
      hud.cards = hud.cards.filter((c) => c.id !== msg.id);
      break;
    case "cards.clear":
      hud.cards = [];
      break;
    case "widget":
      if (msg.data == null) delete hud.widgets[msg.name];
      else hud.widgets[msg.name] = msg.data;
      break;
    case "confirm.request":
      hud.confirm = { id: msg.id, prompt: msg.prompt, voice: !!msg.voice, warning: !!msg.warning };
      break;
    case "confirm.resolved":
      if (hud.confirm?.id === msg.id) hud.confirm = null;
      break;
    case "notice":
      notify(String(msg.text || ""), String(msg.level || "info"));
      break;
  }
}

export function connect() {
  let socket: WebSocket;
  try {
    socket = new WebSocket(`ws://127.0.0.1:${PORT}/?token=${encodeURIComponent(TOKEN)}&client=hud`);
  } catch {
    setTimeout(connect, RETRY_MS);
    return;
  }
  ws = socket;
  socket.onopen = () => { hud.connected = true; };
  socket.onmessage = (e) => {
    try {
      apply(JSON.parse(e.data));
    } catch (err) {
      console.warn("Nachricht nicht verarbeitet", err);
    }
  };
  socket.onclose = () => {
    if (ws === socket) ws = null;
    hud.connected = false;
    hud.confirm = null;
    hud.orbState = "idle";
    orbHooks.state("idle");
    setTimeout(connect, RETRY_MS);
  };
}
