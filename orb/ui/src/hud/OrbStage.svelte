<script lang="ts">
  // Der Orb in der Mitte: dieselbe Kugel wie im Overlay, nur groß und immer sichtbar.
  import { onMount } from "svelte";
  import { createOrb, STATES } from "../orb/engine.js";
  import { hud, last, orbHooks } from "./store.svelte";

  let stage: HTMLDivElement, gl: HTMLCanvasElement, rings: HTMLCanvasElement;

  const running = $derived(last(last(hud.turns)?.tools ?? [], (t) => t.ok === null)?.name ?? "");
  const label = $derived.by(() => {
    if (!hud.connected) return "Getrennt";
    if (hud.confirm) return "Wartet auf dich";
    if (hud.orbState !== "idle") return (STATES as any)[hud.orbState]?.label ?? "";
    if (hud.status.busy) return "Denke nach";
    return hud.status.voice_mode ? "Bereit · „Hey Jarvis“" : "Bereit";
  });

  onMount(() => {
    // Größe so, dass Ringe und Funken ganz in die Bühne passen
    const orb = createOrb(stage, gl, rings, { radius: (w: number, h: number) => Math.min(w, h) * 0.37, toolCallout: false, stateLabel: false });
    orb.setState(hud.orbState);
    Object.assign(orbHooks, { state: orb.setState, level: orb.setLevel, tool: orb.tool, error: orb.error });
    return () => {
      Object.assign(orbHooks, { state: () => {}, level: () => {}, tool: () => {}, error: () => {} });
      orb.destroy();
    };
  });
</script>

<div class="wrap">
  <div class="stage" bind:this={stage} aria-hidden="true">
    <canvas bind:this={gl}></canvas>
    <canvas bind:this={rings}></canvas>
  </div>
  <div class="caption" role="status">
    <span class="label state" class:busy={hud.orbState === "thinking" || hud.status.busy} class:off={!hud.connected}>{label}</span>
    {#if running}<span class="chip tool">{running === "screen" ? "Bildschirm" : running}</span>{/if}
  </div>
</div>

<style>
  .wrap { position: relative; height: 100%; min-height: 0; }
  /* etwas über das Feld hinaus, damit äußere Funken nicht an einer Kante enden */
  .stage { position: absolute; inset: -22px -30px 4px; pointer-events: none; }
  .stage canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .caption {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    height: 24px;
  }
  .state { color: var(--accent); font-size: 11px; }
  .state.busy { color: var(--amber); }
  .state.off { color: var(--dim); }
  .tool { font: 600 11px/1.6 var(--mono); border-color: rgba(255, 181, 71, 0.4); background: rgba(255, 181, 71, 0.1); color: var(--amber); }
</style>
