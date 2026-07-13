<script lang="ts">
	import { onMount } from 'svelte';
	import { assist, relativeTime } from '$lib/assist.svelte';
	import AssistThread from '$lib/AssistThread.svelte';
	import { authState } from '$lib/auth.svelte';
	import { apiFetch } from '$lib/api';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';

	onMount(() => {
		void assist.ensureLoaded();
	});

	function newConversation() {
		assist.newConversation();
	}

	// ---- Governance (org-admin audit of all conversations) ----
	const isOrgAdmin = $derived((authState.user as any)?.role === 'org_admin');
	let governanceMode = $state(false);
	type AuditConvo = { id: string; title: string; owner_email: string; business_unit: string | null; message_count: number; updated_at: string | null };
	let auditConvos = $state<AuditConvo[]>([]);
	let auditLoaded = $state(false);
	let auditActive = $state<AuditConvo | null>(null);
	let auditMessages = $state<any[]>([]);

	async function toggleGovernance() {
		governanceMode = !governanceMode;
		if (governanceMode && !auditLoaded) {
			try {
				const res = await apiFetch('/api/v1/admin/conversations');
				if (res.ok) {
					auditConvos = (await res.json()).conversations || [];
					auditLoaded = true;
				}
			} catch { /* ignore */ }
		}
	}

	async function openAudit(c: AuditConvo) {
		auditActive = c;
		auditMessages = [];
		try {
			const res = await apiFetch(`/api/v1/admin/conversations/${c.id}`);
			if (res.ok) auditMessages = (await res.json()).messages || [];
		} catch { /* ignore */ }
	}

	function md(s: string): string {
		try { return DOMPurify.sanitize(marked.parse(s, { async: false }) as string); } catch { return s; }
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
			{#if isOrgAdmin}
				<button type="button" class="btn btn-secondary new-convo-btn" class:gov-active={governanceMode} style="margin-top: 8px;" onclick={toggleGovernance}>
					<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
					{governanceMode ? 'Exit governance' : 'Governance'}
				</button>
			{/if}
		</div>
		<div class="convo-list">
			{#if governanceMode}
				{#if auditConvos.length === 0}
					<div class="rail-empty">{auditLoaded ? 'No conversations across the organization yet.' : 'Loading…'}</div>
				{:else}
					{#each auditConvos as c (c.id)}
						<div class="convo-item" class:active={auditActive?.id === c.id}>
							<button type="button" class="convo-main" onclick={() => openAudit(c)}>
								<div class="convo-title">{c.title}</div>
								<div class="convo-time">{c.owner_email}{c.business_unit ? ` · ${c.business_unit}` : ''} · {c.message_count} msgs</div>
							</button>
						</div>
					{/each}
				{/if}
			{:else if assist.sorted.length === 0}
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
		{#if governanceMode}
			<div class="audit-pane">
				{#if !auditActive}
					<div class="rail-empty" style="margin: auto;">Select a conversation to review its transcript (read-only).</div>
				{:else}
					<div class="audit-header">
						<div>
							<div class="audit-title">{auditActive.title}</div>
							<div class="convo-time">{auditActive.owner_email} · read-only governance view</div>
						</div>
						<span class="badge badge-purple">audit</span>
					</div>
					<div class="audit-messages">
						{#each auditMessages as m (m.id)}
							<div class="audit-msg" class:user={m.role === 'user'}>
								<div class="audit-role">{m.role === 'user' ? auditActive.owner_email : 'Jaggaer Assist'}</div>
								<div class="chat-markdown">{@html md(m.content)}</div>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{:else}
			<AssistThread />
		{/if}
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

	.gov-active {
		background: var(--accent-primary) !important;
		color: #fff !important;
	}
	.audit-pane {
		display: flex;
		flex-direction: column;
		height: 100%;
		min-height: 0;
	}
	.audit-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 14px 18px;
		border-bottom: 1px solid var(--border-subtle);
		background: #fff;
	}
	.audit-title {
		font-weight: 600;
		font-size: 14px;
	}
	.audit-messages {
		flex: 1;
		overflow-y: auto;
		padding: 18px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.audit-msg {
		background: #fff;
		border-radius: 12px;
		padding: 12px 16px;
		box-shadow: var(--shadow-sm);
		max-width: 760px;
	}
	.audit-msg.user {
		background: rgba(0, 113, 227, 0.06);
		border: 1px solid rgba(0, 113, 227, 0.15);
	}
	.audit-role {
		font-size: 11px;
		font-weight: 600;
		color: var(--text-tertiary);
		margin-bottom: 4px;
	}

</style>
