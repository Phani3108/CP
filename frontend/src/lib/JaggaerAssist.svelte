<script lang="ts">
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { assist } from '$lib/assist.svelte';
	import AssistThread from '$lib/AssistThread.svelte';

	// Hidden entirely on the full-screen /assist route
	const onAssistPage = $derived($page.url.pathname.startsWith('/assist'));
	const showDock = $derived(!onAssistPage && assist.open && assist.mode === 'docked');
	const showFloater = $derived(!onAssistPage && assist.open && assist.mode === 'floater');
</script>

{#if !onAssistPage}
	{#if !assist.open}
		<button class="assist-fab" type="button" onclick={() => assist.toggle()} aria-label="Open Jaggaer Assist">
			<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a4 4 0 0 1-4 4H7l-4 4V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4z"/></svg>
		</button>
	{/if}

	{#if showFloater || showDock}
		<div class="assist-shell" class:floater={showFloater} class:docked={showDock}>
			<div class="assist-head">
				<div class="head-left">
					<div class="assist-logo">
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l1.2 4.3L18 8l-4.8 1.7L12 14l-1.2-4.3L6 8l4.8-1.7L12 2z"/></svg>
					</div>
					<div>
						<div class="assist-title">Jaggaer Assist</div>
						<div class="assist-sub">AI copilot — verify against contract text</div>
					</div>
				</div>
				<div class="head-controls">
					<button
						type="button"
						class="mode-btn"
						class:active={assist.mode === 'floater'}
						title="Floating window"
						aria-label="Floating window"
						onclick={() => assist.setMode('floater')}
					>
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="7" y="9" width="13" height="11" rx="2"/><path d="M4 15V5a2 2 0 0 1 2-2h10"/></svg>
					</button>
					<button
						type="button"
						class="mode-btn"
						class:active={assist.mode === 'docked'}
						title="Dock to side"
						aria-label="Dock to side"
						onclick={() => assist.setMode('docked')}
					>
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>
					</button>
					<button type="button" class="mode-btn" title="Full screen" aria-label="Full screen" onclick={() => goto('/assist')}>
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M16 3h3a2 2 0 0 1 2 2v3"/><path d="M8 21H5a2 2 0 0 1-2-2v-3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
					</button>
					<div class="head-sep"></div>
					<button type="button" class="mode-btn" title="Close" aria-label="Close assistant" onclick={() => (assist.open = false)}>
						<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
					</button>
				</div>
			</div>
			<div class="assist-body-wrap">
				<AssistThread compact />
			</div>
		</div>
	{/if}
{/if}

<style>
	.assist-fab {
		position: fixed;
		right: 20px;
		bottom: 20px;
		width: 52px;
		height: 52px;
		border-radius: 999px;
		border: none;
		background: var(--brand-gradient);
		color: #fff;
		box-shadow: 0 8px 24px rgba(83, 0, 206, 0.35);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		z-index: 150;
		transition: transform 180ms var(--ease-spring), box-shadow 180ms ease;
	}
	.assist-fab:hover {
		transform: scale(1.06);
		box-shadow: 0 10px 30px rgba(226, 43, 131, 0.4);
	}

	.assist-shell {
		display: flex;
		flex-direction: column;
		background: #f5f5f7;
		overflow: hidden;
		z-index: 160;
	}
	.assist-shell.floater {
		position: fixed;
		right: 20px;
		bottom: 20px;
		width: 400px;
		max-width: calc(100vw - 40px);
		height: 560px;
		max-height: calc(100vh - 100px);
		border-radius: 18px;
		border: 1px solid var(--border-subtle);
		box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
		animation: assistIn 240ms var(--ease-spring-gentle);
	}
	.assist-shell.docked {
		position: fixed;
		top: 56px;
		right: 0;
		bottom: 0;
		width: 400px;
		max-width: 100vw;
		border-left: 1px solid var(--border-subtle);
		box-shadow: -8px 0 30px rgba(0, 0, 0, 0.06);
		animation: dockIn 260ms var(--ease-drawer);
	}
	@keyframes assistIn {
		from { opacity: 0; transform: translateY(16px) scale(0.97); }
		to { opacity: 1; transform: translateY(0) scale(1); }
	}
	@keyframes dockIn {
		from { transform: translateX(60px); opacity: 0.4; }
		to { transform: translateX(0); opacity: 1; }
	}

	.assist-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 12px 14px;
		background: var(--brand-gradient);
		color: #fff;
		flex-shrink: 0;
	}
	.head-left {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.assist-logo {
		width: 28px;
		height: 28px;
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.2);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}
	.assist-title {
		font-weight: 700;
		font-size: 13.5px;
		line-height: 1.2;
	}
	.assist-sub {
		font-size: 10.5px;
		opacity: 0.8;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.head-controls {
		display: flex;
		align-items: center;
		gap: 2px;
		flex-shrink: 0;
	}
	.head-sep {
		width: 1px;
		height: 16px;
		background: rgba(255, 255, 255, 0.3);
		margin: 0 4px;
	}
	.mode-btn {
		width: 26px;
		height: 26px;
		border-radius: 7px;
		border: none;
		background: transparent;
		color: rgba(255, 255, 255, 0.85);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		transition: background 140ms ease, color 140ms ease;
	}
	.mode-btn:hover {
		background: rgba(255, 255, 255, 0.18);
		color: #fff;
	}
	.mode-btn.active {
		background: rgba(255, 255, 255, 0.28);
		color: #fff;
	}

	.assist-body-wrap {
		flex: 1;
		min-height: 0;
		display: flex;
		flex-direction: column;
	}
</style>
