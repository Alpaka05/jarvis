<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon from "./Icon.svelte";
  import { hide, layout, move, swapSide, toggleCollapsed } from "./layout.svelte";

  let { id, title, badge = "", children, actions }: {
    id: string; title: string; badge?: string | number; children: Snippet; actions?: Snippet;
  } = $props();

  const collapsed = $derived(!!layout.collapsed[id]);
</script>

<section class="panel" class:collapsed class:editing={layout.editing} aria-label={title}>
  <header>
    <button class="title" onclick={() => toggleCollapsed(id)} aria-expanded={!collapsed} title={collapsed ? "Aufklappen" : "Einklappen"}>
      <span class="label">{title}</span>
      {#if badge !== ""}<span class="badge num">{badge}</span>{/if}
    </button>
    <span class="tools">
      {#if layout.editing}
        <button class="icon-btn" onclick={() => move(id, -1)} title="Nach oben" aria-label="{title} nach oben"><Icon name="up" size={15} /></button>
        <button class="icon-btn" onclick={() => move(id, 1)} title="Nach unten" aria-label="{title} nach unten"><Icon name="down" size={15} /></button>
        <button class="icon-btn" onclick={() => swapSide(id)} title="In die andere Spalte" aria-label="{title} in die andere Spalte"><Icon name="swap" size={15} /></button>
        <button class="icon-btn" onclick={() => hide(id)} title="Ausblenden" aria-label="{title} ausblenden"><Icon name="close" size={15} /></button>
      {:else if actions && !collapsed}
        {@render actions()}
      {/if}
    </span>
  </header>
  {#if !collapsed}
    <div class="body">{@render children()}</div>
  {/if}
</section>

<style>
  .panel {
    position: relative;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    backdrop-filter: blur(6px);
    min-width: 0;
  }
  /* Eck-Markierung oben links – der einzige Schmuck, damit die Inhalte ruhig bleiben */
  .panel::before {
    content: "";
    position: absolute;
    top: -1px;
    left: -1px;
    width: 14px;
    height: 14px;
    border-top: 1px solid var(--line-strong);
    border-left: 1px solid var(--line-strong);
    border-top-left-radius: var(--radius);
    pointer-events: none;
  }
  .panel.editing { border-style: dashed; border-color: var(--line-strong); }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    min-height: 34px;
    padding: 0 6px 0 12px;
  }
  .title { display: flex; align-items: center; gap: 8px; padding: 10px 0; flex: 1; min-width: 0; text-align: left; }
  .title:hover .label { color: var(--text); }
  .badge {
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: var(--accent);
    font: 600 11px/1.5 var(--mono);
  }
  .tools { display: flex; align-items: center; }
  .tools :global(.icon-btn) { width: 26px; height: 26px; }
  .body { padding: 2px 12px 12px; }
  .collapsed header { opacity: 0.75; }
</style>
