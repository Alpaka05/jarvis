/* Jarvis Orb – das schwebende Overlay.
 *
 * Verbindet sich per WebSocket mit Jarvis (core/orb.py) und zeigt den Orb (engine.js) samt
 * Untertiteln. Ereignisformat siehe README, Abschnitt „Orb-Overlay“. Ohne Verbindung bleibt das
 * Fenster leer und versucht es alle 2 s erneut.
 *
 * Der Orb sitzt immer klein in einer Ecke und erscheint erst mit dem Wake-Word („wake“) – oder
 * sofort, wenn er mitten im Gespräch gestartet wird; kurz nachdem Jarvis wieder in den
 * Ruhezustand geht, blendet er sich aus.
 *
 * Zum Ausprobieren im Browser: index.html?preview (dunkler Hintergrund, Orb immer sichtbar) und
 * optional &port=8765&token=… (Inhalt von data/orb.token).
 */
import { createOrb } from "./engine.js";

(() => {
  const params = new URLSearchParams(location.search);
  const PORT = Number(window.ORB_PORT || params.get("port") || 8765);
  const TOKEN = String(window.ORB_TOKEN || params.get("token") || "");
  const $ = (id) => document.getElementById(id);
  const stageEl = $("stage");
  const subsEl = $("subs"), subUser = $("subUser"), subJarvis = $("subJarvis");
  const CORNERS = ["br", "bl", "tl", "tr"];
  const HIDE_MS = 1500, CHARS_PER_S = 15;
  const ALWAYS_SHOWN = params.has("preview");

  if (params.has("preview")) document.body.style.background = "radial-gradient(ellipse at 50% 45%, #0d1c27, #05090d 70%)";

  let corner = CORNERS.includes(params.get("corner")) ? params.get("corner") : "br";
  try { corner = localStorage.getItem("orb.corner") || corner; } catch (e) { /* ohne Speicher: Standardecke */ }

  let state = "idle", connected = false, fresh = false, shown = ALWAYS_SHOWN;
  let W = 1, H = 1;

  // ── Sichtbarkeit: erst mit dem Wake-Word ─────────────────────────────────
  const visible = () => connected && shown;

  const orb = createOrb(stageEl, $("gl"), $("hud"), {
    // Ruheposition in der gewählten Ecke; `rand` = äußerer Ring plus Abstand zum Bildschirmrand
    anchor: (w, h, rand) => {
      W = w; H = h;
      return { x: corner[1] === "l" ? rand : w - rand, y: corner[0] === "t" ? rand : h - rand };
    },
    visible,
    onFrame: (cx, cy, rD, now) => { placeSubs(cx, cy, rD); updateTyping(now); },
  });

  function updateVisibility() {
    const v = visible();
    stageEl.style.opacity = v ? "1" : "0";
    if (!v) { orb.keepRendering(800); clearSubs(); } // Ausblenden noch zu Ende zeichnen
    else orb.poke();
  }

  // ── Untertitel (neben dem Orb, zur Bildschirmmitte hin) ───────────────────
  let typing = null, hideTimer = 0, shownChars = -1, subsPos = "";
  function placeSubs(cx, cy, rD) {
    const gap = 18, left = cx < W / 2, top = cy < H / 2;
    const x = left ? cx + rD + gap : W - cx + rD + gap;
    const y = top ? cy - rD * .6 : H - cy - rD * .6;
    const pos = `${left}|${top}|${Math.round(x)}|${Math.round(y)}`;
    if (pos === subsPos) return;
    subsPos = pos;
    Object.assign(subsEl.style, {
      left: left ? `${x}px` : "auto", right: left ? "auto" : `${x}px`,
      top: top ? `${y}px` : "auto", bottom: top ? "auto" : `${y}px`,
      textAlign: left ? "left" : "right",
    });
  }
  function setLine(el, who, text, cls) {
    el.textContent = "";
    el.className = el === subUser ? "sub user" : "sub" + (cls ? " " + cls : "");
    if (!text) return;
    const b = document.createElement("b");
    b.textContent = who;
    el.append(b, text);
    if (visible()) subsEl.classList.remove("hidden"); // ohne Orb (z.B. Textmodus) keine Untertitel
  }
  function clearSubs() {
    typing = null;
    setLine(subUser, "", ""); setLine(subJarvis, "", "");
    subsEl.classList.add("hidden");
  }
  // Nur den Satz zeigen, der gerade gesprochen wird, damit lange Antworten nicht den Bildschirm füllen
  function currentSentence(text, n) {
    const shown = text.slice(0, n);
    const re = /[.!?…]\s+/g;
    let start = 0, m;
    while ((m = re.exec(shown)) && m.index + m[0].length < n) start = m.index + m[0].length;
    return shown.slice(start);
  }
  function preview(text) {
    return text.length > 140 ? text.slice(0, 137).trimEnd() + " …" : text;
  }
  function updateTyping(now) {
    if (!typing || !typing.start) return;
    const n = Math.min(typing.text.length, Math.floor((now - typing.start) / 1000 * CHARS_PER_S));
    if (n !== shownChars) { shownChars = n; setLine(subJarvis, "Jarvis", currentSentence(typing.text, n)); }
    if (n >= typing.text.length) typing = null;
  }
  function startTyping() {
    if (typing && !typing.start) { typing.start = performance.now(); shownChars = -1; }
  }

  // ── Ereignisse von Jarvis ────────────────────────────────────────────────
  function setState(name) {
    if (!orb.setState(name)) return;
    state = name;
    clearTimeout(hideTimer);
    if (name === "wake") { clearSubs(); if (!shown) { shown = true; updateVisibility(); } }
    if (name === "speaking") startTyping();
    else if (typing && !typing.start) { setLine(subJarvis, "Jarvis", preview(typing.text)); typing = null; } // keine Sprachausgabe
    else typing = null;
    if (name === "idle") hideTimer = setTimeout(() => {
      if (ALWAYS_SHOWN) clearSubs(); else { shown = false; updateVisibility(); }
    }, HIDE_MS);
  }

  function onMessage(msg) {
    switch (msg.type) {
      case "state":
        // Mitten im Gespräch gestartet (z.B. „Schalte den Orb ein“): gleich zeigen statt erst beim nächsten Wake-Word
        if (fresh && msg.state !== "idle" && !shown) { shown = true; updateVisibility(); }
        fresh = false;
        setState(msg.state);
        break;
      case "level":
        orb.setLevel(msg.value);
        break;
      case "tool":
        orb.tool(msg.name);
        break;
      case "error":
        orb.error();
        if (msg.message) setLine(subJarvis, "Jarvis", String(msg.message), "error");
        break;
      case "transcript":
        if (msg.role === "user") { setLine(subUser, "Du", String(msg.text || "")); setLine(subJarvis, "", ""); }
        else { typing = { text: String(msg.text || ""), start: 0 }; if (state === "speaking") startTyping(); }
        break;
    }
  }

  function connect() {
    let ws;
    try { ws = new WebSocket(`ws://127.0.0.1:${PORT}/?token=${encodeURIComponent(TOKEN)}`); } catch (e) { setTimeout(connect, 2000); return; }
    ws.onopen = () => { connected = true; fresh = true; updateVisibility(); };
    ws.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch (err) { /* kaputte Nachricht ignorieren */ } };
    ws.onclose = () => {
      if (connected) { connected = false; shown = ALWAYS_SHOWN; state = "idle"; updateVisibility(); }
      setTimeout(connect, 2000);
    };
  }
  connect();

  // ── Tray-Menü: Ecke für den Ruhezustand wechseln ─────────────────────────
  const tauriEvent = window.__TAURI__ && window.__TAURI__.event;
  if (tauriEvent) {
    tauriEvent.listen("cycle-corner", () => {
      corner = CORNERS[(CORNERS.indexOf(corner) + 1) % CORNERS.length];
      try { localStorage.setItem("orb.corner", corner); } catch (e) { /* nicht kritisch */ }
      orb.poke();
    });
  }
})();
