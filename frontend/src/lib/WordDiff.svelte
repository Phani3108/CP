<script lang="ts">
	// Renders a word_diff op list (from the backend) as Word-style tracked changes.
	// Each op's text is rendered as escaped Svelte text — never {@html} — because it is
	// untrusted contract content.
	let { ops = [] }: { ops?: Array<{ op: string; text: string }> } = $props();
</script>

<span class="word-diff">
	{#each ops as o}
		{#if o.op === 'insert'}<ins>{o.text}</ins>{:else if o.op === 'delete'}<del>{o.text}</del>{:else}<span>{o.text}</span>{/if}
	{/each}
</span>

<style>
	.word-diff {
		white-space: pre-wrap;
		overflow-wrap: break-word;
		font-size: 12px;
		line-height: 1.65;
	}
	.word-diff ins {
		background: color-mix(in srgb, var(--color-low, #22c55e) 20%, transparent);
		color: var(--color-low-text, #15803d);
		text-decoration: none;
		border-radius: 2px;
		padding: 0 1px;
	}
	.word-diff del {
		background: color-mix(in srgb, var(--color-high, #ef4444) 16%, transparent);
		color: var(--color-high-text, #b91c1c);
		text-decoration: line-through;
		border-radius: 2px;
		padding: 0 1px;
	}
</style>
