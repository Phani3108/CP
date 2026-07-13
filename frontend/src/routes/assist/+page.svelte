<script lang="ts">
	import { assist, relativeTime } from '$lib/assist.svelte';
	import AssistThread from '$lib/AssistThread.svelte';

	function newConversation() {
		assist.newConversation();
	}
</script>

<svelte:head>
	<title>Jaggaer Assist — ContractsPulse</title>
</svelte:head>

<div class="assist-page animate-fadeIn">
	<aside class="convo-rail">
		<div class="rail-top">
			<button type="button" class="btn btn-primary new-convo-btn" onclick={newConversation}>
				<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
				New conversation
			</button>
		</div>
		<div class="convo-list">
			{#if assist.sorted.length === 0}
				<div class="rail-empty">No conversations yet.</div>
			{:else}
				{#each assist.sorted as c (c.id)}
					<div class="convo-item" class:active={assist.activeId === c.id}>
						<button type="button" class="convo-main" onclick={() => assist.setActive(c.id)}>
							<div class="convo-title">{c.title}</div>
							<div class="convo-time">{relativeTime(c.updatedAt)}</div>
						</button>
						<button
							type="button"
							class="convo-delete"
							title="Delete conversation"
							aria-label="Delete conversation"
							onclick={() => assist.deleteConversation(c.id)}
						>
							<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
						</button>
					</div>
				{/each}
			{/if}
		</div>
	</aside>

	<section class="thread-pane">
		<AssistThread />
	</section>
</div>

<style>
	.assist-page {
		display: flex;
		height: calc(100vh - 56px);
		min-height: 0;
	}

	.convo-rail {
		width: 280px;
		flex-shrink: 0;
		border-right: 1px solid #e8e8ed;
		background: #fafafa;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.rail-top {
		padding: 14px;
	}
	.new-convo-btn {
		width: 100%;
	}
	.convo-list {
		flex: 1;
		overflow-y: auto;
		padding: 0 10px 14px;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.rail-empty {
		color: var(--text-tertiary);
		font-size: 13px;
		text-align: center;
		padding: 24px 8px;
	}
	.convo-item {
		display: flex;
		align-items: stretch;
		border-radius: 10px;
		border: 1px solid transparent;
		transition: background 130ms ease, border-color 130ms ease, box-shadow 130ms ease;
	}
	.convo-item:hover {
		background: #f0f0f3;
	}
	.convo-item.active {
		background: #ffffff;
		border-color: #d2d2d7;
		box-shadow: var(--shadow-sm);
	}
	.convo-main {
		flex: 1;
		min-width: 0;
		background: transparent;
		border: none;
		text-align: left;
		padding: 9px 10px;
		cursor: pointer;
	}
	.convo-title {
		font-size: 13px;
		font-weight: 500;
		color: var(--text-primary);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.convo-time {
		font-size: 11px;
		color: var(--text-tertiary);
		margin-top: 2px;
	}
	.convo-delete {
		background: transparent;
		border: none;
		color: var(--text-tertiary);
		padding: 0 10px;
		cursor: pointer;
		opacity: 0;
		transition: opacity 130ms ease, color 130ms ease;
	}
	.convo-item:hover .convo-delete {
		opacity: 1;
	}
	.convo-delete:hover {
		color: var(--color-critical-text);
	}

	.thread-pane {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		min-height: 0;
	}

	@media (max-width: 860px) {
		.convo-rail {
			display: none;
		}
	}
</style>
