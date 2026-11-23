<script lang="ts">
  // Quellen der gewählten Antwort (Standard: die neueste, die welche hat).
  import { hud, last, openUrl } from "../store.svelte";

  const turn = $derived(
    hud.turns.find((t) => t.id === hud.selected && t.sources.length) ?? last(hud.turns, (t) => t.done && t.sources.length > 0),
  );
  const cited = $derived(turn?.sources.filter((s) => s.cited) ?? []);
  const found = $derived(turn?.sources.filter((s) => !s.cited) ?? []);
</script>

{#if turn}
  <p class="for dim" title={turn.query}>zu: {turn.query}</p>
  <ol>
    {#each cited as s, i}
      <li>
        <button onclick={() => openUrl(s.url)} title={s.url}>
          <span class="n num">{i + 1}</span>
          <span class="body">
            <span class="title">{s.title}</span>
            <span class="host">{s.host}</span>
            {#if s.snippet}<span class="snippet muted">{s.snippet}</span>{/if}
          </span>
        </button>
      </li>
    {/each}
  </ol>
  {#if found.length}
    <p class="label also">{cited.length ? "Außerdem gefunden" : "Gefunden, nicht zitiert"}</p>
    <ul>
      {#each found as s}
        <li><button class="plain" onclick={() => openUrl(s.url)} title={s.url}><span class="title">{s.title}</span><span class="host">{s.host}</span></button></li>
      {/each}
    </ul>
  {/if}
{:else}
  <p class="muted small">Stützt sich Jarvis auf Webseiten, stehen sie hier – mit Nummer wie in der Antwort.</p>
{/if}

<style>
  .for { margin: 0 0 8px; font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  ol, ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
  button { display: flex; gap: 9px; width: 100%; padding: 6px; margin: 0 -6px; border-radius: 7px; text-align: left; }
  button:hover { background: var(--accent-soft); }
  .n { flex: none; display: inline-grid; place-items: center; min-width: 19px; height: 19px; border-radius: 10px; background: var(--accent); color: var(--bg); font: 700 10px/1 var(--mono); margin-top: 1px; }
  .body { display: grid; min-width: 0; }
  .title { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .host { font: 11px var(--mono); color: var(--accent); }
  .snippet { margin-top: 3px; font-size: 12px; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .also { margin: 12px 0 6px; }
  .plain { display: grid; gap: 0; padding: 4px 6px; }
  .plain .title { font-weight: 400; }
  .small { font-size: 13px; margin: 0; }
</style>
