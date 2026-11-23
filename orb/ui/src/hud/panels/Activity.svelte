<script lang="ts">
  // Was Jarvis getan hat: die letzten Tool-Aufrufe, neueste oben.
  import Icon from "../Icon.svelte";
  import { hud } from "../store.svelte";

  const MAX = 14;
  const items = $derived(
    hud.turns.flatMap((t) => t.tools.map((tool) => ({ ...tool, time: tool.time ?? t.time }))).slice(-MAX).reverse(),
  );
  const clock = (t: number) => new Date(t * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
</script>

{#if items.length}
  <ul>
    {#each items as item (item.call + item.time)}
      <li class:failed={item.ok === false} title={item.args}>
        <span class="time dim num">{clock(item.time)}</span>
        <span class="status">
          {#if item.ok === null}<span class="spinner"></span>{:else}<Icon name={item.ok ? "check" : "alert"} size={12} />{/if}
        </span>
        <span class="name">{item.name === "screen" ? "Bildschirm" : item.name}</span>
        <span class="summary muted">{item.summary}</span>
      </li>
    {/each}
  </ul>
{:else}
  <p class="muted small">Noch keine Aktionen in diesem Gespräch.</p>
{/if}

<style>
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 5px; }
  li { display: flex; align-items: baseline; gap: 7px; min-width: 0; font-size: 12px; }
  .time { font: 10px var(--mono); }
  .status { display: inline-grid; place-items: center; width: 12px; color: var(--green); align-self: center; }
  .failed .status { color: var(--red); }
  .name { font: 600 11.5px var(--mono); color: var(--amber); white-space: nowrap; }
  .summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
  .spinner { width: 9px; height: 9px; border: 1.5px solid var(--amber); border-right-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .small { font-size: 13px; margin: 0; }
</style>
