<script lang="ts">
  // Jarvis-Dashboard: Orb und Gespräch in der Mitte, links die Umgebung (Zeit, Wetter, Termine,
  // Smart Home, Musik, Post), rechts was Jarvis zeigt (Anzeige, Quellen, Aktivität).
  import Conversation from "./Conversation.svelte";
  import Icon from "./Icon.svelte";
  import { layout, PANELS, resetLayout, show, WIDGET_PANELS, type Side } from "./layout.svelte";
  import OrbStage from "./OrbStage.svelte";
  import Panel from "./Panel.svelte";
  import Activity from "./panels/Activity.svelte";
  import Calendar from "./panels/Calendar.svelte";
  import Cards from "./panels/Cards.svelte";
  import Clock from "./panels/Clock.svelte";
  import Mail from "./panels/Mail.svelte";
  import SmartHome from "./panels/SmartHome.svelte";
  import Sources from "./panels/Sources.svelte";
  import Spotify from "./panels/Spotify.svelte";
  import Weather from "./panels/Weather.svelte";
  import { dismissNotice, hud, send } from "./store.svelte";

  // Anzeigen ohne eingerichtetes Widget bleiben weg (kein leerer Kasten für „kein Postfach“)
  const available = (id: string) => !WIDGET_PANELS.includes(id) || hud.widgetNames.includes(id) || id in hud.widgets;
  const column = (side: Side) => layout[side].filter(available);
  const hidden = $derived(layout.hidden.filter(available));

  const cost = $derived(hud.status.cost == null ? "" : `≈ ${hud.status.cost.toFixed(hud.status.cost < 0.1 ? 3 : 2)} $`);
  const unread = $derived(hud.widgets.mail?.unread ? hud.widgets.mail.unread : "");

  function reset() {
    if (!hud.turns.length || confirm("Gespräch und Karten leeren?")) send({ type: "reset" });
  }
</script>

