<script lang="ts">
  // Gespräch: Verlauf, Rückfragen und die Eingabezeile – ersetzt die Terminal-Eingabe.
  import { tick } from "svelte";
  import type { Turn } from "../lib/protocol";
  import Icon from "./Icon.svelte";
  import RichText from "./RichText.svelte";
  import { hud, last, openUrl, send } from "./store.svelte";

  const EXAMPLES = ["Was steht heute an?", "Wie wird das Wetter morgen?", "Was gibt es Neues in der Raumfahrt?", "Zeig mir meine ungelesenen Mails"];

  let draft = $state("");
  let scroller: HTMLDivElement, input: HTMLTextAreaElement;
  let pinned = true; // unten angeheftet: neuer Text schiebt die Ansicht mit

  const speaking = $derived(hud.orbState === "speaking");
  const canStop = $derived(hud.status.busy || speaking);
  const hour = new Date().getHours();
  const greeting = (hour < 5 ? "Gute Nacht" : hour < 11 ? "Guten Morgen" : hour < 18 ? "Guten Tag" : "Guten Abend");

  function submit(text = draft) {
    const value = text.trim();
    if (!value || !hud.connected) return;
    if (send({ type: "input", text: value })) {
      draft = "";
      pinned = true;
      tick().then(grow);
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && canStop) send({ type: "stop" });
  }

  function grow() {
    if (!input) return;
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  }

  function onScroll() {
    pinned = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 60;
  }

  // Bei neuem Inhalt nach unten, solange der Nutzer nicht selbst hochgescrollt hat
  $effect(() => {
    const newest = last(hud.turns);
    void [hud.turns.length, newest?.text.length, newest?.tools.length, newest?.done, hud.confirm?.id];
    if (pinned && scroller) tick().then(() => scroller.scrollTo({ top: scroller.scrollHeight }));
  });

  const answer = (ok: boolean) => hud.confirm && send({ type: "confirm.reply", id: hud.confirm.id, result: ok });
  const cited = (t: Turn) => t.sources.filter((s) => s.cited);
  const toolLabel = (name: string) => (name === "screen" ? "Bildschirm" : name);

  function toolArgs(args: string): string {
    try {
      const parsed = JSON.parse(args);
      return Object.entries(parsed).map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`).join(" · ");
    } catch {
      return args;
    }
  }
</script>

<div class="conversation">
  <div class="log" bind:this={scroller} onscroll={onScroll} aria-live="polite">
    {#if !hud.turns.length}
      <div class="empty">
        <p class="hello">{greeting}{hud.status.user ? `, ${hud.status.user}` : ""}.</p>
        <p class="muted">Schreib mir unten{hud.status.voice_mode ? " oder sag „Hey Jarvis“" : ""}.</p>
        <div class="examples">
          {#each EXAMPLES as example}
            <button class="chip" onclick={() => submit(example)} disabled={!hud.connected}>{example}</button>
          {/each}
        </div>
      </div>
    {/if}
    {#each hud.turns as t (t.id)}
      <article class="turn">
        <div class="you"><span class="label">Du</span><p>{t.query}</p></div>
        {#each t.tools as tool (tool.call)}
          <div class="toolrow" class:failed={tool.ok === false} title={tool.summary}>
            <span class="status">
              {#if tool.ok === null}<span class="spinner"></span>
              {:else}<Icon name={tool.ok ? "check" : "alert"} size={13} />{/if}
            </span>
            <span class="name">{toolLabel(tool.name)}</span>
            <span class="args">{toolArgs(tool.args)}</span>
          </div>
        {/each}
        {#if t.text || !t.done}
          <div class="jarvis" class:error={t.error && !t.cancelled} class:cancelled={t.cancelled}>
            <span class="label">Jarvis</span>
            {#if t.text}
              <RichText text={t.text} sources={t.sources} cursor={!t.done} />
            {:else}
              <p class="thinking"><span></span><span></span><span></span></p>
            {/if}
            {#if t.done && cited(t).length}
              <div class="sources">
                {#each cited(t) as s, i}
                  <button class="chip" onclick={() => openUrl(s.url)} onmouseenter={() => (hud.selected = t.id)} title={s.title}>
                    <span class="n num">{i + 1}</span>{s.host}
                  </button>
                {/each}
                {#if t.sources.length > cited(t).length}
                  <button class="more" onclick={() => (hud.selected = t.id)}>+{t.sources.length - cited(t).length} weitere gefunden</button>
                {/if}
              </div>
            {:else if t.done && t.sources.length}
              <div class="sources"><button class="more" onclick={() => (hud.selected = t.id)}>{t.sources.length} Quellen gefunden</button></div>
            {/if}
          </div>
        {/if}
      </article>
    {/each}
  </div>

  {#if hud.confirm}
    <div class="confirm" role="alertdialog" aria-label="Bestätigung nötig">
      <div class="head">
        <Icon name="shield" size={16} />
        <span class="label">Bestätigung nötig</span>
        {#if hud.confirm.voice}<span class="muted hint">sag „ja“ oder „nein“ – oder klick</span>{/if}
      </div>
      <pre>{hud.confirm.prompt}</pre>
      {#if hud.confirm.warning}
        <p class="warn"><Icon name="alert" size={14} /> In diesem Gespräch stehen Inhalte aus Webseiten oder E-Mails. Sie könnten diese Aktion ausgelöst haben.</p>
      {/if}
      <div class="buttons">
        <button class="no" onclick={() => answer(false)}>Nein</button>
        <button class="yes" onclick={() => answer(true)}>Ja, ausführen</button>
      </div>
    </div>
  {/if}

  <form class="inputbar" onsubmit={(e) => { e.preventDefault(); submit(); }}>
    <textarea
      bind:this={input}
      bind:value={draft}
      oninput={grow}
      onkeydown={onKey}
      rows="1"
      placeholder={hud.connected ? "Frag Jarvis …" : "Warte auf Jarvis …"}
      aria-label="Nachricht an Jarvis"
      disabled={!hud.connected}
    ></textarea>
    {#if canStop}
      <button type="button" class="icon-btn stop" onclick={() => send({ type: "stop" })} title="Stopp (Esc)" aria-label="Stopp"><Icon name="stop" /></button>
    {/if}
    {#if !hud.status.voice_mode}
      <button type="button" class="icon-btn" onclick={() => send({ type: "voice.once" })} disabled={!hud.connected || hud.status.busy} title="Einmal sprechen" aria-label="Einmal sprechen"><Icon name="mic" /></button>
    {/if}
    <button type="submit" class="icon-btn sendbtn" disabled={!hud.connected || !draft.trim()} title="Senden (Enter)" aria-label="Senden"><Icon name="send" /></button>
  </form>
</div>

<style>
  .conversation { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 10px; }
  .log {
    flex: 1; min-height: 0; overflow-y: auto; padding: 14px 6px 4px 2px;
    /* oben weich ausblenden statt Zeilen hart abzuschneiden */
    -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
    mask-image: linear-gradient(to bottom, transparent 0, #000 16px);
  }

  .empty { display: grid; place-content: center; justify-items: center; gap: 6px; height: 100%; text-align: center; }
  .empty p { margin: 0; }
  .hello { font-size: 22px; font-weight: 300; letter-spacing: 0.01em; }
  .examples { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 14px; max-width: 520px; }
  .examples .chip { padding: 5px 12px; font-size: 13px; }

  .turn { display: grid; gap: 8px; margin-bottom: 20px; }
  .turn .label { display: block; margin-bottom: 5px; }
  .you { justify-self: end; max-width: 82%; padding: 9px 13px; border-radius: 12px 12px 3px 12px; background: rgba(255, 255, 255, 0.055); border: 1px solid rgba(255, 255, 255, 0.07); }
  .you .label { color: var(--dim); text-align: right; }
  .you p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  .jarvis { max-width: 92%; padding: 2px 0 2px 13px; border-left: 2px solid var(--line-strong); font-size: 15px; line-height: 1.55; }
  .jarvis .label { color: var(--accent); }
  .jarvis.error { border-left-color: var(--red); }
  .jarvis.error .label { color: var(--red); }
  .jarvis.cancelled { opacity: 0.6; }

  .toolrow { display: flex; align-items: center; gap: 8px; min-width: 0; padding-left: 13px; color: var(--muted); font: 12px/1.5 var(--mono); }
  .toolrow .status { display: inline-grid; place-items: center; width: 14px; color: var(--green); }
  .toolrow.failed .status { color: var(--red); }
  .toolrow .name { color: var(--amber); font-weight: 600; }
  .toolrow .args { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--dim); }
  .spinner { width: 10px; height: 10px; border: 1.5px solid var(--amber); border-right-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .thinking { display: flex; gap: 5px; margin: 6px 0; }
  .thinking span { width: 6px; height: 6px; border-radius: 50%; background: var(--amber); animation: pulse 1.2s ease-in-out infinite; }
  .thinking span:nth-child(2) { animation-delay: 0.15s; }
  .thinking span:nth-child(3) { animation-delay: 0.3s; }
  @keyframes pulse { 0%, 80%, 100% { opacity: 0.25; } 40% { opacity: 1; } }

  .sources { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 9px; }
  .sources .chip { font-size: 12px; padding-left: 4px; }
  .sources .n { display: inline-grid; place-items: center; min-width: 17px; height: 17px; border-radius: 9px; background: var(--accent); color: var(--bg); font: 700 10px/1 var(--mono); }
  .more { color: var(--muted); font-size: 12px; text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 2px; }
  .more:hover { color: var(--text); }

  .confirm { padding: 12px 14px; border: 1px solid rgba(255, 181, 71, 0.55); border-radius: var(--radius); background: rgba(40, 28, 8, 0.72); }
  .confirm .head { display: flex; align-items: center; gap: 8px; color: var(--amber); }
  .confirm .head .label { color: var(--amber); }
  .confirm .hint { font-size: 12px; margin-left: auto; }
  .confirm pre { margin: 9px 0; max-height: 180px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.5 var(--sans); }
  .confirm .warn { display: flex; gap: 6px; align-items: flex-start; margin: 0 0 10px; color: var(--amber); font-size: 12px; }
  .buttons { display: flex; justify-content: flex-end; gap: 8px; }
  .buttons button { padding: 6px 16px; border-radius: 8px; border: 1px solid var(--line-strong); font-weight: 600; }
  .buttons .no:hover { background: rgba(255, 255, 255, 0.08); }
  .buttons .yes { background: var(--amber); border-color: var(--amber); color: #1c1203; }
  .buttons .yes:hover { filter: brightness(1.1); }

  .inputbar { display: flex; align-items: flex-end; gap: 4px; padding: 6px 6px 6px 14px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel); }
  .inputbar:focus-within { border-color: var(--line-strong); }
  textarea { flex: 1; min-width: 0; resize: none; border: 0; outline: 0; background: none; color: var(--text); font: 15px/1.45 var(--sans); padding: 5px 0; max-height: 140px; }
  textarea::placeholder { color: var(--dim); }
  .sendbtn:not(:disabled) { color: var(--accent); }
  .stop { color: var(--red); }
</style>
