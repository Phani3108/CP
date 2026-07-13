<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { toast } from '$lib/toastStore';
	import { createAdaptivePoller } from '$lib/poller';

	type Template = {
		id: string;
		name: string;
		description: string | null;
		status: string;
		clause_count: number;
		created_at: string;
	};
	type TemplateClause = {
		id: string;
		clause_type: string;
		text_content: string;
		position_index: number;
		has_embedding: boolean;
	};

	let templates = $state<Template[]>([]);
	let loaded = $state(false);
	let showCreate = $state(false);
	let creating = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let newText = $state('');
	let uploading = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	let expandedId = $state<string | null>(null);
	let expandedClauses = $state<TemplateClause[]>([]);
	let loadingClauses = $state(false);

	const anyProcessing = $derived(templates.some((t) => t.status === 'PENDING' || t.status === 'PROCESSING'));

	async function load() {
		try {
			const res = await apiFetch('/api/v1/templates');
			if (res.ok) {
				const data = await res.json();
				templates = data.templates || [];
			}
		} catch {
			/* poller retries */
		} finally {
			loaded = true;
		}
	}

	const poller = createAdaptivePoller({
		fn: load,
		isActive: () => anyProcessing,
		activeMs: 3000,
		idleMs: 30000
	});

	onMount(() => {
		load();
		poller.start();
	});
	onDestroy(() => poller.stop());

	async function createTemplate() {
		const name = newName.trim();
		const raw_text = newText.trim();
		if (!name || !raw_text) {
			toast.error('Name and template text are required');
			return;
		}
		creating = true;
		try {
			const res = await apiFetch('/api/v1/templates', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ name, description: newDescription.trim() || null, raw_text })
			});
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Failed to create template');
			toast.success('Template created — segmenting clauses…');
			showCreate = false;
			newName = '';
			newDescription = '';
			newText = '';
			await load();
		} catch (e: any) {
			toast.error(e?.message || 'Failed to create template');
		} finally {
			creating = false;
		}
	}

	async function uploadFile(files: FileList | null) {
		const file = files?.[0];
		if (!file) return;
		if (!file.name.toLowerCase().endsWith('.pdf')) {
			toast.error('Only PDF templates are supported for now');
			return;
		}
		uploading = true;
		try {
			const fd = new FormData();
			fd.append('file', file);
			const res = await apiFetch('/api/v1/templates/upload', { method: 'POST', body: fd });
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Upload failed');
			toast.success(`Template "${json.name}" uploaded — segmenting clauses…`);
			await load();
		} catch (e: any) {
			toast.error(e?.message || 'Upload failed');
		} finally {
			uploading = false;
			if (fileInput) fileInput.value = '';
		}
	}

	async function toggleExpand(t: Template) {
		if (expandedId === t.id) {
			expandedId = null;
			expandedClauses = [];
			return;
		}
		expandedId = t.id;
		expandedClauses = [];
		loadingClauses = true;
		try {
			const res = await apiFetch(`/api/v1/templates/${t.id}`);
			if (res.ok) {
				const data = await res.json();
				expandedClauses = data.clauses || [];
			}
		} finally {
			loadingClauses = false;
		}
	}

	async function removeTemplate(t: Template) {
		if (!confirm(`Delete template "${t.name}"? Deviation analyses that reference it keep their stored results.`)) return;
		try {
			const res = await apiFetch(`/api/v1/templates/${t.id}`, { method: 'DELETE' });
			if (!res.ok) throw new Error('Delete failed');
			toast.success('Template deleted');
			if (expandedId === t.id) expandedId = null;
			await load();
		} catch (e: any) {
			toast.error(e?.message || 'Delete failed');
		}
	}

	function statusBadge(s: string): string {
		if (s === 'READY') return 'badge-success';
		if (s === 'FAILED') return 'badge-danger';
		return 'badge-warning';
	}
</script>

<svelte:head>
	<title>Templates — ContractsPulse</title>
</svelte:head>

<div class="page-header">
	<div class="page-header-inner">
		<div class="header-title-row">
			<div class="header-icon">
				<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><path d="M9 13h6M9 17h6"/></svg>
			</div>
			<div class="header-content">
				<h1>Standard Templates</h1>
				<div class="subtitle">Your approved paper — the baseline incoming contracts are checked against for deviations.</div>
			</div>
		</div>
	</div>
</div>