{#snippet panel(id: string)}
  {#if id === "clock"}
    <Panel {id} title={PANELS[id]}><Clock /></Panel>
  {:else if id === "weather"}
    <Panel {id} title={PANELS[id]}><Weather data={hud.widgets.weather} /></Panel>
  {:else if id === "calendar"}
    <Panel {id} title={PANELS[id]}><Calendar data={hud.widgets.calendar} /></Panel>
  {:else if id === "homeassistant"}
    <Panel {id} title={PANELS[id]}><SmartHome data={hud.widgets.homeassistant} /></Panel>
  {:else if id === "spotify"}
    <Panel {id} title={PANELS[id]}><Spotify data={hud.widgets.spotify} art={hud.widgets["spotify.art"]} /></Panel>
  {:else if id === "mail"}
    <Panel {id} title={PANELS[id]} badge={unread}><Mail data={hud.widgets.mail} /></Panel>
  {:else if id === "cards"}
    <Panel {id} title={PANELS[id]} badge={hud.cards.length > 1 ? hud.cards.length : ""}>
      {#snippet actions()}
        {#if hud.cards.length > 1}
          <button class="icon-btn" onclick={() => send({ type: "cards.clear" })} title="Alle Karten entfernen" aria-label="Alle Karten entfernen"><Icon name="close" size={15} /></button>
        {/if}
      {/snippet}
      <Cards />
    </Panel>
  {:else if id === "sources"}
    <Panel {id} title={PANELS[id]}><Sources /></Panel>
  {:else if id === "activity"}
    <Panel {id} title={PANELS[id]}><Activity /></Panel>
  {/if}
{/snippet}

<div class="app">
  <header class="top">
    <div class="brand">
      <span class="mark">JARVIS</span>
      <span class="conn" class:on={hud.connected} title={hud.connected ? "Verbunden" : "Keine Verbindung zu Jarvis"}></span>
      <span class="provider" title={hud.status.fallback ? `Ersatz: ${hud.status.fallback}` : ""}>
        {hud.connected ? hud.status.provider || "kein KI-Dienst eingerichtet" : "getrennt"}
      </span>
    </div>
    <div class="actions">
      {#if hud.status.untrusted}
        <span class="chip caution" title="Im Gespräch stehen Inhalte aus Webseiten oder E-Mails. Bis zum Leeren des Gesprächs fragt Jarvis vor heiklen Aktionen nach.">
          <Icon name="shield" size={14} /> Erhöhte Vorsicht
        </span>
      {/if}
      {#if cost}<span class="cost num" title={hud.status.usage}>{cost}</span>{/if}
      <button
        class="toggle" class:on={hud.status.voice_mode} disabled={!hud.connected || hud.status.voice_loading}
        onclick={() => send({ type: "voice.toggle" })} aria-pressed={hud.status.voice_mode}
        title={hud.status.voice_mode ? "Sprachmodus beenden" : "Sprachmodus starten: auf „Hey Jarvis“ lauschen"}
      >
        <Icon name="ear" size={16} />
        {hud.status.voice_loading ? "Lade …" : hud.status.voice_mode ? "Hört auf „Hey Jarvis“" : "Sprachmodus"}
      </button>
      <button
        class="icon-btn" class:on={hud.status.tts} disabled={!hud.connected || !hud.status.tts_available}
        onclick={() => send({ type: "tts", enabled: !hud.status.tts })} aria-pressed={hud.status.tts}
        title={hud.status.tts ? "Sprachausgabe ausschalten" : "Sprachausgabe einschalten"} aria-label="Sprachausgabe"
      ><Icon name={hud.status.tts ? "speaker" : "mute"} /></button>
      <button class="icon-btn" onclick={reset} disabled={!hud.connected} title="Neues Gespräch" aria-label="Neues Gespräch"><Icon name="reset" /></button>
      <button class="icon-btn" class:on={layout.editing} onclick={() => (layout.editing = !layout.editing)} aria-pressed={layout.editing} title="Anordnung bearbeiten" aria-label="Anordnung bearbeiten"><Icon name="layout" /></button>
    </div>
  </header>

  {#if layout.editing}
    <div class="editbar">
      <span>Panels mit den Pfeilen verschieben, in die andere Spalte setzen oder ausblenden.</span>
      {#each hidden as id}
        <button class="chip" onclick={() => show(id)}><Icon name="eye" size={13} /> {PANELS[id]}</button>
      {/each}
      <span class="spacer"></span>
      <button class="text" onclick={resetLayout}>Zurücksetzen</button>
      <button class="text done" onclick={() => (layout.editing = false)}>Fertig</button>
    </div>
  {/if}

  <main class="grid">
    <aside class="col left" aria-label="Umgebung">
      {#each column("left") as id (id)}{@render panel(id)}{/each}
    </aside>
    <section class="center">
      <div class="orb"><OrbStage /></div>
      <div class="talk"><Conversation /></div>
    </section>
    <aside class="col right" aria-label="Von Jarvis">
      {#each column("right") as id (id)}{@render panel(id)}{/each}
    </aside>
  </main>

  {#if hud.notices.length}
    <div class="notices" role="status">
      {#each hud.notices as n (n.id)}
        <button class="notice {n.level}" onclick={() => dismissNotice(n.id)} title="Schließen">{n.text}</button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .app { display: flex; flex-direction: column; height: 100%; min-height: 0; }

  .top { display: flex; align-items: center; justify-content: space-between; gap: 16px; height: 46px; padding: 0 14px 0 18px; border-bottom: 1px solid var(--line); flex: none; }
  .brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
  .mark { font: 600 13px var(--mono); letter-spacing: 0.42em; color: var(--accent); }
  .conn { width: 7px; height: 7px; border-radius: 50%; background: var(--red); flex: none; }
  .conn.on { background: var(--green); box-shadow: 0 0 8px var(--green); }
  .provider { font: 11px var(--mono); color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .actions { display: flex; align-items: center; gap: 6px; flex: none; }
  .cost { font: 11px var(--mono); color: var(--muted); margin-right: 4px; }
  .caution { color: var(--amber); border-color: rgba(255, 181, 71, 0.4); background: rgba(255, 181, 71, 0.08); }
  .toggle { display: inline-flex; align-items: center; gap: 7px; height: 30px; padding: 0 11px; border: 1px solid var(--line); border-radius: 8px; color: var(--muted); font-size: 12.5px; white-space: nowrap; }
  .toggle:hover:not(:disabled) { color: var(--text); border-color: var(--line-strong); }
  .toggle.on { color: var(--accent); border-color: var(--line-strong); background: var(--accent-soft); }

  .editbar { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; padding: 7px 18px; border-bottom: 1px dashed var(--line-strong); color: var(--muted); font-size: 12.5px; flex: none; }
  .editbar .spacer { flex: 1; }
  .editbar .text { color: var(--muted); padding: 3px 8px; border-radius: 6px; }
  .editbar .text:hover { color: var(--text); background: var(--accent-soft); }
  .editbar .done { color: var(--accent); font-weight: 600; }

  .grid {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: minmax(240px, 300px) minmax(0, 1fr) minmax(280px, 370px);
    grid-template-areas: "left center right";
    gap: 14px;
    padding: 14px;
  }
  .col { display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow-y: auto; padding-right: 2px; }
  .left { grid-area: left; }
  .right { grid-area: right; }
  .center { grid-area: center; display: flex; flex-direction: column; min-width: 0; min-height: 0; max-width: 860px; width: 100%; margin: 0 auto; }
  .orb { flex: 0 0 clamp(200px, 38vh, 400px); min-height: 0; }
  .talk { flex: 1; min-height: 0; }

  .notices { position: fixed; right: 16px; bottom: 16px; display: grid; gap: 8px; max-width: 380px; z-index: 5; }
  .notice { padding: 9px 13px; border-radius: 9px; border: 1px solid var(--line-strong); background: var(--panel-solid); text-align: left; font-size: 13px; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.45); }
  .notice.warning { border-color: rgba(255, 181, 71, 0.6); }
  .notice.error { border-color: rgba(255, 107, 122, 0.7); color: #ffd7dc; }

  /* Schmaler: beide Seitenspalten untereinander rechts neben der Mitte */
  @media (max-width: 1180px) {
    .grid { grid-template-columns: minmax(0, 1fr) minmax(260px, 320px); grid-template-rows: minmax(0, 1fr) minmax(0, 1fr); grid-template-areas: "center right" "center left"; }
    .provider { display: none; }
  }
  /* Sehr schmal: alles untereinander, die Seite rollt */
  @media (max-width: 760px) {
    :global(body) { overflow: auto; }
    .app { height: auto; min-height: 100%; }
    .grid { display: flex; flex-direction: column; }
    .center { order: -1; min-height: 78vh; }
    .col { overflow: visible; }
    .toggle { font-size: 0; gap: 0; padding: 0 8px; }
  }
</style>
