<script lang="ts">
	import { goto } from '$app/navigation';
	import { marked } from 'marked';
	import DOMPurify from 'dompurify';
	import { assist, STARTER_QUESTIONS, STARTER_QUESTIONS_CONTRACT, type AssistAction, type AssistMsg, type AssistSource } from '$lib/assist.svelte';
	import { toast } from '$lib/toastStore';

	let { compact = false }: { compact?: boolean } = $props();

	let input = $state('');
	let copiedIdx = $state(-1);
	let bodyEl: HTMLDivElement | undefined = $state();
	let textareaEl: HTMLTextAreaElement | undefined = $state();

	const messages = $derived(assist.active?.messages ?? []);
	const starters = $derived(assist.pageContext ? STARTER_QUESTIONS_CONTRACT : STARTER_QUESTIONS);
	const contextName = $derived.by(() => {
		const ctx = assist.pageContext;
		if (!ctx) return null;
		const name = ctx.contract_name || 'this contract';
		return name.length > 34 ? name.slice(0, 34) + '…' : name;
	});
	const lastSuggested = $derived.by(() => {
		const last = messages[messages.length - 1];
		return last?.role === 'assistant' && !last.error ? (last.suggested ?? []) : [];
	});

	function renderMarkdown(src: string): string {
		try {
			return DOMPurify.sanitize(marked.parse(src, { async: false }) as string);
		} catch {
			return DOMPurify.sanitize(src);
		}
	}

	async function submit(text?: string) {
		const q = (text ?? input).trim();
		if (!q) return;
		input = '';
		if (textareaEl) textareaEl.style.height = 'auto';
		await assist.send(q);
		queueMicrotask(scrollDown);
	}

	function scrollDown() {
		if (bodyEl) bodyEl.scrollTop = bodyEl.scrollHeight;
	}

	$effect(() => {
		// autoscroll when messages change or loading toggles
		void messages.length;
		void assist.loading;
		queueMicrotask(scrollDown);
	});

	function autoGrow() {
		if (!textareaEl) return;
		textareaEl.style.height = 'auto';
		textareaEl.style.height = Math.min(textareaEl.scrollHeight, 120) + 'px';
	}

	async function copyMessage(m: AssistMsg, idx: number) {
		try {
			await navigator.clipboard.writeText(m.content);
			copiedIdx = idx;
			setTimeout(() => (copiedIdx = -1), 2000);
		} catch {
			toast.error('Copy failed');
		}
	}

	async function runAction(a: AssistAction) {
		switch (a.type) {
			case 'open_contract':
				if (a.contract_id) goto(`/contracts/${a.contract_id}`);
				break;
			case 'view_clause':
				if (a.contract_id)
					goto(`/contracts/${a.contract_id}?tab=clauses${a.clause_type ? `&search=${encodeURIComponent(a.clause_type)}` : ''}`);
				break;
			case 'view_deviations':
				if (a.contract_id) goto(`/contracts/${a.contract_id}?tab=deviation`);
				break;
			case 'copy_redline':
				if (a.text) {
					try {
						await navigator.clipboard.writeText(a.text);
						toast.success('Redline copied to clipboard');
					} catch {
						toast.error('Copy failed');
					}
				}
				break;
			case 'draft_email':
				if (a.contract_id) await assist.draftEmail(a.contract_id);
				break;
			default:
				break;
		}
	}

	function groupSources(sources: AssistSource[]): { name: string; contract_id?: string; clauseTypes: string[]; sections: string[] }[] {
		const groups = new Map<string, { name: string; contract_id?: string; clauseTypes: string[]; sections: string[] }>();
		for (const s of sources) {
			const name = s.contract_name || 'Contract';
			if (!groups.has(name)) groups.set(name, { name, contract_id: s.contract_id, clauseTypes: [], sections: [] });
			const g = groups.get(name)!;
			if (s.contract_id && !g.contract_id) g.contract_id = s.contract_id;
			if (s.clause_type && !g.clauseTypes.includes(s.clause_type)) g.clauseTypes.push(s.clause_type);
			if (s.section && !g.sections.includes(s.section)) g.sections.push(s.section);
		}
		return [...groups.values()];
	}
</script>

