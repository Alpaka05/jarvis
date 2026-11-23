<script lang="ts">
  let { data }: { data: any } = $props();

  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

  function dayLabel(iso: string): string {
    const date = new Date(iso);
    const days = Math.round((startOfDay(date) - startOfDay(new Date())) / 86_400_000);
    if (days === 0) return "Heute";
    if (days === 1) return "Morgen";
    return date.toLocaleDateString("de-DE", { weekday: "short", day: "numeric", month: "numeric" });
  }
  const time = (iso: string) => new Date(iso).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
</script>

{#if data?.error}
  <p class="muted small">{data.error}</p>
{:else if data?.events?.length}
  <ul>
    {#each data.events as event}
      <li class:today={dayLabel(event.datetime) === "Heute"} title={event.description}>
        <span class="when num"><span class="day">{dayLabel(event.datetime)}</span> {time(event.datetime)}</span>
        <span class="title">{event.title}</span>
      </li>
    {/each}
  </ul>
{:else}
  <p class="muted small">Keine Termine in den nächsten zwei Wochen.</p>
{/if}

<style>
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 7px; }
  li { display: grid; grid-template-columns: 104px 1fr; gap: 8px; align-items: baseline; font-size: 13px; }
  .when { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .today .day { color: var(--accent); font-weight: 600; }
  .title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .small { font-size: 13px; margin: 0; }
</style>
