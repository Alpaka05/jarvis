<script lang="ts">
  // Text mit einfacher Formatierung, Links und Beleg-Marken. Alles wird als Text eingesetzt (kein HTML).
  import type { Source } from "../lib/protocol";
  import { blocks, hostOf, type Inline } from "./richtext";
  import { openUrl } from "./store.svelte";

  let { text, sources = [], cursor = false }: { text: string; sources?: Source[]; cursor?: boolean } = $props();

  const parsed = $derived(blocks(text));
  // Belege werden je Antwort ab 1 gezählt (in der Reihenfolge der Quellenliste darunter)
  const local = (n: number) => sources.findIndex((s) => s.n === n) + 1;
  const source = (n: number) => sources.find((s) => s.n === n);
</script>

{#snippet run(parts: Inline[])}
  {#each parts as part}
    {#if part.t === "text"}{part.v}
    {:else if part.t === "b"}<strong>{part.v}</strong>
    {:else if part.t === "i"}<em>{part.v}</em>
    {:else if part.t === "code"}<code>{part.v}</code>
    {:else if part.t === "url"}<button class="url" onclick={() => openUrl(part.v)} title={part.v}>{hostOf(part.v)}<span class="path">{part.v.replace(/^https?:\/\/[^/]+/, "").slice(0, 40)}</span></button>
    {:else if part.t === "cite"}{#each part.ns as n}{@const s = source(n)}{#if s}<button class="cite num" onclick={() => openUrl(s.url)} title="{s.title} – {s.host}">{local(n)}</button>{/if}{/each}
    {/if}
  {/each}
{/snippet}

<div class="rich">
  {#each parsed as block, i}
    {#if block.t === "p"}
      <p>{@render run(block.inline)}{#if cursor && i === parsed.length - 1}<span class="cursor"></span>{/if}</p>
    {:else if block.ordered}
      <ol>{#each block.items as item}<li>{@render run(item)}</li>{/each}</ol>
    {:else}
      <ul>{#each block.items as item}<li>{@render run(item)}</li>{/each}</ul>
    {/if}
  {/each}
  {#if cursor && !parsed.length}<p><span class="cursor"></span></p>{/if}
</div>

<style>
  .rich { overflow-wrap: anywhere; }
  p { margin: 0 0 0.55em; white-space: pre-wrap; }
  p:last-child, ul:last-child, ol:last-child { margin-bottom: 0; }
  ul, ol { margin: 0 0 0.55em; padding-left: 1.25em; }
  li { margin: 0.15em 0; }
  li::marker { color: var(--accent); }
  strong { font-weight: 650; color: #fff; }
  code { font: 0.92em var(--mono); padding: 1px 5px; border-radius: 4px; background: rgba(255, 255, 255, 0.07); }
  .url { color: var(--accent); text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 2px; }
  .url .path { color: var(--muted); }
  .cite {
    display: inline-grid;
    place-items: center;
    min-width: 17px;
    height: 17px;
    margin: 0 1px 0 3px;
    padding: 0 4px;
    border-radius: 9px;
    background: var(--accent-soft);
    border: 1px solid var(--line-strong);
    color: var(--accent);
    font: 600 10px/1 var(--mono);
    vertical-align: 1px;
  }
  .cite:hover { background: var(--accent); color: var(--bg); }
  .cursor {
    display: inline-block;
    width: 7px;
    height: 1em;
    margin-left: 2px;
    vertical-align: -2px;
    background: var(--accent);
    animation: blink 1s steps(2) infinite;
  }
  @keyframes blink { 50% { opacity: 0; } }
</style>