<div class="assist-thread" class:compact>
	<div class="thread-body" bind:this={bodyEl}>
		{#if messages.length === 0}
			<div class="thread-empty animate-fadeIn">
				<h2 class="empty-title">Ask about your contracts</h2>
				<p class="empty-sub">Query payment terms, compare clauses, check expiry dates, and more.</p>
				<div class="starter-grid">
					{#each starters as q (q)}
						<button type="button" class="starter-chip" onclick={() => submit(q)}>{q}</button>
					{/each}
				</div>
			</div>
		{:else}
			{#each messages as m, idx (idx)}
				<div class="msg-row {m.role}">
					<div class="bubble {m.role}" class:error={m.error}>
						{#if m.role === 'assistant' && !m.error}
							<button
								type="button"
								class="copy-btn"
								class:copied={copiedIdx === idx}
								onclick={() => copyMessage(m, idx)}
								aria-label="Copy answer"
								title="Copy answer"
							>
								{#if copiedIdx === idx}
									<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#34c759" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
								{:else}
									<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
								{/if}
							</button>
						{/if}
						{#if m.error}
							<div class="error-tag">Failed</div>
							<div class="bubble-text">{m.content}</div>
							<button type="button" class="btn btn-secondary resend-btn" onclick={() => assist.resend()}>Resend message</button>
						{:else if m.role === 'assistant'}
							<div class="chat-markdown">{@html renderMarkdown(m.content)}</div>
							{#if m.results && m.results.length > 0}
								<div class="results-table-wrap">
									<table class="results-table">
										<thead>
											<tr><th>Contract</th><th>Counterparty</th><th>Type</th><th>Expiry</th><th>Value</th></tr>
										</thead>
										<tbody>
											{#each m.results.slice(0, 8) as r (r.id)}
												<tr onclick={() => goto(`/contracts/${r.id}`)}>
													<td class="rt-name">{r.filename}</td>
													<td>{r.counterparty || r.company || '—'}</td>
													<td>{r.contract_type || '—'}</td>
													<td>{r.expiry_date || '—'}</td>
													<td>{r.total_value != null ? `${Number(r.total_value).toLocaleString()} ${r.currency || ''}` : '—'}</td>
												</tr>
											{/each}
										</tbody>
									</table>
									{#if m.results.length > 8}
										<div class="rt-more">+ {m.results.length - 8} more — refine the question to narrow down</div>
									{/if}
								</div>
							{/if}
							{#if m.actions && m.actions.length > 0}
								<div class="action-row">
									{#each m.actions as a (a.type + (a.contract_id ?? '') + a.label)}
										<button type="button" class="action-chip" onclick={() => runAction(a)}>
											{#if a.type === 'open_contract'}
												<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
											{:else if a.type === 'view_deviations'}
												<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 21H3v-5"/><path d="M21 3l-7.5 7.5"/><path d="M3 21l7.5-7.5"/></svg>
											{:else if a.type === 'copy_redline'}
												<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
											{:else if a.type === 'draft_email'}
												<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
											{:else}
												<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
											{/if}
											{a.label}
										</button>
									{/each}
								</div>
							{/if}
							{#if (m.sources && m.sources.length > 0) || m.meta?.route}
								<div class="meta-footer">
									<div class="meta-chips">
										{#if m.meta?.route}<span class="badge badge-blue">{m.meta.route}</span>{/if}
										{#if m.meta?.query_scope}<span class="badge badge-secondary">{m.meta.query_scope}</span>{/if}
										{#if m.meta?.conversation_mode}<span class="badge badge-secondary">{m.meta.conversation_mode}</span>{/if}
									</div>
									{#if m.sources && m.sources.length > 0}
										<div class="sources-label">Sources ({m.sources.length} result{m.sources.length === 1 ? '' : 's'})</div>
										<div class="source-chips">
											{#each groupSources(m.sources) as g (g.name)}
												<button
													type="button"
													class="source-chip"
													onclick={() => g.contract_id && goto(`/contracts/${g.contract_id}`)}
													disabled={!g.contract_id}
												>
													<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#0071e3" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
													<span class="source-name">{g.name}</span>
													{#each g.clauseTypes.slice(0, 3) as ct (ct)}
														<span class="badge badge-blue badge-sm">{ct.replace(/_/g, ' ')}</span>
													{/each}
													{#if g.sections.length > 0}
														<span class="source-sections">{g.sections.map((s) => `§${s}`).join(', ')}</span>
													{/if}
												</button>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						{:else}
							<div class="bubble-text">{m.content}</div>
						{/if}
					</div>
				</div>
			{/each}
			{#if assist.loading}
				<div class="msg-row assistant">
					<div class="typing-pill">
						<span class="spinner spinner-md"></span>
						<span>Analyzing contracts…</span>
					</div>
				</div>
			{/if}
			{#if lastSuggested.length > 0 && !assist.loading}
				<div class="followup-row animate-fadeIn">
					{#each lastSuggested as q (q)}
						<button type="button" class="followup-chip" onclick={() => submit(q)}>{q}</button>
					{/each}
				</div>
			{/if}
		{/if}
	</div>

	{#if contextName}
		<div class="context-bar">
			<span class="context-chip">
				<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
				Viewing: {contextName}
				<button
					type="button"
					class="context-clear"
					title="Ask portfolio-wide instead"
					aria-label="Clear contract context"
					onclick={() => assist.setPageContext(null)}
				>
					<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
				</button>
			</span>
		</div>
	{/if}
	<div class="composer frosted-bar">
		<textarea
			bind:this={textareaEl}
			class="composer-input"
			rows="1"
			placeholder="Ask a question about your contracts..."
			bind:value={input}
			oninput={autoGrow}
			disabled={assist.loading}
			aria-label="Ask Jaggaer Assist"
			onkeydown={(e) => {
				if (e.key === 'Enter' && !e.shiftKey) {
					e.preventDefault();
					submit();
				}
			}}
		></textarea>
		<button
			type="button"
			class="send-btn"
			onclick={() => submit()}
			disabled={assist.loading || !input.trim()}
			aria-label="Send"
		>
			{#if assist.loading}
				<span class="spinner spinner-sm" style="border-top-color: #fff;"></span>
			{:else}
				<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg>
			{/if}
		</button>
	</div>
</div>

<style>
	.assist-thread {
		display: flex;
		flex-direction: column;
		height: 100%;
		min-height: 0;
	}
	.thread-body {
		flex: 1;
		overflow-y: auto;
		padding: 18px;
		min-height: 0;
	}
	.compact .thread-body {
		padding: 14px;
	}

	/* Empty state */
	.thread-empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		text-align: center;
		height: 100%;
		min-height: 220px;
		gap: 8px;
		padding: 12px;
	}
	.empty-title {
		font-size: 1.25rem;
		font-weight: 600;
		color: var(--text-primary);
	}
	.compact .empty-title {
		font-size: 1.05rem;
	}
	.empty-sub {
		color: var(--text-tertiary);
		font-size: 0.875rem;
		margin-bottom: 10px;
	}
	.starter-grid {
		display: flex;
		flex-direction: column;
		gap: 8px;
		width: 100%;
		max-width: 300px;
	}
	.starter-chip {
		text-align: left;
		background: var(--bg-panel);
		border: 1px solid var(--border-subtle);
		border-radius: 12px;
		padding: 10px 14px;
		font-size: 0.85rem;
		color: var(--text-primary);
		cursor: pointer;
		box-shadow: var(--shadow-sm);
		transition: border-color 150ms ease, box-shadow 150ms ease, transform 150ms var(--ease-out);
	}
	.starter-chip:hover {
		border-color: var(--accent-primary);
		box-shadow: var(--shadow-md);
		transform: translateY(-1px);
	}

	/* Messages */
	.msg-row {
		display: flex;
		margin-bottom: 12px;
	}
	.msg-row.user {
		justify-content: flex-end;
	}
	.msg-row.assistant {
		justify-content: flex-start;
	}
	.bubble {
		position: relative;
		max-width: 85%;
	}
	.bubble.user {
		background: #0071e3;
		color: #fff;
		border-radius: 18px 18px 4px 18px;
		padding: 10px 14px;
		max-width: 70%;
		font-size: 0.9375rem;
		line-height: 1.6;
	}
	.bubble.assistant {
		background: #ffffff;
		border-radius: 18px 18px 18px 4px;
		box-shadow: 0 1px 8px rgba(0, 0, 0, 0.06), 0 0 1px rgba(0, 0, 0, 0.08);
		padding: 14px 18px 12px;
	}
	.bubble.assistant.error {
		background: #fef2f2;
		border: 1px solid #fecaca;
	}
	.bubble-text {
		white-space: pre-wrap;
		font-size: 0.9375rem;
		line-height: 1.6;
	}
	.error-tag {
		display: inline-block;
		text-transform: uppercase;
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.06em;
		color: #b91c1c;
		margin-bottom: 6px;
	}
	.resend-btn {
		margin-top: 10px;
		height: 30px;
		font-size: 12px;
	}
	.copy-btn {
		position: absolute;
		top: 10px;
		right: 10px;
		background: transparent;
		border: none;
		color: #86868b;
		cursor: pointer;
		opacity: 0.3;
		transition: opacity 150ms ease;
		padding: 4px;
		line-height: 0;
	}
	.bubble.assistant:hover .copy-btn {
		opacity: 1;
	}
	.copy-btn.copied {
		opacity: 1;
	}

	/* Action chips */
	.action-row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 10px;
	}
	.action-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 5px 12px;
		border-radius: var(--radius-pill);
		border: 1.5px solid var(--accent-primary);
		background: transparent;
		color: var(--accent-primary);
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
		transition: background 150ms ease, color 150ms ease, transform 150ms var(--ease-out);
	}
	.action-chip:hover {
		background: var(--accent-primary);
		color: #fff;
		transform: scale(1.02);
	}

	/* Meta footer: route chips + sources */
	.meta-footer {
		border-top: 1px solid #f0f0f2;
		margin: 12px -18px -12px;
		padding: 10px 18px 12px;
		background: #fafafa;
		border-radius: 0 0 18px 4px;
	}
	.meta-chips {
		display: flex;
		gap: 6px;
		flex-wrap: wrap;
	}
	.sources-label {
		text-transform: uppercase;
		font-size: 10px;
		font-weight: 600;
		letter-spacing: 0.05em;
		color: var(--text-tertiary);
		margin: 8px 0 6px;
	}
	.source-chips {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.source-chip {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
		background: #f5f5f7;
		border: none;
		border-radius: 10px;
		padding: 7px 10px;
		font-size: 12px;
		cursor: pointer;
		text-align: left;
		transition: background 150ms ease;
	}
	.source-chip:hover:not(:disabled) {
		background: #e8e8ed;
	}
	.source-chip:disabled {
		cursor: default;
	}
	.source-name {
		font-weight: 600;
		color: var(--text-primary);
		max-width: 220px;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.source-sections {
		margin-left: auto;
		color: var(--text-tertiary);
		font-size: 11px;
	}

	/* Typing indicator */
	.typing-pill {
		display: inline-flex;
		align-items: center;
		gap: 10px;
		background: #fff;
		border-radius: var(--radius-pill);
		box-shadow: 0 1px 8px rgba(0, 0, 0, 0.06);
		padding: 9px 16px;
		font-size: 0.85rem;
		color: var(--text-secondary);
	}

	/* Follow-up suggestion chips */
	.followup-row {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
		margin: 2px 0 8px;
	}
	.followup-chip {
		background: rgba(0, 113, 227, 0.06);
		border: 1px solid rgba(0, 113, 227, 0.25);
		color: var(--accent-primary);
		border-radius: var(--radius-pill);
		padding: 6px 12px;
		font-size: 12px;
		cursor: pointer;
		transition: background 150ms ease, color 150ms ease;
	}
	.followup-chip:hover {
		background: var(--accent-primary);
		color: #fff;
	}

	/* Metadata-route results table */
	.results-table-wrap {
		margin-top: 10px;
		border: 1px solid var(--border-subtle);
		border-radius: 10px;
		overflow-x: auto;
	}
	.results-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 12px;
	}
	.results-table th {
		background: #f5f5f7;
		border-bottom: 2px solid #d2d2d7;
		padding: 6px 10px;
		text-align: left;
		font-weight: 600;
		white-space: nowrap;
	}
	.results-table td {
		border-bottom: 1px solid #e8e8ed;
		padding: 6px 10px;
		white-space: nowrap;
	}
	.results-table tbody tr {
		cursor: pointer;
		transition: background 120ms ease;
	}
	.results-table tbody tr:hover {
		background: var(--bg-hover);
	}
	.results-table tbody tr:last-child td {
		border-bottom: none;
	}
	.rt-name {
		max-width: 220px;
		overflow: hidden;
		text-overflow: ellipsis;
		font-weight: 500;
	}
	.rt-more {
		padding: 6px 10px;
		font-size: 11px;
		color: var(--text-tertiary);
		background: #fafafa;
	}

	/* Page-context chip */
	.context-bar {
		padding: 6px 14px 0;
	}
	.context-chip {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		background: rgba(0, 113, 227, 0.08);
		border: 1px solid rgba(0, 113, 227, 0.25);
		color: var(--accent-primary);
		border-radius: var(--radius-pill);
		padding: 4px 10px;
		font-size: 11.5px;
		font-weight: 500;
		max-width: 100%;
	}
	.context-clear {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: none;
		color: var(--accent-primary);
		cursor: pointer;
		padding: 2px;
		border-radius: 999px;
		line-height: 0;
	}
	.context-clear:hover {
		background: rgba(0, 113, 227, 0.15);
	}

	/* Composer */
	.composer {
		display: flex;
		align-items: flex-end;
		gap: 10px;
		padding: 12px 14px;
	}
	.composer-input {
		flex: 1;
		resize: none;
		min-height: 44px;
		max-height: 120px;
		padding: 11px 16px;
		background: var(--bg-hover);
		border: 1.5px solid var(--border-subtle);
		border-radius: 22px;
		color: var(--text-primary);
		font-size: 0.9rem;
		line-height: 1.45;
		outline: none;
		transition: border-color 150ms ease, box-shadow 150ms ease, background 150ms ease;
	}
	.composer-input:focus {
		border-color: var(--accent-primary);
		background: #fff;
		box-shadow: var(--ring);
	}
	.send-btn {
		width: 44px;
		height: 44px;
		flex-shrink: 0;
		border-radius: 999px;
		border: none;
		background: var(--accent-primary);
		color: #fff;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		transition: background 150ms ease, transform 150ms var(--ease-out);
	}
	.send-btn:hover:not(:disabled) {
		background: var(--accent-hover);
		transform: scale(1.04);
	}
	.send-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
