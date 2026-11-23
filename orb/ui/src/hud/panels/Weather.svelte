<script lang="ts">
  import Icon from "../Icon.svelte";
  let { data }: { data: any } = $props();

  const round = (v: unknown) => (typeof v === "number" ? Math.round(v) : "–");
  const icon = (name: string, day = true) => (name === "clear" && !day ? "night" : name);
</script>

{#if data?.error}
  <p class="muted small">Wetter nicht abrufbar: {data.error}</p>
{:else if data?.current}
  <div class="now">
    <span class="sym"><Icon name={icon(data.current.icon, data.current.is_day)} size={38} stroke={1.3} /></span>
    <span class="temp num">{round(data.current.temp)}<span class="unit">°C</span></span>
    <span class="where">
      <span class="place">{data.location}</span>
      <span class="muted">{data.current.text}</span>
    </span>
  </div>
  <div class="facts muted num">
    gefühlt {round(data.current.feels)}° · Wind {round(data.current.wind)} km/h · {round(data.current.humidity)} % Feuchte
  </div>
  <div class="days">
    {#each data.days as day, i}
      <div class="day" title="{day.text}{day.rain != null ? ` · ${day.rain} % Regen` : ''}">
        <span class="label">{i === 0 ? "Heute" : day.weekday}</span>
        <Icon name={icon(day.icon)} size={20} stroke={1.4} />
        <span class="range num">{round(day.max)}°<span class="dim low">{round(day.min)}°</span></span>
      </div>
    {/each}
  </div>
{:else}
  <p class="muted small">Lade Wetter …</p>
{/if}

<style>
  .now { display: flex; align-items: center; gap: 10px; }
  .sym { color: var(--accent); display: grid; }
  .temp { font-size: 34px; font-weight: 200; line-height: 1; }
  .unit { font-size: 15px; color: var(--muted); margin-left: 2px; }
  .where { display: grid; margin-left: auto; text-align: right; font-size: 13px; min-width: 0; }
  .place { font-weight: 600; }
  .facts { margin-top: 8px; font-size: 12px; }
  .days { display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; gap: 6px; margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--line); }
  .day { display: grid; justify-items: center; gap: 5px; color: var(--muted); }
  .range { color: var(--text); font-size: 13px; white-space: nowrap; }
  .low { margin-left: 5px; }
  .small { font-size: 13px; margin: 0; }
</style>
