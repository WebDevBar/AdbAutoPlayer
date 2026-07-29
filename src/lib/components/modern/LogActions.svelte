<script lang="ts">
  import { t } from "$lib/i18n/i18n";

  interface Props {
    onClear: () => void;
    onExport: () => void;
    onExportWdb: () => void;
  }

  let { onClear, onExport, onExportWdb }: Props = $props();
</script>

<div class="actions">
  <button class="action-btn" onclick={onExport}>{$t("export")}</button>
  <!-- The live view is filtered; this saves what the run actually said. -->
  <button
    class="action-btn"
    onclick={onExportWdb}
    title="Save the full WDB session log">wdb log</button
  >
  <button class="action-btn" onclick={onClear}>{$t("clear")}</button>
</div>

<style>
  .actions {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .action-btn {
    font-size: 11px;
    color: var(--text-3);
    padding: 2px 6px;
    border-radius: 4px;
    transition: background var(--dur-1);
    background: transparent;
    border: none;
    cursor: pointer;
  }

  .action-btn:hover {
    background: var(--bg-hover);
    color: var(--text-1);
  }
</style>
