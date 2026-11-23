<script lang="ts">
  let { data }: { data: any } = $props();
</script>

{#if data?.error}
  <p class="muted small">Postfach nicht erreichbar: {data.error}</p>
{:else if data?.items?.length}
  <ul>
    {#each data.items as mail}
      <li>
        <span class="from">{mail.from}</span>
        <span class="subject muted">{mail.subject}</span>
      </li>
    {/each}
  </ul>
  {#if data.unread > data.items.length}<p class="dim small more">und {data.unread - data.items.length} weitere</p>{/if}
{:else if data}
  <p class="muted small">Keine ungelesenen Mails.</p>
{:else}
  <p class="muted small">Lade Post …</p>
{/if}

<style>
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  li { display: grid; font-size: 13px; min-width: 0; }
  .from { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .subject { font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .small { font-size: 13px; margin: 0; }
  .more { margin-top: 8px; font-size: 12px; }
</style>
