<script lang="ts">
	// Collapses long text to `lines` and reveals a "Read more" toggle only when it actually
	// overflows. Keeps the dense right-hand analysis panel scannable (deviation rationales,
	// standard-language blocks, clause text) instead of forcing long scrolls.
	let { text = '', lines = 3 }: { text?: string; lines?: number } = $props();

	let expanded = $state(false);
	let overflowing = $state(false);
	let el = $state<HTMLElement | null>(null);

	$effect(() => {
		// Re-measure when the text or clamp changes. Only meaningful while collapsed.
		const _ = text;
		if (el && !expanded) {
			overflowing = el.scrollHeight - el.clientHeight > 2;
		}
	});
</script>

<div class="clamp-wrap">
	<div
		bind:this={el}
		class="clamp-text"
		class:clamped={!expanded}
		style="--clamp-lines: {lines};"
	>{text}</div>
	{#if overflowing || expanded}
		<button type="button" class="clamp-toggle" onclick={() => (expanded = !expanded)}>
			{expanded ? 'Read less' : 'Read more'}
		</button>
	{/if}
</div>

<style>
	.clamp-text {
		white-space: pre-wrap;
		overflow-wrap: break-word;
	}
	.clamp-text.clamped {
		display: -webkit-box;
		-webkit-line-clamp: var(--clamp-lines, 3);
		line-clamp: var(--clamp-lines, 3);
		-webkit-box-orient: vertical;
		overflow: hidden;
	}
	.clamp-toggle {
		margin-top: 4px;
		background: none;
		border: none;
		padding: 0;
		color: var(--ai, var(--brand-primary, #0071e3));
		font-size: 11px;
		font-weight: 600;
		cursor: pointer;
	}
	.clamp-toggle:hover {
		text-decoration: underline;
	}
</style>
