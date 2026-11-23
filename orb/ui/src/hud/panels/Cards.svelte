<script lang="ts">
  // „Anzeige“: Karten, die Jarvis mit dem display-Tool zeigt. Neueste oben.
  import type { Card } from "../../lib/protocol";
  import Icon from "../Icon.svelte";
  import RichText from "../RichText.svelte";
  import { hud, openUrl, send } from "../store.svelte";

  const cards = $derived([...hud.cards].reverse());
  // Bilder kommen als data:-URL von Jarvis; alles andere wird gar nicht erst als Bild eingesetzt
  const image = (card: Card) => (typeof card.image === "string" && card.image.startsWith("data:image/") ? card.image : "");
</script>

{#if cards.length}
  <div class="cards">
    {#each cards as card (card.id)}
      <article class="card">
        <header>
          <h3>{card.title || { text: "Notiz", list: "Liste", table: "Tabelle", image: "Bild" }[card.kind]}</h3>
          <button class="icon-btn" onclick={() => send({ type: "card.dismiss", id: card.id })} title="Entfernen" aria-label="Karte entfernen"><Icon name="close" size={14} /></button>
        </header>
        {#if card.kind === "image" && image(card)}
          <img src={image(card)} alt={card.title} />
        {/if}
        {#if card.kind === "list" && card.items}
          <ul>{#each card.items as item}<li><RichText text={item} sources={card.sources} /></li>{/each}</ul>
        {/if}
        {#if card.kind === "table" && card.rows}
          <div class="tablewrap">
            <table>
              {#if card.columns?.some((c) => c)}
                <thead><tr>{#each card.columns as col}<th>{col}</th>{/each}</tr></thead>
              {/if}
              <tbody>
                {#each card.rows as row}<tr>{#each row as cell}<td>{cell}</td>{/each}</tr>{/each}
              </tbody>
            </table>
          </div>
        {/if}
        {#if card.text}
          <div class="text" class:note={card.kind !== "text"}><RichText text={card.text} sources={card.sources} /></div>
        {/if}
        {#if card.sources?.length}
          <footer>
            {#each card.sources as s, i}
              <button class="chip" onclick={() => openUrl(s.url)} title={s.title}><span class="n num">{i + 1}</span>{s.host}</button>
            {/each}
          </footer>
        {/if}
      </article>
    {/each}
  </div>
{:else}
  <p class="muted small">Hier zeigt Jarvis Listen, Tabellen und Bilder – sag zum Beispiel „Zeig mir die Zugverbindungen als Tabelle“.</p>
{/if}

<style>
  .cards { display: grid; gap: 10px; }
  .card { border: 1px solid var(--line); border-radius: 8px; background: rgba(5, 12, 17, 0.55); min-width: 0; animation: appear 0.35s ease; }
  @keyframes appear { from { opacity: 0; transform: translateY(-6px); } }
  header { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 4px 0 11px; }
  h3 { margin: 0; font-size: 13.5px; font-weight: 650; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  header .icon-btn { width: 24px; height: 24px; flex: none; }
  img { display: block; width: calc(100% - 22px); margin: 8px 11px 0; border-radius: 6px; }
  ul { margin: 6px 11px 0; padding-left: 1.2em; font-size: 13px; }
  li { margin: 0.25em 0; }
  li::marker { color: var(--accent); }
  .tablewrap { margin: 8px 11px 0; overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; font-variant-numeric: tabular-nums; }
  th { text-align: left; padding: 4px 10px 4px 0; font: 600 10px/1.4 var(--mono); letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); border-bottom: 1px solid var(--line-strong); white-space: nowrap; }
  td { padding: 5px 10px 5px 0; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
  tr:last-child td { border-bottom: 0; }
  .text { margin: 7px 11px 0; font-size: 13px; }
  .text.note { color: var(--muted); font-size: 12px; }
  footer { display: flex; flex-wrap: wrap; gap: 5px; margin: 9px 11px 0; }
  footer .chip { font-size: 11px; padding-left: 4px; }
  footer .n { display: inline-grid; place-items: center; min-width: 16px; height: 16px; border-radius: 8px; background: var(--accent); color: var(--bg); font: 700 9px/1 var(--mono); }
  .card > :last-child { margin-bottom: 11px; }
  .small { font-size: 13px; margin: 0; }
</style>