<div class="page-content animate-fadeIn">
	<div class="actions-row">
		<button type="button" class="btn btn-primary" onclick={() => (showCreate = true)}>
			<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
			Paste template text
		</button>
		<button type="button" class="btn btn-secondary" disabled={uploading} onclick={() => fileInput?.click()}>
			{#if uploading}<span class="spinner spinner-sm"></span>{:else}
				<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
			{/if}
			Upload PDF template
		</button>
		<input type="file" accept=".pdf" style="display:none" bind:this={fileInput} onchange={(e) => uploadFile((e.target as HTMLInputElement).files)} />
	</div>

	{#if !loaded}
		<div class="empty-card" style="text-align:center;"><span class="spinner spinner-md"></span></div>
	{:else if templates.length === 0}
		<div class="empty-card" style="text-align:center;">
			No templates yet. Paste or upload your standard paper — e.g. your approved MSA, NDA, or SOW — and
			ContractsPulse will segment and embed its clauses as the deviation baseline.
		</div>
	{:else}
		<div class="template-list">
			{#each templates as t (t.id)}
				<div class="card template-card">
					<div class="template-row">
						<button type="button" class="template-main" onclick={() => toggleExpand(t)}>
							<div class="template-name-row">
								<span class="template-name">{t.name}</span>
								<span class="badge {statusBadge(t.status)}">
									{#if t.status === 'PENDING' || t.status === 'PROCESSING'}<span class="spinner spinner-sm"></span>{/if}
									{t.status}
								</span>
								{#if t.clause_count > 0}
									<span class="badge badge-blue">{t.clause_count} clauses</span>
								{/if}
							</div>
							{#if t.description}<div class="template-desc">{t.description}</div>{/if}
						</button>
						<button type="button" class="btn-icon btn-danger-action" title="Delete template" aria-label="Delete template" onclick={() => removeTemplate(t)}>
							<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
						</button>
					</div>
					{#if expandedId === t.id}
						<div class="clause-preview">
							{#if loadingClauses}
								<div class="text-tertiary" style="padding: 10px;"><span class="spinner spinner-sm"></span> Loading clauses…</div>
							{:else if expandedClauses.length === 0}
								<div class="text-tertiary" style="padding: 10px;">No clauses segmented yet.</div>
							{:else}
								{#each expandedClauses as c (c.id)}
									<div class="clause-line">
										<span class="badge badge-secondary badge-sm">#{c.position_index + 1}</span>
										<span class="clause-line-type">{c.clause_type}</span>
										<span class="clause-line-text">{(c.text_content || '').slice(0, 140)}…</span>
									</div>
								{/each}
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if showCreate}
	<div class="modal-backdrop" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) showCreate = false; }}>
		<div class="modal-card card animate-fadeIn" role="dialog" aria-label="Create template">
			<h3 style="margin-bottom: 14px;">New standard template</h3>
			<div class="form-row">
				<label for="tpl-name">Name</label>
				<input id="tpl-name" class="modal-input" placeholder="e.g. Vista Standard — MSA 2026" bind:value={newName} />
			</div>
			<div class="form-row">
				<label for="tpl-desc">Description (optional)</label>
				<input id="tpl-desc" class="modal-input" placeholder="Approved paper for platform subscriptions" bind:value={newDescription} />
			</div>
			<div class="form-row">
				<label for="tpl-text">Template text</label>
				<textarea id="tpl-text" class="modal-textarea" rows="10" placeholder="Paste the full text of your approved standard template…" bind:value={newText}></textarea>
			</div>
			<div class="modal-actions">
				<button type="button" class="btn btn-secondary" onclick={() => (showCreate = false)}>Cancel</button>
				<button type="button" class="btn btn-primary" disabled={creating} onclick={createTemplate}>
					{#if creating}<span class="spinner spinner-sm" style="border-top-color:#fff;"></span>{:else}Create & segment{/if}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.actions-row {
		display: flex;
		gap: 10px;
		margin-bottom: 20px;
	}
	.template-list {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.template-card {
		padding: 16px 18px;
	}
	.template-row {
		display: flex;
		align-items: flex-start;
		gap: 10px;
	}
	.template-main {
		flex: 1;
		min-width: 0;
		background: transparent;
		border: none;
		text-align: left;
		cursor: pointer;
		padding: 0;
	}
	.template-name-row {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-wrap: wrap;
	}
	.template-name {
		font-weight: 600;
		font-size: 14px;
		color: var(--text-primary);
	}
	.template-desc {
		color: var(--text-tertiary);
		font-size: 12.5px;
		margin-top: 4px;
	}
	.clause-preview {
		margin-top: 14px;
		border-top: 1px solid var(--border-subtle);
		padding-top: 10px;
		display: flex;
		flex-direction: column;
		gap: 6px;
		max-height: 340px;
		overflow-y: auto;
	}
	.clause-line {
		display: flex;
		align-items: baseline;
		gap: 8px;
		font-size: 12.5px;
		padding: 4px 6px;
		border-radius: 8px;
	}
	.clause-line:hover {
		background: var(--bg-hover);
	}
	.clause-line-type {
		font-weight: 600;
		white-space: nowrap;
		color: var(--text-primary);
	}
	.clause-line-text {
		color: var(--text-tertiary);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(29, 29, 31, 0.4);
		backdrop-filter: blur(4px);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 300;
		padding: 20px;
	}
	.modal-card {
		width: 100%;
		max-width: 640px;
		padding: 24px;
		max-height: calc(100vh - 80px);
		overflow-y: auto;
	}
	.form-row {
		margin-bottom: 14px;
	}
	.form-row label {
		display: block;
		font-size: 12px;
		font-weight: 500;
		color: var(--text-secondary);
		margin-bottom: 6px;
	}
	.modal-input,
	.modal-textarea {
		width: 100%;
		padding: 10px 14px;
		background: var(--bg-hover);
		border: 1.5px solid var(--border-subtle);
		border-radius: var(--radius-md);
		color: var(--text-primary);
		font-size: 13px;
		outline: none;
		transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
	}
	.modal-textarea {
		resize: vertical;
		min-height: 160px;
		font-family: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
		font-size: 12px;
		line-height: 1.5;
	}
	.modal-input:focus,
	.modal-textarea:focus {
		border-color: var(--accent-primary);
		background: #fff;
		box-shadow: var(--ring);
	}
	.modal-actions {
		display: flex;
		justify-content: flex-end;
		gap: 10px;
		margin-top: 6px;
	}
</style>
