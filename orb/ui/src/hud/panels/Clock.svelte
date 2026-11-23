<script lang="ts">
  import { onMount } from "svelte";

  let now = $state(new Date());
  onMount(() => {
    const timer = setInterval(() => (now = new Date()), 1000);
    return () => clearInterval(timer);
  });

  const two = (n: number) => String(n).padStart(2, "0");
  const date = $derived(now.toLocaleDateString("de-DE", { weekday: "long", day: "numeric", month: "long" }));
</script>

<div class="clock">
  <div class="time num">{two(now.getHours())}<span class="colon">:</span>{two(now.getMinutes())}<span class="sec">{two(now.getSeconds())}</span></div>
  <div class="date muted">{date}</div>
</div>

<style>
  .time { font-size: 44px; font-weight: 200; line-height: 1; letter-spacing: 0.01em; }
  .colon { color: var(--accent); margin: 0 1px; position: relative; top: -3px; }
  .sec { margin-left: 6px; font: 400 14px var(--mono); color: var(--dim); }
  .date { margin-top: 6px; font-size: 13px; }
</style>
