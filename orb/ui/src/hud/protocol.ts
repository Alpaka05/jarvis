// Nachrichten zwischen Jarvis (core/hud.py) und dem Dashboard – siehe dort für das ganze Protokoll.

export interface Source {
  n: number;
  url: string;
  title: string;
  host: string;
  snippet: string;
  cited?: boolean;
}

export interface ToolItem {
  call: string;
  name: string;
  args: string;
  ok: boolean | null; // null = läuft noch
  summary: string;
  time?: number;
}

export interface Turn {
  id: string;
  query: string;
  text: string;
  tools: ToolItem[];
  sources: Source[];
  done: boolean;
  error: boolean;
  cancelled: boolean;
  usage: string;
  time: number;
}

export interface Card {
  id: string;
  kind: "text" | "list" | "table" | "image";
  title: string;
  text: string;
  items?: string[];
  columns?: string[];
  rows?: string[][];
  image?: string;
  sources: Source[];
  created: number;
}

export interface Confirm {
  id: string;
  prompt: string;
  voice: boolean;
  warning: boolean;
}

export interface Status {
  provider: string;
  fallback: string;
  busy: boolean;
  voice_mode: boolean;
  voice_loading: boolean;
  tts: boolean;
  tts_available: boolean;
  cost: number | null;
  usage: string;
  untrusted: boolean;
  notes: string[];
  user: string;
}

export type OrbState = "idle" | "wake" | "listening" | "thinking" | "speaking";
