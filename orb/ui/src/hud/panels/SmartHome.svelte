<script lang="ts">
  import Icon from "../Icon.svelte";
  import { send } from "../store.svelte";
  let { data }: { data: any } = $props();

  const ICONS: Record<string, string> = { light: "bulb", climate: "thermo", switch: "plug", fan: "plug", input_boolean: "plug", sensor: "dot" };
  const STATES: Record<string, string> = {
    on: "an", off: "aus", unavailable: "nicht erreichbar", unknown: "unbekannt", heat: "heizt", cool: "kühlt",
    auto: "automatisch", idle: "ruht", playing: "spielt", paused: "pausiert", home: "zu Hause", not_home: "unterwegs",
    open: "offen", closed: "zu", locked: "verriegelt", unlocked: "entriegelt",
  };

  function value(e: any): string {
    if (e.domain === "climate" && e.temperature != null) return `${e.temperature}°` + (e.target != null ? ` → ${e.target}°` : "");
    if (e.state === "on" && e.brightness != null) return `${e.brightness} %`;
    const text = STATES[e.state] ?? e.state;
    return e.unit ? `${text} ${e.unit}` : text;
  }
  const toggle = (id: string) => send({ type: "action", widget: "homeassistant", action: "toggle", entity_id: id });
</script>

{#if data?.error}
  <p class="muted small">Home Assistant nicht erreichbar: {data.error}</p>
{:else if data?.entities?.length}
  <ul>
    {#each data.entities as e (e.id)}
      <li class:on={e.state === "on" || e.state === "heat"} class:gone={e.state === "unavailable"}>
        <span class="sym"><Icon name={ICONS[e.domain] ?? "dot"} size={16} /></span>
        <span class="name" title={e.id}>{e.name}</span>
        <span class="value num">{value(e)}</span>
        {#if e.toggle}
          <button class="switch" class:active={e.state === "on"} role="switch" aria-checked={e.state === "on"} aria-label="{e.name} umschalten" onclick={() => toggle(e.id)} disabled={e.state === "unavailable"}><span></span></button>
        {/if}
      </li>
    {/each}
  </ul>
{:else if data}
  <p class="muted small">Keine Geräte gefunden. In der .env mit HUD_HA_ENTITIES auswählen.</p>
{:else}
  <p class="muted small">Lade Geräte …</p>
{/if}

<style>
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
  li { display: flex; align-items: center; gap: 8px; min-height: 26px; font-size: 13px; }
  .sym { display: grid; color: var(--dim); }
  .on .sym { color: var(--amber); }
  .gone { opacity: 0.5; }
  .name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .value { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .on .value { color: var(--text); }
  .switch { position: relative; flex: none; width: 30px; height: 17px; border-radius: 9px; background: rgba(255, 255, 255, 0.12); transition: background 0.15s; }
  .switch span { position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; border-radius: 50%; background: var(--muted); transition: transform 0.15s, background 0.15s; }
  .switch.active { background: rgba(255, 181, 71, 0.35); }
  .switch.active span { transform: translateX(13px); background: var(--amber); }
  .small { font-size: 13px; margin: 0; }
</style>
