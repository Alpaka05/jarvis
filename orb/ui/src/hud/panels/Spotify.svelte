<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "../Icon.svelte";
  import { send } from "../store.svelte";
  let { data, art }: { data: any; art: any } = $props();

  // Zwischen zwei Abfragen (alle 5 s) läuft der Balken von selbst weiter
  let tick = $state(Date.now());
  onMount(() => {
    const timer = setInterval(() => (tick = Date.now()), 1000);
    return () => clearInterval(timer);
  });

  const progress = $derived.by(() => {
    if (!data?.duration_ms || data.progress_ms == null) return null;
    const elapsed = data.playing ? Math.max(0, tick - data.at * 1000) : 0;
    const ms = Math.min(data.duration_ms, data.progress_ms + elapsed);
    return { ms, ratio: ms / data.duration_ms };
  });
  const clock = (ms: number) => `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
  const act = (action: string) => send({ type: "action", widget: "spotify", action });
  const image = $derived(typeof art?.image === "string" && art.image.startsWith("data:image/") ? art.image : "");
</script>

{#if data?.error}
  <p class="muted small">Spotify nicht erreichbar: {data.error}</p>
{:else if data?.linked === false}
  <p class="muted small">Noch nicht verknüpft – im Terminal <code>spotify login</code> eingeben.</p>
{:else if data}
  <div class="player">
    <div class="cover">{#if image}<img src={image} alt="" />{:else}<Icon name="play" size={18} />{/if}</div>
    <div class="meta">
      {#if data.track}
        <div class="track" title={data.track}>{data.track}</div>
        <div class="artist muted" title={data.album}>{data.artists}</div>
      {:else}
        <div class="track muted">{data.text ?? "Es läuft nichts."}</div>
      {/if}
      {#if data.device}<div class="device dim">{data.device}</div>{/if}
    </div>
  </div>
  {#if progress}
    <div class="bar"><span style:width="{progress.ratio * 100}%"></span></div>
    <div class="times dim num"><span>{clock(progress.ms)}</span><span>{clock(data.duration_ms)}</span></div>
  {/if}
  {#if data.controls}
    <div class="controls">
      <button class="icon-btn" onclick={() => act("previous")} aria-label="Vorheriger Titel"><Icon name="prev" size={16} /></button>
      <button class="icon-btn main" onclick={() => act(data.playing ? "pause" : "play")} aria-label={data.playing ? "Pause" : "Wiedergabe"}><Icon name={data.playing ? "pause" : "play"} size={18} /></button>
      <button class="icon-btn" onclick={() => act("next")} aria-label="Nächster Titel"><Icon name="next" size={16} /></button>
    </div>
  {/if}
{:else}
  <p class="muted small">Lade …</p>
{/if}

<style>
  .player { display: flex; gap: 10px; align-items: center; }
  .cover { flex: none; width: 52px; height: 52px; border-radius: 6px; overflow: hidden; display: grid; place-items: center; background: rgba(255, 255, 255, 0.06); color: var(--dim); }
  .cover img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .meta { min-width: 0; font-size: 13px; }
  .track { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .artist { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .device { font-size: 11px; margin-top: 2px; }
  .bar { height: 3px; margin-top: 10px; border-radius: 2px; background: rgba(255, 255, 255, 0.1); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); transition: width 1s linear; }
  .times { display: flex; justify-content: space-between; margin-top: 3px; font: 10px var(--mono); }
  .controls { display: flex; justify-content: center; gap: 6px; margin-top: 2px; }
  .controls .main { color: var(--text); background: var(--accent-soft); border-radius: 50%; width: 34px; height: 34px; }
  .small { font-size: 13px; margin: 0; }
  code { font: 0.92em var(--mono); color: var(--text); }
</style>
