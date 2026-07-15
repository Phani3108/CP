<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import { apiFetch } from '$lib/api';
	import { toast } from '$lib/toastStore';
	import { assist } from '$lib/assist.svelte';
	import { authState } from '$lib/auth.svelte';
	import ClampText from '$lib/ClampText.svelte';
	import WordDiff from '$lib/WordDiff.svelte';

	type ContractDetail = {
		id: string;
		filename: string;
		status: string;
		metadata_json?: any;
		overall_risk?: string | null;
		has_original_file?: boolean;
		lifecycle_stage?: string | null;
		created_at: string;
	};

	type Clause = {
		id: string;
		clause_type: string;
		text_content: string;
		risk_level: string;
		risk_reasoning?: string | null;
		redline_suggestion?: string | null;
		risk_debug_json?: any;
	};

	const contractId = $derived($page.params.id);

	let contract = $state<ContractDetail | null>(null);
	let clauses = $state<Clause[]>([]);
	let isLoading = $state(true);
	let isClausesLoading = $state(false);
	
	// Polling and live steps for processing state
	let pollInterval: any;
	let stopwatchInterval: any;
	let now = $state(Date.now());
	let liveSteps = $state<{text: string, startTime: number, endTime: number | null}[]>([]);
	let processingStatus = $state<any>(null);
	let traceEvents = $state<any[]>([]);
	let isTraceLoading = $state(false);

	// Vendor email draft (AI)
	let emailModalOpen = $state(false);
	let emailTone = $state<'professional' | 'firm' | 'friendly'>('professional');
	let emailInclude = $state<'unresolved' | 'all'>('unresolved');
	let isEmailLoading = $state(false);
	let emailDraft = $state<{ subject: string; body: string } | null>(null);
	let isCopied = $state(false);

	// Tabs state
	let activeTab = $state('overview'); // 'overview' | 'risks' | 'clauses' | 'obligations' | 'verification' | 'deviation' | 'workflow' | 'history' | 'trace'
	const VALID_TABS = ['overview', 'risks', 'clauses', 'obligations', 'changes', 'deviation', 'workflow', 'history', 'trace'];
	let tabBarEl = $state<HTMLElement | null>(null);
	let riskLegendOpen = $state(false);

	// Document panel: show the real uploaded PDF ("Original") vs the annotated OCR text ("Text").
	type DocView = 'original' | 'text';
	let docView = $state<DocView>('text');
	let docViewInit = false;
	let originalUrl = $state<string | null>(null);
	let originalLoading = $state(false);
	let originalError = $state(false);
	const hasOriginalFile = $derived(!!contract?.has_original_file);

	async function loadOriginalFile() {
		if (originalUrl || originalLoading || !contractId) return;
		originalLoading = true;
		originalError = false;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/file`);
			if (!res.ok) throw new Error('no original file');
			originalUrl = URL.createObjectURL(await res.blob());
		} catch {
			originalError = true;
		} finally {
			originalLoading = false;
		}
	}

	// Fetch the PDF lazily when the Original view is active.
	$effect(() => {
		if (docView === 'original' && hasOriginalFile && !originalUrl && !originalLoading && !originalError) {
			loadOriginalFile();
		}
	});

	// Revoke the blob URL and reset when navigating to another contract (the component is reused
	// across [id] param changes) and on destroy.
	$effect(() => {
		const _id = contractId;
		return () => {
			if (originalUrl) URL.revokeObjectURL(originalUrl);
			originalUrl = null;
			originalError = false;
			docViewInit = false;
		};
	});
	// Unified "Changes" tab sub-section: what changed vs previous version | redline resolution | trend
	let changesSection = $state<'whatchanged' | 'redlines' | 'trend'>('whatchanged');

	// Obligations state
	type ObligationItem = {
		title: string;
		description: string;
		party_responsible: string;
		due_trigger: string;
		category: string;
	};
	let obligations = $state<ObligationItem[] | null>(null);
	let obligationsGenerated = $state(false);
	let isObligationsLoading = $state(false);

	// Filters for clauses
	let clauseSearchQuery = $state('');
	let clauseRiskFilter = $state('ALL'); // 'ALL' | 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
	let expandedClauses = $state<Record<string, boolean>>({});

	// Modals
	let deleteModalOpen = $state(false);

	// Version history and revision upload states
	let allContracts = $state<ContractDetail[]>([]);
	let versionDropdownOpen = $state(false);
	let uploadRevisionModalOpen = $state(false);
	let revisionParty = $state<'internal' | 'counterparty'>('internal');
	let revisionInputType = $state<'file' | 'text'>('file');
	let revisionText = $state('');
	let isRevisionUploading = $state(false);
	let revisionFileInput = $state<HTMLInputElement | null>(null);
	let revisionFile = $state<File | null>(null);

	// Highlight & Bi-directional sync states
	let hoveredClauseId = $state<string | null>(null);
	let selectedClauseId = $state<string | null>(null);

	function escapeHtml(text: string) {
		return text
			.replace(/&/g, '&amp;')
			.replace(/</g, '&lt;')
			.replace(/>/g, '&gt;')
			.replace(/"/g, '&quot;')
			.replace(/'/g, '&#039;');
	}

	function formatDocumentName(filename: string) {
		if (!filename) return '';
		let clean = filename.replace(/\.[a-zA-Z0-9]+$/, '');
		clean = clean.replace(/[_-]/g, ' ');
		return clean.replace(/\b\w/g, c => c.toUpperCase());
	}

	let highlightedHtml = $derived.by(() => {
		const rawText = contract?.metadata_json?.raw_text;
		if (!rawText) return '';
		if (clauses.length === 0) return escapeHtml(rawText);

		interface Match {
			start: number;
			end: number;
			clause: Clause;
		}
		const matches: Match[] = [];

		clauses.forEach((clause) => {
			const textToFind = clause.text_content.trim();
			if (!textToFind) return;

			let index = rawText.indexOf(textToFind);
			while (index !== -1) {
				const overlaps = matches.some(m => 
					(index >= m.start && index < m.end) || 
					(index + textToFind.length > m.start && index + textToFind.length <= m.end) ||
					(m.start >= index && m.start < index + textToFind.length)
				);

				if (!overlaps) {
					matches.push({
						start: index,
						end: index + textToFind.length,
						clause
					});
				}

				index = rawText.indexOf(textToFind, index + 1);
			}
		});

		matches.sort((a, b) => a.start - b.start);

		let result = '';
		let currentIndex = 0;

		matches.forEach((match) => {
			if (match.start > currentIndex) {
				result += escapeHtml(rawText.slice(currentIndex, match.start));
			}

			const isHovered = hoveredClauseId === match.clause.id;
			const isSelected = selectedClauseId === match.clause.id;
			const activeClass = (isHovered || isSelected) ? 'active-highlight' : '';
			const riskClass = `risk-${match.clause.risk_level.toLowerCase()}`;
			
			const badgePrefixMap: Record<string, string> = {
				'LIMITATION OF LIABILITY': 'LOB',
				'INDEMNIFICATION': 'IND',
				'TERMINATION': 'TRM',
				'INTELLECTUAL PROPERTY': 'IP',
				'CONFIDENTIALITY': 'CON',
				'WARRANTY': 'WRN',
				'GOVERNING LAW': 'GOV',
				'LIQUIDATED DAMAGES': 'LIQ',
				'FORCE MAJEURE': 'FOR',
				'PAYMENT': 'PAY'
			};
			const cleanType = match.clause.clause_type.toUpperCase().trim();
			let badgeText = badgePrefixMap[cleanType] || cleanType.slice(0, 3);

			result += `<span class="clause-highlight ${riskClass} ${activeClass}" data-clause-id="${match.clause.id}" id="highlight-${match.clause.id}"><span class="highlight-badge">${escapeHtml(badgeText)}</span>${escapeHtml(match.clause.text_content)}</span>`;

			currentIndex = match.end;
		});

		if (currentIndex < rawText.length) {
			result += escapeHtml(rawText.slice(currentIndex));
		}

		return result;
	});

	type ClauseMarker = { clauseId: string; risk: string; topPct: number };
	let clauseMarkers = $derived.by(() => {
		const rawText = contract?.metadata_json?.raw_text || '';
		if (!rawText || clauses.length === 0) return [] as ClauseMarker[];
		const total = Math.max(1, rawText.length);
		const markers: ClauseMarker[] = [];
		for (const clause of clauses) {
			const t = (clause.text_content || '').trim();
			if (!t) continue;
			const idx = rawText.indexOf(t);
			if (idx < 0) continue;
			const topPct = Math.max(0, Math.min(1, idx / total));
			markers.push({ clauseId: clause.id, risk: clause.risk_level, topPct });
		}
		return markers;
	});

	function jumpToClause(clauseId: string) {
		selectedClauseId = clauseId;
		const highlightEl = document.getElementById(`highlight-${clauseId}`);
		if (highlightEl) highlightEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	function handleDocumentClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		const highlightSpan = target.closest('.clause-highlight');
		if (highlightSpan) {
			const clauseId = highlightSpan.getAttribute('data-clause-id');
			if (clauseId) {
				selectedClauseId = clauseId;
				activeTab = 'clauses';
				
				expandedClauses = {
					...expandedClauses,
					[clauseId]: true
				};

				setTimeout(() => {
					const cardEl = document.getElementById(`clause-card-${clauseId}`);
					if (cardEl) {
						cardEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
					}
				}, 100);
			}
		}
	}

	function handleDocumentMouseOver(e: MouseEvent) {
		const target = e.target as HTMLElement;
		const highlightSpan = target.closest('.clause-highlight');
		if (highlightSpan) {
			hoveredClauseId = highlightSpan.getAttribute('data-clause-id');
		} else {
			hoveredClauseId = null;
		}
	}

	function handleDocumentMouseOut(e: MouseEvent) {
		hoveredClauseId = null;
	}

	function handleClauseCardClick(clauseId: string) {
		selectedClauseId = clauseId;
		toggleClauseExpand(clauseId);
		syncScrollToHighlight(clauseId);
	}

	async function fetchObligations() {
		if (!contract || contract.status !== 'COMPLETED') return;
		isObligationsLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contract.id}/obligations`);
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Failed to load obligations');
			obligations = json.obligations ?? null;
			obligationsGenerated = Boolean(json.generated);
		} catch (e: any) {
			toast.error(e?.message || 'Failed to load obligations');
		} finally {
			isObligationsLoading = false;
		}
	}

	async function generateObligations() {
		if (!contract || contract.status !== 'COMPLETED') return;
		isObligationsLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contract.id}/obligations/generate`, { method: 'POST' });
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Failed to generate obligations');
			toast.success('Generating obligations…');
			// Poll until generated
			const startedAt = Date.now();
			while (Date.now() - startedAt < 30000) {
				await new Promise((r) => setTimeout(r, 1500));
				const check = await apiFetch(`/api/v1/contracts/${contract.id}/obligations`);
				const cj = await check.json().catch(() => ({}));
				if (check.ok && cj.generated) {
					obligations = cj.obligations ?? [];
					obligationsGenerated = true;
					break;
				}
			}
		} catch (e: any) {
			toast.error(e?.message || 'Failed to generate obligations');
		} finally {
			isObligationsLoading = false;
		}
	}

	async function generateVendorEmail() {
		if (!contract) return;
		const hadDraft = !!emailDraft;
		// Open the modal immediately so the user sees the loading state while we draft.
		emailModalOpen = true;
		isEmailLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contract.id}/redlines/email`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ tone: emailTone, include: emailInclude })
			});
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Failed to generate email');
			emailDraft = json.email || null;
		} catch (e: any) {
			toast.error(e?.message || 'Failed to generate email');
			// If this was the first attempt (no existing draft), don't strand the user on a skeleton.
			if (!hadDraft) emailModalOpen = false;
		} finally {
			isEmailLoading = false;
		}
	}

	async function copyEmailDraft() {
		if (!emailDraft) return;
		const text = `Subject: ${emailDraft.subject}\n\n${emailDraft.body}`;
		try {
			await navigator.clipboard.writeText(text);
			toast.success('Email copied to clipboard.');
			isCopied = true;
			setTimeout(() => {
				isCopied = false;
			}, 2000);
		} catch {
			toast.error('Failed to copy email.');
		}
	}

	function syncScrollToHighlight(clauseId: string) {
		setTimeout(() => {
			const highlightEl = document.getElementById(`highlight-${clauseId}`);
			if (highlightEl) {
				highlightEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
			}
		}, 80);
	}

	// Reactively initialize live processing trace steps
	$effect(() => {
		if (contract && contract.status === 'PROCESSING') {
			const step = contract.metadata_json?.processing_step;
			if (step) {
				const lastStep = liveSteps[liveSteps.length - 1];
				if (!lastStep || lastStep.text !== step) {
					if (lastStep && !lastStep.endTime) {
						lastStep.endTime = Date.now();
					}
					liveSteps = [...liveSteps, { text: step, startTime: Date.now(), endTime: null }];
				}
			}
		} else if (contract && (contract.status === 'COMPLETED' || contract.status === 'FAILED')) {
			const lastStep = liveSteps[liveSteps.length - 1];
			if (lastStep && !lastStep.endTime) {
				lastStep.endTime = Date.now();
			}
		}
	});

	// Dynamic search and severity filtering for clauses
	let filteredClauses = $derived(
		clauses.filter((c) => {
			const matchesSearch = 
				c.clause_type.toLowerCase().includes(clauseSearchQuery.toLowerCase()) ||
				c.text_content.toLowerCase().includes(clauseSearchQuery.toLowerCase()) ||
				(c.risk_reasoning || '').toLowerCase().includes(clauseSearchQuery.toLowerCase());
			
			const matchesRisk = 
				clauseRiskFilter === 'ALL' || 
				c.risk_level.toUpperCase() === clauseRiskFilter;
			
			return matchesSearch && matchesRisk;
		})
	);

	function formatStopwatch(ms: number) {
		if (!ms || ms < 0) return "0.0s";
		return (ms / 1000).toFixed(1) + "s";
	}

	function formatEta(seconds: number | null | undefined) {
		if (seconds === null || seconds === undefined) return '--';
		if (seconds <= 1) return '< 1s';
		const s = Math.max(0, Math.floor(seconds));
		const m = Math.floor(s / 60);
		const r = s % 60;
		if (m <= 0) return `~${r}s`;
		return `~${m}m ${r}s`;
	}

	function getProcessingPhase(step: string | undefined | null) {
		if (!step) return "Initializing";
		const s = step.toLowerCase();
		if (s.includes('segment')) return "Planning";
		if (s.includes('analyz')) return "Thinking";
		if (s.includes('sav')) return "Executing";
		return "Processing";
	}

	async function fetchContractStatus() {
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/status`);
			if (!res.ok) return;
			processingStatus = await res.json();
		} catch {
			// non-fatal
		}
	}

	async function fetchContractEvents() {
		if (!contractId) return;
		isTraceLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/events`);
			if (!res.ok) return;
			const json = await res.json();
			traceEvents = json.events || [];
		} catch {
			// non-fatal
		} finally {
			isTraceLoading = false;
		}
	}

	async function fetchContractDetails(silent = false) {
		if (!silent) isLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}`);
			if (res.ok) {
				const data = await res.json();
				contract = data.contract;

				// Default to the original document when we have it; else the annotated text.
				if (contract && !docViewInit) {
					docViewInit = true;
					docView = contract.has_original_file ? 'original' : 'text';
				}

				if (contract) {
					if (contract.status === 'PROCESSING') {
						fetchContractStatus();
						if (activeTab === 'overview') {
							// If processing, default tab is Overview to see trace
							activeTab = 'overview';
						}
					} else {
						processingStatus = null;
					}
					// Auto-select 'verification' tab if there are redline resolutions and it's a version and no tab has been selected yet.
					if (contract.metadata_json?.parent_contract_id && activeTab === 'overview' && !new URL(window.location.href).searchParams.get('tab')) {
						const hasChanges = contract.metadata_json?.version_changes?.available;
						const hasRedlines = contract.metadata_json?.redline_resolutions?.length > 0;
						if (hasChanges || hasRedlines) {
							activeTab = 'changes';
							changesSection = hasChanges ? 'whatchanged' : 'redlines';
						}
					}
				}
			} else if (res.status === 404) {
				toast.error('Contract not found.');
				goto('/contracts');
			}
		} catch (err) {
			console.error('Error loading contract:', err);
			toast.error('Failed to load contract details.');
		} finally {
			if (!silent) isLoading = false;
		}
	}

	async function loadVersionChain() {
		try {
			const res = await apiFetch('/api/v1/contracts');
			if (res.ok) {
				const data = await res.json();
				allContracts = data.contracts || [];
			}
		} catch (err) {
			console.error('Failed to load version chain:', err);
		}
	}

	let versionChain = $derived.by(() => {
		if (!contract || allContracts.length === 0) return [];

		// Precompute maps for O(1) lookups
		const idToContract = new Map<string, ContractDetail>();
		const parentIdToChild = new Map<string, ContractDetail>();

		for (const c of allContracts) {
			idToContract.set(c.id, c);
			if (c.metadata_json?.parent_contract_id) {
				if (!parentIdToChild.has(c.metadata_json.parent_contract_id)) {
					parentIdToChild.set(c.metadata_json.parent_contract_id, c);
				}
			}
		}
		
		// Trace back to the root parent
		let root: ContractDetail = contract;
		let visited = new Set<string>();
		
		while (root.metadata_json?.parent_contract_id && !visited.has(root.id)) {
			visited.add(root.id);
			const parent = idToContract.get(root.metadata_json.parent_contract_id);
			if (parent) {
				root = parent;
			} else {
				break;
			}
		}
		
		// Build linear chain from root downwards
		const chain: ContractDetail[] = [root];
		let currentId = root.id;
		visited.clear();
		visited.add(root.id);
		
		let nextContract = parentIdToChild.get(currentId);
		while (nextContract && !visited.has(nextContract.id)) {
			visited.add(nextContract.id);
			chain.push(nextContract);
			currentId = nextContract.id;
			nextContract = parentIdToChild.get(currentId);
		}
		
		return chain.map((c, index) => {
			const versionNum = c.metadata_json?.version_number || (index + 1);
			return {
				id: c.id,
				filename: c.filename,
				versionNumber: versionNum,
				label: versionNum === 1 ? `v1 (Original)` : `v${versionNum} (Revised)`,
				status: c.status,
				created_at: c.created_at
			};
		});
	});

	async function handleRevisionSuccess(response: any, loadingToastId: string, successMessage: string, isText: boolean) {
		toast.dismiss(loadingToastId);
		toast.success(successMessage);
		uploadRevisionModalOpen = false;
		if (isText) {
			revisionText = '';
		} else {
			revisionFile = null;
		}
		const data = await response.json();
		if (data.contract_id) {
			goto(`/contracts/${data.contract_id}`);
		} else {
			fetchContractDetails();
			loadVersionChain();
		}
	}

	async function handleRevisionFileUpload() {
		if (!revisionFile) {
			toast.error('Please select a file first.');
			return;
		}
		isRevisionUploading = true;
		const formData = new FormData();
		formData.append('file', revisionFile);

		const loadingToastId = toast.loading(`Uploading revision ${revisionFile.name}...`);
		try {
			const response = await apiFetch(`/api/v1/contracts/upload?parent_id=${contractId}&party=${revisionParty}`, {
				method: 'POST',
				body: formData
			});
			if (response.ok) {
				await handleRevisionSuccess(response, loadingToastId, 'Revision uploaded successfully. AI processing started.', false);
			} else {
				throw new Error('Revision upload failed');
			}
		} catch (error) {
			console.error('Revision upload error:', error);
			toast.dismiss(loadingToastId);
			toast.error('Failed to upload revision.');
		} finally {
			isRevisionUploading = false;
		}
	}

	async function handleRevisionTextUpload() {
		const text = revisionText.trim();
		if (!text) {
			toast.error('Please paste contract text first.');
			return;
		}
		isRevisionUploading = true;
		const loadingToastId = toast.loading('Submitting revision text for analysis...');
		try {
			const response = await apiFetch(`/api/v1/contracts/text?parent_id=${contractId}&party=${revisionParty}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ text })
			});
			if (response.ok) {
				await handleRevisionSuccess(response, loadingToastId, 'Revision text submitted. AI processing started.', true);
			} else {
				throw new Error('Revision text submit failed');
			}
		} catch (error) {
			console.error('Revision text submit error:', error);
			toast.dismiss(loadingToastId);
			toast.error('Failed to submit revision text.');
		} finally {
			isRevisionUploading = false;
		}
	}

	async function handleRevisionUpload() {
		if (revisionInputType === 'file') {
			await handleRevisionFileUpload();
		} else {
			await handleRevisionTextUpload();
		}
	}

	async function fetchClauses() {
		isClausesLoading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/clauses`);
			if (res.ok) {
				const data = await res.json();
				clauses = data.clauses || [];
			}
		} catch (error) {
			console.error('Failed to fetch clauses:', error);
			toast.error('Failed to load contract details.');
		} finally {
			isClausesLoading = false;
		}
	}

	async function handleReprocess() {
		const loadingToastId = toast.loading('Restarting AI pipeline...');
		try {
			const response = await apiFetch(`/api/v1/contracts/${contractId}/reprocess`, {
				method: 'POST'
			});
			if (response.ok) {
				toast.dismiss(loadingToastId);
				toast.success('Reprocessing started.');
				fetchContractDetails();
			} else {
				throw new Error('Reprocess failed');
			}
		} catch (error) {
			toast.dismiss(loadingToastId);
			toast.error('Failed to reprocess contract.');
		}
	}

	async function handleDelete() {
		deleteModalOpen = false;
		const loadingToastId = toast.loading('Deleting contract...');
		try {
			const response = await apiFetch(`/api/v1/contracts/${contractId}`, {
				method: 'DELETE'
			});
			if (response.ok) {
				toast.dismiss(loadingToastId);
				toast.success('Contract deleted.');
				goto('/contracts');
			} else {
				throw new Error('Delete failed');
			}
		} catch (error) {
			toast.dismiss(loadingToastId);
			toast.error('Failed to delete contract.');
		}
	}

	async function copyClauseRedline(clause: Clause) {
		if (!clause?.redline_suggestion) return;
		const payload =
			`${clause.clause_type ? `Clause: ${clause.clause_type}\n\n` : ''}` +
			`Original:\n${clause.text_content || ''}\n\n` +
			`Suggested replacement:\n${clause.redline_suggestion}\n\n` +
			`Rationale:\n${clause.risk_reasoning || ''}\n`;
		try {
			await navigator.clipboard.writeText(payload);
			toast.success('Redline copied to clipboard.');
		} catch (e) {
			try {
				const ta = document.createElement('textarea');
				ta.value = payload;
				ta.style.position = 'fixed';
				ta.style.left = '-9999px';
				document.body.appendChild(ta);
				ta.focus();
				ta.select();
				document.execCommand('copy');
				document.body.removeChild(ta);
				toast.success('Redline copied to clipboard.');
			} catch {
				toast.error('Failed to copy. Select text and copy manually.');
			}
		}
	}

	function toggleClauseExpand(clauseId: string) {
		expandedClauses = {
			...expandedClauses,
			[clauseId]: !expandedClauses[clauseId]
		};
	}

	function timeAgo(dateString: string) {
		const now = new Date();
		const date = new Date(dateString);
		const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);
		
		if (seconds < 60) return 'Just now';
		const minutes = Math.floor(seconds / 60);
		if (minutes < 60) return `${minutes}m ago`;
		const hours = Math.floor(minutes / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		return `${days}d ago`;
	}

	function isAiEventType(t: string) {
		const s = (t || '').toLowerCase();
		return s === 'llm' || s === 'embedding' || s === 'agent' || s === 'ai';
	}

	// React to contractId changes (parameter transitions)
	$effect(() => {
		if (contractId) {
			clauses = [];
			fetchContractDetails();
			loadVersionChain();
		}
	});

	// --- Workflow / lifecycle cockpit ---
	const LIFECYCLE_ORDER = ['DRAFT', 'INTERNAL_REVIEW', 'SENT_TO_SUPPLIER', 'NEGOTIATION', 'LEGAL_APPROVAL', 'SIGNATURE', 'EXECUTED', 'ACTIVE', 'EXPIRED', 'TERMINATED'];
	const WF_MAIN_PATH = ['DRAFT', 'INTERNAL_REVIEW', 'SENT_TO_SUPPLIER', 'NEGOTIATION', 'LEGAL_APPROVAL', 'SIGNATURE', 'EXECUTED', 'ACTIVE'];
	let workflowData = $state<any>(null);
	let approvalsData = $state<any[]>([]);
	let lineageData = $state<any>(null);
	let workflowLoading = $state(false);
	let transitionNote = $state('');

	const wfCurrentIdx = $derived(workflowData ? LIFECYCLE_ORDER.indexOf(workflowData.current_stage) : -1);
	const wfPending = $derived((approvalsData || []).filter((a: any) => a.status === 'PENDING'));
	const wfGateRole = $derived.by(() => {
		const gated = (workflowData?.allowed_transitions || []).find((t: any) => t.gated && t.gate_role);
		if (gated) return gated.gate_role;
		return wfPending[0]?.assigned_role || 'approver';
	});

	function wfStageLabel(stage: string) {
		const s = (workflowData?.stages || []).find((x: any) => x.stage === stage);
		if (s?.label) return s.label;
		return (stage || '').split('_').map((w: string) => w.charAt(0) + w.slice(1).toLowerCase()).join(' ');
	}
	function wfStageOwner(stage: string) {
		const s = (workflowData?.stages || []).find((x: any) => x.stage === stage);
		if (s?.owner) return s.owner;
		if (stage === 'SENT_TO_SUPPLIER') return 'counterparty';
		if (stage === 'LEGAL_APPROVAL') return 'legal';
		return '';
	}
	function wfPhaseBadge(stage: string) {
		switch (stage) {
			case 'DRAFT':
			case 'INTERNAL_REVIEW': return 'badge-blue';
			case 'SENT_TO_SUPPLIER':
			case 'NEGOTIATION': return 'badge-purple';
			case 'LEGAL_APPROVAL': return 'badge-warning';
			case 'SIGNATURE': return 'badge-blue';
			case 'EXECUTED':
			case 'ACTIVE': return 'badge-success';
			case 'TERMINATED': return 'badge-danger';
			default: return 'badge-secondary';
		}
	}
	function hasApproval(stage: string) {
		return (approvalsData || []).some((a: any) => a.target_stage === stage && a.status === 'APPROVED');
	}

	async function loadWorkflow() {
		if (!contractId) return;
		workflowLoading = true;
		try {
			const [wfRes, apRes, lnRes] = await Promise.all([
				apiFetch(`/api/v1/contracts/${contractId}/workflow`),
				apiFetch(`/api/v1/contracts/${contractId}/approvals`),
				apiFetch(`/api/v1/contracts/${contractId}/lineage`)
			]);
			if (wfRes.ok) workflowData = await wfRes.json();
			if (apRes.ok) { const d = await apRes.json(); approvalsData = d.approvals || []; }
			if (lnRes.ok) lineageData = await lnRes.json();
		} catch {
			toast.error('Failed to load workflow');
		} finally {
			workflowLoading = false;
		}
	}

	async function doTransition(toStage: string, note = '') {
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/transition`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ to_stage: toStage, note: note || undefined })
			});
			const j = await res.json().catch(() => ({}));
			if (!res.ok) { toast.error(j?.detail || 'Transition blocked'); return; }
			transitionNote = '';
			await loadWorkflow();
			toast.success(`Moved to ${wfStageLabel(toStage)}`);
		} catch {
			toast.error('Transition failed');
		}
	}

	async function requestApproval(targetStage: string) {
		try {
			const res = await apiFetch('/api/v1/approvals', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ contract_id: contractId, artifact_type: 'stage_advance', target_stage: targetStage })
			});
			const j = await res.json().catch(() => ({}));
			if (!res.ok) { toast.error(j?.detail || 'Failed to request approval'); return; }
			await loadWorkflow();
			toast.success('Approval requested');
		} catch {
			toast.error('Failed to request approval');
		}
	}

	async function decideApproval(id: string, decision: 'APPROVED' | 'REJECTED' | 'CHANGES_REQUESTED') {
		try {
			const res = await apiFetch(`/api/v1/approvals/${id}/decide`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ decision })
			});
			const j = await res.json().catch(() => ({}));
			if (!res.ok) { toast.error(j?.detail || 'Failed to record decision'); return; }
			await loadWorkflow();
			toast.success(`Approval ${decision.toLowerCase().replace('_', ' ')}`);
		} catch {
			toast.error('Failed to record decision');
		}
	}

	// --- Template deviation (first-party paper) ---
	let availableTemplates = $state<any[]>([]);
	let selectedTemplateId = $state('');
	let deviationStarting = $state(false);
	const deviationData = $derived(contract?.metadata_json?.deviation_analysis || null);

	// Changes tab (Slice 3): clause-level "what changed vs previous version" (from the pipeline)
	// and the redline-resolution audit. Both live in metadata_json, so no extra fetch is needed.
	const versionChanges = $derived(contract?.metadata_json?.version_changes || null);
	const hasParentVersion = $derived(!!contract?.metadata_json?.parent_contract_id);
	const changeCount = $derived.by(() => {
		const s = versionChanges?.available ? versionChanges.summary : null;
		return s ? (s.modified || 0) + (s.added || 0) + (s.removed || 0) : 0;
	});

	// Per-deviation reviewer decisions (T14/H2) — a human accept/reject on each deviation or
	// changed clause, captured in the system of record.
	const deviationDecisions = $derived((contract?.metadata_json?.deviation_decisions || {}) as Record<string, any>);
	let decisionBusy = $state<string | null>(null);
	function decisionLabel(d: string): string {
		return ({ ACCEPT: 'Accepted', REJECT: 'Rejected', NEEDS_CHANGES: 'Needs changes', ACCEPTED_FALLBACK: 'Using our language' } as Record<string, string>)[d] || d;
	}
	// H5: surface our approved fallback language wherever a deviation shows up — including
	// What-Changed modified clauses — so the tool resolves, not just diagnoses.
	function standardLanguageFor(clauseType: string | null | undefined): string | null {
		if (!clauseType || !deviationData?.items) return null;
		const norm = (s: string) => (s || '').toLowerCase().trim();
		const item = deviationData.items.find(
			(it: any) => norm(it.clause_type) === norm(clauseType) && it.suggested_language_to_restore_standard
		);
		return item?.suggested_language_to_restore_standard || null;
	}
	async function copyStandardLanguage(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success('Standard language copied');
		} catch {
			toast.error('Copy failed');
		}
	}
	function decisionBadgeClass(d: string): string {
		return ({ ACCEPT: 'badge-success', REJECT: 'badge-danger', NEEDS_CHANGES: 'badge-warning', ACCEPTED_FALLBACK: 'badge-blue' } as Record<string, string>)[d] || 'badge-secondary';
	}
	async function decideDeviation(key: string, decision: string, clauseType?: string | null) {
		decisionBusy = key;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/deviations/${encodeURIComponent(key)}/decide`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ decision, clause_type: clauseType || null })
			});
			if (res.ok && contract) {
				const data = await res.json();
				const meta = { ...(contract.metadata_json || {}) };
				meta.deviation_decisions = { ...(meta.deviation_decisions || {}), [key]: data.decision };
				contract.metadata_json = meta;
				toast.success(`Marked: ${decisionLabel(decision)}`);
			} else {
				toast.error('Could not save decision');
			}
		} catch {
			toast.error('Could not save decision');
		} finally {
			decisionBusy = null;
		}
	}

	// Lifecycle-stage-driven "what to do next" guidance (E5). Turns the workflow into in-product
	// guidance rather than just a diagram. Stage comes from the workflow endpoint when loaded, else
	// the contract's own lifecycle_stage column.
	const cockpitStage = $derived(
		workflowData?.current_stage ?? (contract as any)?.lifecycle_stage ?? contract?.metadata_json?.lifecycle_stage ?? null
	);
	const nextAction = $derived.by(() => {
		const stage = cockpitStage;
		if (!stage || contract?.status !== 'COMPLETED') return null;
		const off = deviationData?.summary?.off_playbook ?? 0;
		const hasParent = !!contract?.metadata_json?.parent_contract_id;
		switch (stage) {
			case 'DRAFT':
				return { tone: 'info', title: 'Draft', detail: 'Review the AI analysis, then advance to internal review when ready.', cta: 'Open workflow', act: () => (activeTab = 'workflow') };
			case 'INTERNAL_REVIEW':
				return { tone: 'info', title: 'Internal review',
					detail: off > 0 ? `Resolve ${off} off-standard clause${off === 1 ? '' : 's'}, then request approval to send to the supplier.` : 'No off-standard clauses flagged. Request approval to send to the supplier.',
					cta: off > 0 ? 'Review deviations' : 'Open workflow', act: () => (activeTab = off > 0 ? 'deviation' : 'workflow') };
			case 'SENT_TO_SUPPLIER':
				return { tone: 'wait', title: 'Sent to supplier', detail: 'Awaiting the counterparty’s returned redlines. Upload their version when it arrives.', cta: 'Upload revision', act: () => (uploadRevisionModalOpen = true) };
			case 'NEGOTIATION':
				return { tone: 'info', title: 'In negotiation', detail: hasParent ? 'Review exactly what changed this round, then counter or advance.' : 'Review the deviations, then counter or advance the stage.', cta: hasParent ? 'Review changes' : 'Review deviations', act: () => (activeTab = hasParent ? 'verification' : 'deviation') };
			case 'LEGAL_APPROVAL':
				return { tone: 'warn', title: 'Legal approval', detail: 'Legal must approve before this can move to signature.', cta: 'Open workflow', act: () => (activeTab = 'workflow') };
			case 'SIGNATURE':
				return { tone: 'info', title: 'Signature', detail: 'Approved — ready for signature.', cta: 'Open workflow', act: () => (activeTab = 'workflow') };
			case 'EXECUTED':
			case 'ACTIVE':
				return { tone: 'ok', title: 'Active', detail: 'Executed. Track obligations and renewal deadlines.', cta: 'View obligations', act: () => (activeTab = 'obligations') };
			default:
				return null; // EXPIRED / TERMINATED — no next action
		}
	});
	const deviationStatus = $derived(contract?.metadata_json?.deviation_status || null);
	const deviationItems = $derived(
		((deviationData?.items || []) as any[]).filter((i) => i.deviation_type !== 'MATCHED')
	);
	const deviationMatched = $derived(
		((deviationData?.items || []) as any[]).filter((i) => i.deviation_type === 'MATCHED')
	);

	async function loadTemplatesList() {
		try {
			const res = await apiFetch('/api/v1/templates');
			if (res.ok) {
				const d = await res.json();
				availableTemplates = (d.templates || []).filter((t: any) => t.status === 'READY');
				if (!selectedTemplateId && availableTemplates.length > 0) selectedTemplateId = availableTemplates[0].id;
			}
		} catch {
			/* non-fatal */
		}
	}

	async function startDeviationAnalysis() {
		if (!selectedTemplateId || deviationStarting) return;
		deviationStarting = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/analyze-deviations`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ template_id: selectedTemplateId })
			});
			const j = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(j?.detail || 'Failed to start deviation analysis');
			toast.success('Deviation analysis started');
			await fetchContractDetails(true);
		} catch (e: any) {
			toast.error(e?.message || 'Failed to start deviation analysis');
		} finally {
			deviationStarting = false;
		}
	}

	function copyRestoreLanguage(item: any) {
		const textToCopy = item.suggested_language_to_restore_standard || item.template_text || '';
		navigator.clipboard.writeText(textToCopy);
		toast.success('Standard language copied to clipboard');
	}

	$effect(() => {
		if (activeTab === 'deviation') loadTemplatesList();
	});

	// --- Portfolio intelligence (overview tab) ---
	let insightsData = $state<any>(null);
	let relatedData = $state<any>(null);
	let insightsLoading = $state(false);
	let showPrecedents = $state(false);

	async function loadIntelligence() {
		if (insightsLoading) return;
		insightsLoading = true;
		try {
			const [insRes, relRes] = await Promise.all([
				apiFetch(`/api/v1/contracts/${contractId}/insights`),
				apiFetch(`/api/v1/contracts/${contractId}/related`)
			]);
			insightsData = insRes.ok ? await insRes.json() : {};
			relatedData = relRes.ok ? await relRes.json() : {};
		} catch {
			insightsData = insightsData || {};
			relatedData = relatedData || {};
		} finally {
			insightsLoading = false;
		}
	}

	function copyIntelText(text: string) {
		navigator.clipboard.writeText(text || '');
		toast.success('Copied to clipboard');
	}

	async function linkSuggestion(s: any) {
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/related`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					related_contract_id: s.contract.id,
					relationship_type: s.relationship_type
				})
			});
			if (!res.ok) {
				const j = await res.json().catch(() => ({}));
				throw new Error(j?.detail || 'Failed to link document');
			}
			toast.success('Documents linked');
			insightsData = null;
			relatedData = null;
			await loadIntelligence();
		} catch (e: any) {
			toast.error(e?.message || 'Failed to link document');
		}
	}

	const intelGaps = $derived((insightsData?.coverage_gaps || []) as any[]);
	const intelPrecedents = $derived((insightsData?.precedents || []) as any[]);
	const intelAmbiguities = $derived((insightsData?.ambiguities || []) as any[]);
	const intelLinks = $derived((relatedData?.links || []) as any[]);
	const intelSuggestions = $derived((relatedData?.suggestions || []) as any[]);
	const intelAttributes = $derived((contract?.metadata_json?.dynamic_attributes || []) as any[]);
	const intelHasContent = $derived(
		intelGaps.length > 0 ||
			intelPrecedents.length > 0 ||
			intelAmbiguities.length > 0 ||
			intelLinks.length > 0 ||
			intelSuggestions.length > 0 ||
			intelAttributes.length > 0
	);

	// Reset intelligence when navigating to a different contract
	$effect(() => {
		contractId;
		insightsData = null;
		relatedData = null;
		showPrecedents = false;
	});

	$effect(() => {
		if (activeTab === 'overview' && contract?.status === 'COMPLETED' && !insightsData && !insightsLoading) loadIntelligence();
	});

	// Jaggaer Assist page context: contract-scoped answers while viewing this contract.
	$effect(() => {
		assist.setPageContext(
			contract ? { contract_id: contract.id, contract_name: contract.filename } : null
		);
		return () => assist.setPageContext(null);
	});

	onMount(() => {
		loadVersionChain();

		// Parse query parameters for deep linking
		const params = new URL(window.location.href).searchParams;
		let tabParam = params.get('tab');
		// 'verification' folded into the unified Changes tab — keep old deep-links working.
		if (tabParam === 'verification') { tabParam = 'changes'; changesSection = 'redlines'; }
		if (tabParam) activeTab = VALID_TABS.includes(tabParam) ? tabParam : 'overview';
		const sectionParam = params.get('section');
		if (sectionParam === 'whatchanged' || sectionParam === 'redlines' || sectionParam === 'trend') changesSection = sectionParam;
		const searchParam = params.get('search');
		if (searchParam) clauseSearchQuery = searchParam;
		const riskParam = params.get('risk');
		if (riskParam) clauseRiskFilter = riskParam.toUpperCase();

		// Auto-poll while analysis or deviation runs in the background
		pollInterval = setInterval(() => {
			if (contract && (contract.status === 'PROCESSING' || contract.metadata_json?.deviation_status === 'RUNNING')) {
				fetchContractDetails(true);
			}
		}, 3000);
	});

	$effect(() => {
		if (activeTab === 'obligations' && contract?.status === 'COMPLETED' && !isObligationsLoading) {
			if (obligations === null || !obligationsGenerated) fetchObligations();
		}
	});

	$effect(() => {
		if (activeTab === 'clauses' && contract?.status === 'COMPLETED' && clauses.length === 0 && !isClausesLoading) {
			fetchClauses();
		}
	});

	$effect(() => {
		if (activeTab === 'trace' && contract?.status === 'PROCESSING') {
			fetchContractEvents();
		}
	});

	$effect(() => {
		if ((activeTab === 'workflow' || activeTab === 'history' || activeTab === 'overview' || activeTab === 'changes') && contract?.status === 'COMPLETED' && !workflowData && !workflowLoading) {
			loadWorkflow();
		}
	});

	// Keep the active tab visible when the tab row scrolls horizontally (e.g. deep-linked to
	// Workflow/History/Trace, or when the docked Assist widget narrows the panel).
	$effect(() => {
		const el = tabBarEl?.querySelector<HTMLElement>('.tab-btn.active');
		if (el) el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
	});

	// Reactively start/stop the stopwatch based on processing status
	$effect(() => {
		if (contract?.status === 'PROCESSING') {
			if (!stopwatchInterval) {
				stopwatchInterval = setInterval(() => {
					now = Date.now();
				}, 100);
			}
		} else {
			if (stopwatchInterval) {
				clearInterval(stopwatchInterval);
				stopwatchInterval = null;
			}
		}
	});

	onDestroy(() => {
		if (pollInterval) clearInterval(pollInterval);
		if (stopwatchInterval) clearInterval(stopwatchInterval);
	});
</script>

{#if isLoading}
	<div class="cockpit-loading">
		<span class="spinner spinner-lg"></span>
		<p>Opening cockpit workspace...</p>
	</div>
{:else if contract}
	<div class="cockpit-header">
		<div class="breadcrumbs">
			<a href="/contracts" class="crumb crumb-link">Contract Repository</a>
			<span class="separator">›</span>
			<div class="version-select-container">
				<button class="crumb active version-dropdown-trigger" onclick={() => versionDropdownOpen = !versionDropdownOpen} aria-expanded={versionDropdownOpen}>
					<span>{formatDocumentName(contract.filename)}</span>
					{#if versionChain.length > 1}
						<span class="version-badge">v{contract.metadata_json?.version_number || 1}</span>
						<svg class="dropdown-chevron" class:open={versionDropdownOpen} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
					{/if}
				</button>
				{#if versionDropdownOpen && versionChain.length > 1}
					<div class="version-dropdown-menu">
						<div class="dropdown-header">Version History</div>
						{#each versionChain as ver}
							<a href="/contracts/{ver.id}" class="version-item" class:active={ver.id === contract.id} onclick={() => versionDropdownOpen = false}>
								<div class="version-item-left">
									<span class="version-item-badge">{ver.label}</span>
									<span class="version-item-date">{timeAgo(ver.created_at)}</span>
								</div>
								{#if ver.id === contract.id}
									<svg class="check-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
								{/if}
							</a>
						{/each}
					</div>
				{/if}
			</div>
			{#if workflowData?.current_stage}
				<span class="separator">›</span>
				<button type="button" class="wf-strip" onclick={() => activeTab = 'workflow'} title="Open workflow cockpit">
					<span class="badge {wfPhaseBadge(workflowData.current_stage)} badge-sm">Workflow: {wfStageLabel(workflowData.current_stage)}</span>
				</button>
			{/if}
		</div>
		<div class="cockpit-actions">
			{#if contract.status === 'COMPLETED'}
				<button class="btn btn-secondary btn-compact btn-ai" onclick={generateVendorEmail} disabled={isEmailLoading} title="Generate a vendor email summarizing requested redlines (AI)">
					{#if isEmailLoading}
						<span class="spinner spinner-sm"></span>
						Drafting…
					{:else}
						<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21.75 6.75v10.5a2.25 2.25 0 0 1-2.25 2.25h-15a2.25 2.25 0 0 1-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0 0 19.5 4.5h-15a2.25 2.25 0 0 0-2.25 2.25m19.5 0v.243a2.25 2.25 0 0 1-1.07 1.916l-7.5 4.615a2.25 2.25 0 0 1-2.36 0L3.32 8.91a2.25 2.25 0 0 1-1.07-1.916V6.75"/></svg>
						Email Vendor
					{/if}
				</button>
				<button class="btn btn-primary btn-compact" onclick={() => uploadRevisionModalOpen = true} title="Upload Revision / Next Version">
					<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
					Upload Revision
				</button>
			{/if}
			{#if contract.status === 'FAILED' || contract.status === 'COMPLETED'}
				<button class="btn btn-secondary btn-compact" onclick={handleReprocess} title="Reprocess Contract">
					<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>
					Reprocess
				</button>
			{/if}
			<button class="btn btn-secondary btn-danger-action btn-compact" onclick={() => deleteModalOpen = true} title="Delete Contract">
				<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg>
				Delete
			</button>
		</div>
	</div>

	{#snippet decisionBar(key: string, clauseType: string | null | undefined)}
		{@const dec = deviationDecisions[key]}
		<div class="decision-bar">
			<span class="decision-label">Reviewer decision:</span>
			<button type="button" class="decision-btn decision-accept" class:active={dec?.decision === 'ACCEPT'} disabled={decisionBusy === key} onclick={() => decideDeviation(key, 'ACCEPT', clauseType)}>Accept</button>
			<button type="button" class="decision-btn decision-fallback" class:active={dec?.decision === 'ACCEPTED_FALLBACK'} disabled={decisionBusy === key} onclick={() => decideDeviation(key, 'ACCEPTED_FALLBACK', clauseType)}>Use our language</button>
			<button type="button" class="decision-btn decision-reject" class:active={dec?.decision === 'REJECT'} disabled={decisionBusy === key} onclick={() => decideDeviation(key, 'REJECT', clauseType)}>Reject</button>
			{#if dec}
				<span class="decision-meta" title={dec.decided_at}>{decisionLabel(dec.decision)} · {dec.actor_email?.split('@')[0] || 'reviewer'}</span>
			{/if}
		</div>
	{/snippet}

	<div class="cockpit-wrapper">
		<!-- Left Panel: Raw Document OCR Text -->
		<div class="document-panel">
			<div class="pane-header">
				<div class="pane-title flex-row">
					<svg class="file-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
					<span>{docView === 'original' && hasOriginalFile ? 'Original Document' : 'Document Text'}</span>
				</div>
				<div class="pane-header-right">
					{#if hasOriginalFile}
						<div class="doc-toggle" role="tablist" aria-label="Document view">
							<button type="button" role="tab" aria-selected={docView === 'original'} class="doc-toggle-btn" class:active={docView === 'original'} onclick={() => (docView = 'original')}>Original</button>
							<button type="button" role="tab" aria-selected={docView === 'text'} class="doc-toggle-btn" class:active={docView === 'text'} onclick={() => (docView = 'text')}>Text</button>
						</div>
					{/if}
					<div class="document-meta-info text-tertiary">
						{#if docView === 'original' && hasOriginalFile}
							Original PDF
						{:else if contract.metadata_json?.raw_text}
							{contract.metadata_json.raw_text.length.toLocaleString()} characters{#if !hasOriginalFile} · <span title="The original file wasn't stored for this contract. Re-upload the PDF to view it here.">original not stored</span>{/if}
						{:else}
							OCR Loading...
						{/if}
					</div>
				</div>
			</div>
			
			<div class="document-body" id="document-body-container">
				{#if docView === 'original' && hasOriginalFile}
					{#if originalUrl}
						<iframe class="pdf-frame" src={originalUrl} title="Original contract document"></iframe>
					{:else if originalLoading}
						<div class="document-placeholder">
							<span class="spinner spinner-md"></span>
							<p>Loading original document…</p>
						</div>
					{:else}
						<div class="document-placeholder">
							<p class="text-tertiary">Could not load the original document.</p>
							<button type="button" class="btn btn-secondary btn-compact" onclick={() => (docView = 'text')}>Show extracted text</button>
						</div>
					{/if}
				{:else}
				{#if clauseMarkers.length > 0}
					<div class="clause-minimap" aria-hidden="true">
						{#each clauseMarkers as m (m.clauseId)}
							<button
								type="button"
								class="minimap-dot risk-{m.risk.toLowerCase()} {selectedClauseId === m.clauseId ? 'active' : ''}"
								style="top: {m.topPct * 100}%;"
								onclick={() => jumpToClause(m.clauseId)}
								title={m.risk}
							></button>
						{/each}
					</div>
				{/if}
				<!-- svelte-ignore a11y_click_events_have_key_events -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<!-- svelte-ignore a11y_mouse_events_have_key_events -->
				<div class="document-paper" onclick={handleDocumentClick} onmouseover={handleDocumentMouseOver} onmouseout={handleDocumentMouseOut}>
					{#if contract.metadata_json?.raw_text}
						{@html highlightedHtml}
					{:else if contract.status === 'PROCESSING'}
						<div class="document-placeholder">
							<span class="spinner spinner-md"></span>
							<p>Raw text is being extracted by AI pipeline...</p>
						</div>
					{:else}
						<div class="document-placeholder">
							<p class="text-tertiary">Raw contract text could not be loaded.</p>
						</div>
					{/if}
				</div>
				{/if}
			</div>
		</div>

		<!-- Right Panel: AI Analysis panel -->
		<div class="analysis-panel">
			<!-- Sleek Tabs Navigation -->
			<div class="analysis-tabs" role="tablist" aria-label="Contract Analysis Tabs" bind:this={tabBarEl}>
				<button role="tab" aria-selected={activeTab === 'overview'} class="tab-btn" class:active={activeTab === 'overview'} onclick={() => activeTab = 'overview'}>
					<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/></svg>
					Overview
				</button>
				{#if contract.status === 'COMPLETED'}
					<button role="tab" aria-selected={activeTab === 'risks'} class="tab-btn" class:active={activeTab === 'risks'} onclick={() => activeTab = 'risks'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
						Key Risks
					</button>
					<button role="tab" aria-selected={activeTab === 'clauses'} class="tab-btn" class:active={activeTab === 'clauses'} onclick={() => activeTab = 'clauses'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>
						Smart Clauses ({clauses.length})
					</button>
					<button role="tab" aria-selected={activeTab === 'obligations'} class="tab-btn" class:active={activeTab === 'obligations'} onclick={() => activeTab = 'obligations'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>
						Obligations
					</button>
					{#if contract.metadata_json?.parent_contract_id}
						<button role="tab" aria-selected={activeTab === 'changes'} class="tab-btn" class:active={activeTab === 'changes'} onclick={() => activeTab = 'changes'}>
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
							Changes
							{#if changeCount > 0}
								<span class="badge badge-purple badge-sm" style="margin-left: 4px;">{changeCount}</span>
							{/if}
						</button>
					{/if}
					<button role="tab" aria-selected={activeTab === 'deviation'} class="tab-btn" class:active={activeTab === 'deviation'} onclick={() => activeTab = 'deviation'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 21H3v-5"/><path d="M21 3l-7.5 7.5"/><path d="M3 21l7.5-7.5"/></svg>
						Template Deviation
						{#if deviationData?.summary?.off_playbook}
							<span class="badge badge-danger badge-sm" style="margin-left: 4px;">{deviationData.summary.off_playbook}</span>
						{/if}
					</button>
					<button role="tab" aria-selected={activeTab === 'workflow'} class="tab-btn" class:active={activeTab === 'workflow'} onclick={() => activeTab = 'workflow'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/><circle cx="7" cy="14" r="1"/><circle cx="11" cy="10" r="1"/><circle cx="14" cy="13" r="1"/></svg>
						Workflow
						{#if workflowData?.gate_blocked || wfPending.length > 0}
							<span class="badge badge-warning badge-sm" style="margin-left: 4px;">!</span>
						{/if}
					</button>
					<button role="tab" aria-selected={activeTab === 'history'} class="tab-btn" class:active={activeTab === 'history'} onclick={() => activeTab = 'history'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v5h5"/><path d="M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/></svg>
						History
					</button>
				{/if}
				{#if contract.status === 'PROCESSING'}
					<button role="tab" aria-selected={activeTab === 'trace'} class="tab-btn" class:active={activeTab === 'trace'} onclick={() => activeTab = 'trace'}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
						System Trace
					</button>
				{/if}
			</div>

			<!-- Tab Content Viewport -->
			<div class="analysis-viewport">
				<!-- OVERVIEW TAB -->
				{#if activeTab === 'overview'}
					<div class="tab-content flex-col">
						{#if nextAction}
							<div class="next-action next-action-{nextAction.tone}">
								<div class="na-icon" aria-hidden="true">
									<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
								</div>
								<div class="na-body">
									<div class="na-title">{nextAction.title} · <span class="na-detail">{nextAction.detail}</span></div>
								</div>
								<button type="button" class="btn btn-primary btn-compact na-cta" onclick={nextAction.act}>{nextAction.cta}</button>
							</div>
						{/if}
						<div class="overview-section">
							<h3 class="subsection-title">Executive Summary</h3>
							<div class="metadata-grid">
								<div class="meta-card bg-panel-glow">
									<span class="mc-label">Filename</span>
									<span class="mc-value truncate" title={contract.filename}>{formatDocumentName(contract.filename)}</span>
								</div>
								<div class="meta-card bg-panel-glow">
									<span class="mc-label">Analysis Status</span>
									<div class="flex-row gap-6">
										{#if contract.status === 'COMPLETED'}
											<span class="badge badge-success">Completed</span>
										{:else if contract.status === 'FAILED'}
											<span class="badge badge-danger">Failed</span>
										{:else}
											{@const phase = getProcessingPhase(contract.metadata_json?.processing_step)}
											<span class="badge badge-warning spinner-badge">
												<span class="spinner spinner-sm"></span>
												{phase}
											</span>
										{/if}
									</div>
								</div>
								<div class="meta-card bg-panel-glow">
									<span class="mc-label">Uploaded</span>
									<span class="mc-value">{timeAgo(contract.created_at)}</span>
								</div>
								<div class="meta-card bg-panel-glow">
									<span class="mc-label">Risk Rating</span>
									{#if contract.status === 'COMPLETED' && contract.overall_risk}
										<div class="flex-row gap-8">
											<span class="risk-indicator risk-{contract.overall_risk.toLowerCase()}"></span>
											<span class="mc-value risk-label font-bold text-{contract.overall_risk.toLowerCase()}">{contract.overall_risk.toLowerCase()}</span>
										</div>
									{:else}
										<span class="mc-value text-tertiary">--</span>
									{/if}
								</div>
							</div>
						</div>

						{#if contract.status === 'COMPLETED'}
							<div class="overview-section">
								<h3 class="subsection-title">Risk Severity Matrix</h3>
								<div class="risk-matrix">
									<div class="matrix-item bg-critical-glow">
										<span class="matrix-count text-critical">{contract.metadata_json?.risk_counts?.CRITICAL || 0}</span>
										<span class="matrix-label">Critical</span>
									</div>
									<div class="matrix-item bg-high-glow">
										<span class="matrix-count text-high">{contract.metadata_json?.risk_counts?.HIGH || 0}</span>
										<span class="matrix-label">High</span>
									</div>
									<div class="matrix-item bg-medium-glow">
										<span class="matrix-count text-medium">{contract.metadata_json?.risk_counts?.MEDIUM || 0}</span>
										<span class="matrix-label">Medium</span>
									</div>
									<div class="matrix-item bg-low-glow">
										<span class="matrix-count text-low">{contract.metadata_json?.risk_counts?.LOW || 0}</span>
										<span class="matrix-label">Low</span>
									</div>
								</div>
							</div>

							{#if contract.metadata_json?.routing_summary}
								<div class="overview-section">
									<h3 class="subsection-title">AI Routing Recommendation</h3>
									<div class="routing-card bg-panel-glow">
										<p>{contract.metadata_json.routing_summary}</p>
									</div>
								</div>
							{/if}
						{/if}

						{#if contract.status === 'PROCESSING'}
							<div class="overview-section">
								<h3 class="subsection-title">Pipeline Progress</h3>
								{#if processingStatus}
									<div class="processing-stats bg-panel-glow">
										<div class="ps-row">
											<span class="ps-label">Stage</span>
											<span class="ps-value">{processingStatus.stage?.label || 'Processing'} ({processingStatus.stage?.index || 0}/{processingStatus.stage?.count || 3})</span>
										</div>
										<div class="ps-row">
											<span class="ps-label">Estimated ETA</span>
											<span class="ps-value font-mono">{formatEta(processingStatus.eta_seconds)}</span>
										</div>
										{#if processingStatus.progress}
											<div class="ps-row">
												<span class="ps-label">Processed Clauses</span>
												<span class="ps-value">{processingStatus.progress.current} of {processingStatus.progress.total}</span>
											</div>
											<div class="progress-bar-container">
												<div class="progress-bar-fill" style="width: {(processingStatus.progress.current / processingStatus.progress.total) * 100}%"></div>
											</div>
										{/if}
									</div>
								{/if}

								<div class="trace-timeline margin-top-16">
									{#each liveSteps as step, i}
										<div class="timeline-step">
											<div class="timeline-icon {step.endTime ? 'done' : 'active'}">
												{#if step.endTime}
													<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>
												{:else}
													<span class="spinner" style="width: 10px; height: 10px; border-width: 2px;"></span>
												{/if}
											</div>
											<div class="timeline-content">
												<div class="timeline-text">{step.text}</div>
												<div class="timeline-time {step.endTime ? 'text-tertiary' : 'time-active'}">
													{#if step.endTime}
														{formatStopwatch(step.endTime - step.startTime)}
													{:else}
														{formatStopwatch(now - step.startTime)}
													{/if}
												</div>
											</div>
										</div>
									{/each}
								</div>
							</div>
						{/if}
						{#if contract.status === 'COMPLETED' && (insightsLoading || intelHasContent)}
							<div class="overview-section">
								<div class="flex-row gap-8" style="margin-bottom: 8px;">
									<h3 class="subsection-title" style="margin: 0;">Portfolio Intelligence</h3>
									<span class="ai-badge" title="Insights derived from your portfolio, templates and linked documents. Verify against contract text.">
										<svg class="ai-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l1.2 4.3L18 8l-4.8 1.7L12 14l-1.2-4.3L6 8l4.8-1.7L12 2z"/></svg>
										AI
									</span>
								</div>
								{#if insightsLoading}
									<div class="clauses-loading">
										<span class="spinner spinner-md"></span>
										<p>Loading portfolio intelligence...</p>
									</div>
								{:else}
									<div class="intel-card bg-panel-glow">
										{#if intelGaps.length > 0}
											<div class="intel-subsection">
												<h4 class="intel-heading">Missing Clauses</h4>
												{#each intelGaps as gap}
													<div class="intel-row">
														<span class="badge badge-danger badge-sm">MISSING</span>
														<div class="intel-row-main">
															<span class="intel-row-title">{gap.clause_type}</span>
															<span class="intel-caption">{gap.source === 'template' ? 'Deleted vs standard template' : `Present in ${gap.presence_pct}% of similar agreements`}</span>
														</div>
														{#if gap.suggested_text}
															<button class="btn btn-secondary intel-btn" onclick={() => copyIntelText(gap.suggested_text)}>Copy standard language</button>
														{/if}
													</div>
												{/each}
											</div>
										{/if}

										{#if intelPrecedents.length > 0}
											<div class="intel-subsection">
												<div class="intel-heading-row">
													<h4 class="intel-heading">Approved Precedents</h4>
													<button class="intel-toggle" onclick={() => (showPrecedents = !showPrecedents)}>
														{showPrecedents ? 'Hide' : `Show ${intelPrecedents.length}`}
													</button>
												</div>
												{#if showPrecedents}
													{#each intelPrecedents as p}
														<div class="intel-row">
															<span class="badge badge-success badge-sm">{p.approved_via === 'feedback' ? 'User approved' : 'Low risk'}</span>
															<div class="intel-row-main">
																<span class="intel-row-title">{p.clause_type}</span>
																<span class="intel-caption">{p.contract_filename}</span>
																{#if p.text_excerpt}
																	<pre class="intel-excerpt">{p.text_excerpt}</pre>
																{/if}
															</div>
															<button class="btn btn-secondary intel-btn" onclick={() => copyIntelText(p.text_excerpt)}>Copy</button>
														</div>
													{/each}
												{/if}
											</div>
										{/if}

										{#if intelAmbiguities.length > 0}
											<div class="intel-subsection">
												<h4 class="intel-heading">Ambiguities</h4>
												{#each intelAmbiguities as amb}
													<div class="intel-row">
														<span class="badge badge-warning badge-sm">{amb.risk_level || 'AMBIGUOUS'}</span>
														<div class="intel-row-main">
															<span class="intel-row-title">{amb.clause_type}</span>
															<span class="intel-caption">{amb.reason}</span>
														</div>
													</div>
												{/each}
											</div>
										{/if}

										{#if intelLinks.length > 0 || intelSuggestions.length > 0}
											<div class="intel-subsection">
												<h4 class="intel-heading">Related Documents</h4>
												{#each intelLinks as l (l.relationship_id)}
													<button class="intel-row intel-row-link" onclick={() => goto(`/contracts/${l.contract.id}`)}>
														<span class="badge badge-blue badge-sm">{l.relationship_type}</span>
														<div class="intel-row-main">
															<span class="intel-row-title">{l.contract.filename}</span>
															<span class="intel-caption">{l.contract.contract_type || 'Document'}{l.contract.business_unit ? ` · ${l.contract.business_unit}` : ''}</span>
														</div>
													</button>
												{/each}
												{#each intelSuggestions as s}
													<div class="intel-row">
														<span class="badge badge-secondary badge-sm">{s.relationship_type}</span>
														<div class="intel-row-main">
															<span class="intel-row-title">{s.contract.filename}</span>
															<span class="intel-caption">Suggested: {s.reference_title}</span>
														</div>
														<button class="btn btn-secondary intel-btn" onclick={() => linkSuggestion(s)}>Link</button>
													</div>
												{/each}
											</div>
										{/if}

										{#if intelAttributes.length > 0}
											<div class="intel-subsection">
												<h4 class="intel-heading">Extracted Attributes</h4>
												<div class="intel-attr-grid">
													{#each intelAttributes as attr}
														<div class="intel-attr">
															<span class="intel-attr-key">{attr.key}</span>
															<span class="intel-attr-value">{attr.value}</span>
														</div>
													{/each}
												</div>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						{/if}
					</div>
				{/if}

				<!-- KEY RISKS TAB -->
				{#if activeTab === 'risks'}
					<div class="tab-content">
						{#if !contract.metadata_json?.top_risks || contract.metadata_json.top_risks.length === 0}
							<div class="empty-tab-state">
								<p class="text-tertiary">No critical or high risks detected in this contract.</p>
							</div>
						{:else}
							<div class="risks-list">
								{#each contract.metadata_json.top_risks as r}
									<div class="risk-glow-card risk-{(r.risk_level || 'LOW').toLowerCase()}">
										<div class="risk-glow-header">
											<span class="risk-glow-type">{r.clause_type || 'Clause'}</span>
											<span class="badge badge-{r.risk_level === 'CRITICAL' || r.risk_level === 'HIGH' ? 'danger' : r.risk_level === 'MEDIUM' ? 'warning' : 'success'}">{r.risk_level}</span>
										</div>
										
										{#if r.auto_renewal}
											<div class="auto-renewal-badge">
												<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
												<span>
													<strong>Auto-renewal detected:</strong> 
													{#if r.auto_renewal.opt_out_days_before_renewal}
														Must opt-out {r.auto_renewal.opt_out_days_before_renewal} days prior.
													{:else}
														Verify renewal terms.
													{/if}
												</span>
											</div>
										{/if}

										<div class="risk-glow-reasoning">
											<strong>Why it matters:</strong> {r.risk_reasoning || 'Flagged clause requires strict visual inspection.'}
										</div>

										{#if r.text_excerpt}
											<div class="risk-glow-excerpt">
												"{r.text_excerpt}"
											</div>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/if}

				<!-- SMART CLAUSES TAB -->
				{#if activeTab === 'clauses'}
					<div class="tab-content flex-col scroll-container">
						<!-- Filters Panel -->
						<div class="clauses-filters bg-panel-glow">
							<div class="search-input-wrapper">
								<svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
								<input 
									type="text" 
									placeholder="Search clauses by keyword..." 
									bind:value={clauseSearchQuery}
									class="clause-search-bar"
									aria-label="Search clauses by keyword"
								/>
							</div>

							<div class="filter-pills flex-row gap-6">
								<button class="filter-pill" class:active={clauseRiskFilter === 'ALL'} onclick={() => clauseRiskFilter = 'ALL'}>
									All
								</button>
								<button class="filter-pill filter-pill-critical" class:active={clauseRiskFilter === 'CRITICAL'} onclick={() => clauseRiskFilter = 'CRITICAL'}>
									Critical
								</button>
								<button class="filter-pill filter-pill-high" class:active={clauseRiskFilter === 'HIGH'} onclick={() => clauseRiskFilter = 'HIGH'}>
									High
								</button>
								<button class="filter-pill filter-pill-medium" class:active={clauseRiskFilter === 'MEDIUM'} onclick={() => clauseRiskFilter = 'MEDIUM'}>
									Medium
								</button>
								<button class="filter-pill filter-pill-low" class:active={clauseRiskFilter === 'LOW'} onclick={() => clauseRiskFilter = 'LOW'}>
									Low
								</button>
							</div>
						</div>

						<!-- Clause List -->
						{#if isClausesLoading}
							<div class="clauses-loading">
								<span class="spinner spinner-md"></span>
								<p>Loading clauses...</p>
							</div>
						{:else if filteredClauses.length === 0}
							<div class="empty-tab-state">
								<p class="text-tertiary">No clauses match the filter parameters.</p>
							</div>
						{:else}
							<div class="clauses-list">
								{#each filteredClauses as clause (clause.id)}
									{@const isExpanded = expandedClauses[clause.id]}
									<div 
										id="clause-card-{clause.id}"
										class="clause-interactive-card risk-{clause.risk_level.toLowerCase()} {isExpanded ? 'expanded' : ''} {selectedClauseId === clause.id || hoveredClauseId === clause.id ? 'active-card' : ''}" 
										role="button"
										tabindex="0"
										onmouseenter={() => { hoveredClauseId = clause.id; }}
										onmouseleave={() => { if (hoveredClauseId === clause.id) hoveredClauseId = null; }}
										onclick={() => handleClauseCardClick(clause.id)}
										onkeydown={(e: KeyboardEvent) => {
											if (e.key === 'Enter' || e.key === ' ') {
												const target = e.target as HTMLElement | null;
												if (!target || !target.closest('button')) {
													e.preventDefault();
													handleClauseCardClick(clause.id);
												}
											}
										}}
									>
										<div class="clause-interactive-header">
											<div class="flex-row gap-8">
												<svg class="chevron-icon" class:rotated={isExpanded} width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
												<span class="clause-type font-semibold">{clause.clause_type}</span>
											</div>
											<span class="badge badge-{clause.risk_level === 'CRITICAL' || clause.risk_level === 'HIGH' ? 'danger' : clause.risk_level === 'MEDIUM' ? 'warning' : 'success'}">
												{clause.risk_level}
											</span>
										</div>

										<div class="clause-interactive-excerpt font-mono">
											{isExpanded ? clause.text_content : (clause.text_content.slice(0, 140) + (clause.text_content.length > 140 ? '...' : ''))}
										</div>

										{#if isExpanded}
											<div class="clause-expanded-section" role="presentation" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
												<div class="clause-reasoning">
													<strong>Rationale:</strong> {clause.risk_reasoning || 'Flagged for strict visual audit.'}
												</div>

												{#if clause.redline_suggestion}
													<div class="clause-redline">
														<div class="clause-redline-head flex-between">
															<strong>Suggested Redline</strong>
															<button class="btn btn-secondary btn-compact text-xs" onclick={() => copyClauseRedline(clause)}>
																Copy Redline
															</button>
														</div>
														<pre class="clause-redline-block">{clause.redline_suggestion}</pre>
													</div>
												{/if}

												{#if clause.risk_debug_json && Object.keys(clause.risk_debug_json).length}
													<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
													<details class="clause-tech" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()}>
														<summary class="font-medium text-xs text-secondary cursor-pointer">Technical Details</summary>
														<div class="tech-grid">
															<div class="tech-row">
																<span class="tech-label">Model</span>
																<span class="tech-value font-mono">{clause.risk_debug_json.model || '--'}</span>
															</div>
															<div class="tech-row">
																<span class="tech-label">Latency</span>
																<span class="tech-value font-mono">{clause.risk_debug_json.latency_ms ?? '--'}ms</span>
															</div>
															<div class="tech-row">
																<span class="tech-label">Composite Score</span>
																<span class="tech-value font-mono">{clause.risk_debug_json.composite_score ?? '--'}</span>
															</div>
															<div class="tech-row">
																<span class="tech-label">Confidence</span>
																<span class="tech-value font-mono">{clause.risk_debug_json.confidence ?? '--'}</span>
															</div>
														</div>
													</details>
												{/if}
											</div>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/if}

				<!-- OBLIGATIONS TAB -->
				{#if activeTab === 'obligations' && contract.status === 'COMPLETED'}
					<div class="tab-content flex-col scroll-container">
						<div class="overview-section">
							<div class="flex-row gap-8" style="margin-bottom: 8px;">
								<h3 class="subsection-title" style="margin: 0;">Actionable Obligations</h3>
								<span class="ai-badge" title="Generated by AI. Verify against contract text.">
									<svg class="ai-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l1.2 4.3L18 8l-4.8 1.7L12 14l-1.2-4.3L6 8l4.8-1.7L12 2z"/></svg>
									AI
								</span>
							</div>

							{#if isObligationsLoading}
								<div class="clauses-loading">
									<span class="spinner spinner-md"></span>
									<p>Loading obligations...</p>
								</div>
							{:else if obligationsGenerated && obligations && obligations.length === 0}
								<div class="empty-tab-state">
									<p class="text-tertiary">No actionable obligations were extracted.</p>
								</div>
							{:else if obligationsGenerated && obligations}
								<div class="obligations-list">
									{#each obligations as o (o.title + o.category + o.due_trigger)}
										<div class="obligation-card bg-panel-glow">
											<div class="obligation-head flex-between">
												<span class="font-semibold">{o.title}</span>
												<span class="badge badge-secondary">{o.category}</span>
											</div>
											<div class="ai-chip" style="margin-top: 10px;" title="Extracted by AI">
												<svg class="ai-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 13l.8 2.8L22 17l-2.2.8L19 20l-.8-2.2L16 17l2.2-1.2L19 13z"/></svg>
												AI extracted
											</div>
											{#if o.description}<div class="text-secondary text-sm margin-top-8">{o.description}</div>{/if}
											<div class="obligation-meta margin-top-10">
												<div><span class="text-tertiary">Party:</span> {o.party_responsible || '--'}</div>
												<div><span class="text-tertiary">Trigger:</span> {o.due_trigger || '--'}</div>
											</div>
										</div>
									{/each}
								</div>
							{:else}
								<div class="empty-tab-state">
									<p class="text-tertiary">Obligations have not been generated for this contract yet.</p>
									<button class="btn btn-primary margin-top-12" onclick={generateObligations} disabled={isObligationsLoading}>
										{#if isObligationsLoading}
											<span class="spinner spinner-sm"></span> Generating...
										{:else}
											Generate Obligations
										{/if}
									</button>
								</div>
							{/if}
						</div>
					</div>
				{/if}

				<!-- SYSTEM TRACE TAB (only for PROCESSING status) -->
				{#if activeTab === 'trace' && contract.status === 'PROCESSING'}
					<div class="tab-content flex-col">
						<div class="overview-section">
							<div class="flex-between" style="align-items: flex-end;">
								<div>
									<h3 class="subsection-title" style="margin-bottom: 4px;">System Trace</h3>
									<div class="text-tertiary text-xs">API-level events and pipeline steps as they happen.</div>
								</div>
								<button class="btn btn-secondary btn-compact" onclick={fetchContractEvents} disabled={isTraceLoading}>
									Refresh
								</button>
							</div>

							{#if isTraceLoading && traceEvents.length === 0}
								<div class="clauses-loading">
									<span class="spinner spinner-md"></span>
									<p>Loading events…</p>
								</div>
							{:else if traceEvents.length === 0}
								<div class="empty-tab-state">
									<p class="text-tertiary">No events yet.</p>
								</div>
							{:else}
								<div class="events-list bg-panel-glow">
									{#each traceEvents as e (e.id)}
										<details class="event-row">
											<summary class="event-summary">
												{#if isAiEventType(e.event_type)}
													<span class="ai-chip" title="AI request/response">
														<svg class="ai-icon" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2l1.2 4.3L18 8l-4.8 1.7L12 14l-1.2-4.3L6 8l4.8-1.7L12 2z"/></svg>
														AI
													</span>
												{:else}
													<span class="badge badge-secondary">{e.event_type}</span>
												{/if}
												<span class="event-message">{e.message}</span>
												<span class="event-time text-tertiary font-mono">{timeAgo(e.created_at)}</span>
											</summary>
											{#if e.payload_json && Object.keys(e.payload_json).length}
												<pre class="event-payload">{JSON.stringify(e.payload_json, null, 2)}</pre>
											{/if}
										</details>
									{/each}
								</div>
							{/if}
						</div>
					</div>
				{/if}

				<!-- REDLINE VERIFICATION TAB -->
				{#if activeTab === 'changes' && contract.status === 'COMPLETED'}
					{@const resolutions = contract.metadata_json?.redline_resolutions || []}
					{@const resolvedCount = resolutions.filter((r: any) => r.status === 'RESOLVED').length}
					{@const partialCount = resolutions.filter((r: any) => r.status === 'PARTIALLY_RESOLVED').length}
					{@const unresolvedCount = resolutions.filter((r: any) => r.status === 'UNRESOLVED').length}
					<div class="tab-content flex-col gap-16">
						<div class="changes-subnav">
							<button type="button" class="cs-tab" class:active={changesSection === 'whatchanged'} onclick={() => changesSection = 'whatchanged'}>What changed{#if changeCount > 0} · {changeCount}{/if}</button>
							<button type="button" class="cs-tab" class:active={changesSection === 'redlines'} onclick={() => changesSection = 'redlines'}>Redline resolution{#if resolutions.length > 0} · {resolutions.length}{/if}</button>
							<button type="button" class="cs-tab" class:active={changesSection === 'trend'} onclick={() => changesSection = 'trend'}>Deviation trend</button>
						</div>

						{#if changesSection === 'whatchanged'}
							{#if versionChanges?.available}
								<div class="wc-summary">
									<span class="wc-stat wc-mod">{versionChanges.summary.modified} modified</span>
									<span class="wc-stat wc-add">{versionChanges.summary.added} added</span>
									<span class="wc-stat wc-rem">{versionChanges.summary.removed} removed</span>
									<span class="wc-stat wc-unc">{versionChanges.summary.unchanged} unchanged</span>
								</div>
								<div class="wc-list flex-col gap-12">
									{#each versionChanges.clauses.filter((c: any) => c.change_type !== 'UNCHANGED') as ch}
										<div class="wc-card bg-panel-glow wc-{ch.change_type.toLowerCase()}">
											<div class="wc-head">
												<span class="wc-kind wc-kind-{ch.change_type.toLowerCase()}">{ch.change_type}</span>
												<span class="clause-type-tag">{ch.clause_type || 'Clause'}</span>
											</div>
											{#if ch.change_type === 'MODIFIED' && ch.word_diff}
												<div class="wc-diff"><WordDiff ops={ch.word_diff} /></div>
												{@const stdLang = standardLanguageFor(ch.clause_type)}
												{#if stdLang}
													<button type="button" class="btn btn-secondary btn-compact wc-fallback-btn" onclick={() => copyStandardLanguage(stdLang)}>Use our standard language</button>
												{/if}
											{:else if ch.change_type === 'ADDED'}
												<div class="wc-body wc-added-body"><ClampText text={ch.new_text} lines={4} /></div>
											{:else if ch.change_type === 'REMOVED'}
												<div class="wc-body wc-removed-body"><ClampText text={ch.prev_text} lines={4} /></div>
											{/if}
											{@render decisionBar('chg:' + (ch.new_clause_id || ch.prev_clause_id), ch.clause_type)}
										</div>
									{:else}
										<div class="empty-state bg-panel-glow">
											<p class="text-secondary">No clause-level changes were detected versus the previous version.</p>
										</div>
									{/each}
								</div>
							{:else}
								<div class="empty-state bg-panel-glow">
									<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
									<p class="text-secondary" style="margin-top: 8px;">A clause-by-clause comparison isn't available for this version{#if versionChanges?.reason === 'parent_not_analyzed'} — the previous version predates clause analysis{/if}. See Redline resolution for the AI audit of counterparty edits.</p>
								</div>
							{/if}
						{:else if changesSection === 'redlines'}
						<div class="verification-header bg-panel-glow">
							<div class="vh-left">
								<svg class="verify-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
								<div>
									<h3 class="subsection-title margin-bottom-4" style="margin: 0;">Redline Verification Cockpit</h3>
									<p class="text-tertiary font-size-12" style="margin: 4px 0 0 0;">AI Senior Counsel audit of counterparty edits compared to v1 recommended redlines.</p>
								</div>
							</div>
							<div class="vh-right">
								<div class="vh-stats">
									<div class="vstat-badge success">
										<span class="vstat-num">{resolvedCount}</span>
										<span class="vstat-label">Resolved</span>
									</div>
									<div class="vstat-badge warning">
										<span class="vstat-num">{partialCount}</span>
										<span class="vstat-label">Partial</span>
									</div>
									<div class="vstat-badge danger">
										<span class="vstat-num">{unresolvedCount}</span>
										<span class="vstat-label">Remaining</span>
									</div>
								</div>
							</div>
						</div>

						<div class="resolutions-list flex-col gap-16">
							{#each contract.metadata_json?.redline_resolutions || [] as resolution}
								<div class="resolution-card bg-panel-glow">
									<div class="rc-header">
										<div class="rc-header-left">
											<span class="clause-type-tag">{resolution.clause_type}</span>
											<span class="risk-pill risk-{resolution.parent_risk_level?.toLowerCase()}" title="Original risk level">
												<span class="rp-label">Originally</span>
												<span class="rp-level">{(resolution.parent_risk_level || '').toLowerCase()}</span>
											</span>
										</div>
										<span class="badge {resolution.status === 'RESOLVED' ? 'badge-success' : resolution.status === 'PARTIALLY_RESOLVED' ? 'badge-warning' : 'badge-danger'}">
											{resolution.status === 'RESOLVED' ? 'Resolved' : resolution.status === 'PARTIALLY_RESOLVED' ? 'Partially Resolved' : 'Unresolved'}
										</span>
									</div>

									<div class="rc-comparison-grid">
										<div class="pane pane-original">
											<div class="pane-label">Original Text (v1)</div>
											<div class="pane-content text-strikethrough">{resolution.parent_text}</div>
											{#if resolution.parent_redline_suggestion}
												<div class="redline-rec bg-hover">
													<div class="rr-label">Our Proposed Redline:</div>
													<div class="rr-text">{resolution.parent_redline_suggestion}</div>
												</div>
											{/if}
										</div>
										<div class="pane pane-revised">
											<div class="pane-label">Revised Text (v{contract.metadata_json?.version_number || 2})</div>
											<div class="pane-content highlight-revised">{resolution.new_text}</div>
										</div>
									</div>

									<div class="rc-explanation bg-active">
										<div class="ex-header">
											<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
											<span>AI Senior Counsel Verdict:</span>
										</div>
										<p class="ex-body">{resolution.verification_details}</p>
									</div>
								</div>
							{:else}
								<div class="empty-state bg-panel-glow">
									<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
									<p class="text-secondary" style="margin-top: 8px;">No redline resolutions were processed for this revision.</p>
								</div>
							{/each}
						</div>
						{:else if changesSection === 'trend'}
							{@const versions = lineageData?.versions || []}
							<div class="verification-header bg-panel-glow">
								<div class="vh-left">
									<svg class="verify-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><polyline points="7 14 11 10 14 13 20 7"/></svg>
									<div>
										<h3 class="subsection-title" style="margin: 0;">Deviation trend</h3>
										<p class="text-tertiary font-size-12" style="margin: 4px 0 0 0;">How off-standard clauses and per-round changes evolved across the negotiation.</p>
									</div>
								</div>
							</div>
							{#if versions.length > 0}
								<div class="trend-list flex-col gap-8">
									{#each versions as v}
										<div class="trend-row bg-panel-glow" class:current={v.is_current}>
											<span class="badge {wfPhaseBadge(v.lifecycle_stage)} badge-sm">v{v.version_number}</span>
											<span class="trend-party">{v.party === 'counterparty' ? 'Counterparty' : 'Our side'} · Round {v.round}</span>
											<span class="trend-metrics">
												{#if v.off_standard != null}<span class="trend-chip trend-off">{v.off_standard} off-standard</span>{/if}
												{#if v.version_changed_clauses != null}<span class="trend-chip trend-chg">{v.version_changed_clauses} changed</span>{/if}
											</span>
											{#if !v.is_current}<button type="button" class="hist-link" onclick={() => goto('/contracts/' + v.id)}>Open</button>{/if}
										</div>
									{/each}
								</div>
							{:else}
								<div class="empty-state bg-panel-glow">
									<p class="text-secondary">Version history isn't available yet.</p>
								</div>
							{/if}
						{/if}
					</div>
				{/if}

				<!-- TEMPLATE DEVIATION TAB (first-party paper) -->
				{#if activeTab === 'deviation' && contract.status === 'COMPLETED'}
					<div class="tab-content flex-col gap-24">
						{#if deviationStatus === 'RUNNING'}
							<div class="dev-picker-card bg-panel-glow">
								<div class="flex-row gap-12">
									<span class="spinner spinner-lg"></span>
									<div>
										<h3 class="subsection-title" style="margin: 0;">Analyzing deviations from standard…</h3>
										<p class="text-tertiary font-size-12" style="margin: 4px 0 0 0;">{contract.metadata_json?.processing_step || 'Aligning clauses against the standard template…'}</p>
									</div>
								</div>
							</div>
						{:else if !deviationData}
							<div class="dev-picker-card bg-panel-glow">
								<h3 class="subsection-title" style="margin: 0 0 6px 0;">Check this contract against your standard paper</h3>
								<p class="text-tertiary font-size-12" style="margin: 0 0 14px 0;">
									Pick the approved template this contract was authored from. ContractsPulse aligns every clause,
									then flags what the counterparty <strong>modified</strong>, <strong>added</strong>, or <strong>deleted</strong> — and scores the risk of each change.
								</p>
								{#if availableTemplates.length === 0}
									<div class="empty-card">
										No ready templates yet. <a href="/templates" style="color: var(--accent-primary);">Create one in Templates</a> from your standard paper first.
									</div>
								{:else}
									<div class="dev-picker-row">
										<select class="dev-select" bind:value={selectedTemplateId} aria-label="Select standard template">
											{#each availableTemplates as t (t.id)}
												<option value={t.id}>{t.name} ({t.clause_count} clauses)</option>
											{/each}
										</select>
										<button type="button" class="btn btn-primary" disabled={deviationStarting || !selectedTemplateId} onclick={startDeviationAnalysis}>
											{#if deviationStarting}<span class="spinner spinner-sm" style="border-top-color:#fff;"></span>{:else}Analyze deviations{/if}
										</button>
									</div>
								{/if}
								{#if deviationStatus === 'FAILED'}
									<div class="text-danger font-size-12" style="margin-top: 10px;">The last deviation analysis failed — try again.</div>
								{/if}
							</div>
						{:else}
							<div class="verification-header bg-panel-glow">
								<div class="vh-left">
									<svg class="verify-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5"/><path d="M8 21H3v-5"/><path d="M21 3l-7.5 7.5"/><path d="M3 21l7.5-7.5"/></svg>
									<div>
										<h3 class="subsection-title" style="margin: 0;">Deviation vs “{deviationData.template_name}”</h3>
										<p class="text-tertiary font-size-12" style="margin: 4px 0 0 0;">
											First-party paper audit — what the counterparty changed on our standard, and the risk of each change.
										</p>
									</div>
								</div>
								<div class="vh-right">
									<div class="vh-stats">
										<div class="vstat-badge danger">
											<span class="vstat-num">{deviationData.summary?.deleted ?? 0}</span>
											<span class="vstat-label">Deleted</span>
										</div>
										<div class="vstat-badge warning">
											<span class="vstat-num">{deviationData.summary?.added ?? 0}</span>
											<span class="vstat-label">Added</span>
										</div>
										<div class="vstat-badge warning">
											<span class="vstat-num">{deviationData.summary?.off_playbook ?? 0}</span>
											<span class="vstat-label">Off-standard</span>
										</div>
										<div class="vstat-badge success">
											<span class="vstat-num">{deviationData.summary?.standard ?? 0}</span>
											<span class="vstat-label">On-standard</span>
										</div>
									</div>
								</div>
							</div>

							<div class="resolutions-list flex-col gap-16">
								{#each deviationItems as item, di (di)}
									<div class="resolution-card bg-panel-glow dev-card dev-{item.deviation_type.toLowerCase()}">
										<div class="rc-header">
											<div class="rc-header-left">
												<span class="dev-kind-tag dev-kind-{item.deviation_type.toLowerCase()}">
													{item.deviation_type === 'DELETED' ? 'DELETED FROM OUR PAPER' : item.deviation_type === 'ADDED' ? 'INSERTED BY COUNTERPARTY' : 'MODIFIED'}
												</span>
												<span class="clause-type-tag">{item.clause_type}</span>
												{#if item.alignment_score != null}
													<span class="badge badge-secondary badge-sm" title="Embedding similarity to standard">{Math.round(item.alignment_score * 100)}% similar</span>
												{/if}
											</div>
											<div class="flex-row gap-8">
												{#if item.escalate}
													<span class="badge badge-danger">Escalate to Legal</span>
												{/if}
												<span class="badge {item.risk_of_change === 'CRITICAL' || item.risk_of_change === 'HIGH' ? 'badge-danger' : item.risk_of_change === 'MEDIUM' ? 'badge-warning' : 'badge-success'}">
													{item.risk_of_change} risk of change
												</span>
											</div>
										</div>

										<div class="rc-comparison-grid">
											<div class="pane pane-original">
												<div class="pane-label">Our Standard</div>
												{#if item.template_text}
													<div class="pane-content" class:text-strikethrough={item.deviation_type === 'DELETED'}><ClampText text={item.template_text} lines={4} /></div>
												{:else}
													<div class="dev-absent">Not in our standard template</div>
												{/if}
											</div>
											<div class="pane pane-revised">
												<div class="pane-label">Counterparty Version</div>
												{#if item.deviation_type === 'DELETED'}
													<div class="dev-removed">
														<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
														REMOVED BY COUNTERPARTY
														<span class="dev-removed-sub">This protection no longer exists in the incoming contract.</span>
													</div>
												{:else}
													<div class="pane-content highlight-revised"><ClampText text={item.contract_text} lines={4} /></div>
												{/if}
											</div>
										</div>

										<div class="rc-explanation bg-active">
											<div class="ex-header">
												<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
												<span>AI Senior Counsel Verdict:</span>
												<span class="badge {item.playbook_verdict === 'STANDARD' ? 'badge-success' : 'badge-warning'} badge-sm">{item.playbook_verdict === 'STANDARD' ? 'On-standard' : 'Off-standard'}</span>
												<span class="badge badge-secondary badge-sm">{item.materiality}</span>
												{#if item.direction === 'MORE_FAVORABLE_TO_COUNTERPARTY'}
													<span class="badge badge-danger badge-sm">Favors counterparty</span>
												{:else if item.direction === 'MORE_FAVORABLE_TO_US'}
													<span class="badge badge-success badge-sm">Favors us</span>
												{/if}
											</div>
											<div class="ex-body"><ClampText text={item.rationale} lines={3} /></div>
											{#if item.absolute_risk?.risk_reasoning}
												<p class="ex-body text-tertiary" style="margin-top: 6px;">Standalone risk review: {item.absolute_risk.risk_reasoning}</p>
											{/if}
											{#if item.suggested_language_to_restore_standard}
												<div class="redline-rec bg-hover" style="margin-top: 10px;">
													<div class="rr-label">{item.deviation_type === 'DELETED' ? 'Re-insert standard language:' : 'Restore-to-standard language:'}</div>
													<div class="rr-text"><ClampText text={item.suggested_language_to_restore_standard} lines={3} /></div>
												</div>
												<button type="button" class="btn btn-secondary btn-compact" style="margin-top: 8px;" onclick={() => copyRestoreLanguage(item)}>
													Copy standard language
												</button>
											{/if}
											{@render decisionBar('dev:' + di, item.clause_type)}
										</div>
									</div>
								{:else}
									<div class="empty-state bg-panel-glow">
										<p class="text-secondary" style="margin-top: 8px;">No deviations — the contract matches your standard paper.</p>
									</div>
								{/each}

								{#if deviationMatched.length > 0}
									<div class="dev-matched-note">
										✓ {deviationMatched.length} clauses match the standard template and were not flagged.
									</div>
								{/if}
								<div class="dev-rerun-row">
									{#if availableTemplates.length > 0}
										<select class="dev-select" bind:value={selectedTemplateId} aria-label="Select standard template">
											{#each availableTemplates as t (t.id)}
												<option value={t.id}>{t.name}</option>
											{/each}
										</select>
									{/if}
									<button type="button" class="btn btn-secondary btn-compact" disabled={deviationStarting} onclick={startDeviationAnalysis}>
										Re-run analysis
									</button>
								</div>
							</div>
						{/if}
					</div>
				{/if}

				<!-- WORKFLOW TAB (lifecycle cockpit) -->
				{#if activeTab === 'workflow' && contract.status === 'COMPLETED'}
					<div class="tab-content flex-col gap-24">
						{#if workflowLoading && !workflowData}
							<div class="wf-loading"><span class="spinner spinner-md"></span><p class="text-tertiary">Loading workflow…</p></div>
						{:else if !workflowData}
							<div class="empty-state bg-panel-glow">
								<p class="text-secondary">Workflow could not be loaded for this contract.</p>
							</div>
						{:else}
							<!-- Flow diagram stepper -->
							<div class="wf-flow-card bg-panel-glow">
								<div class="wf-stepper">
									{#each WF_MAIN_PATH as stage, i}
										{@const idx = LIFECYCLE_ORDER.indexOf(stage)}
										{@const nodeState = idx < wfCurrentIdx ? 'done' : idx === wfCurrentIdx ? 'current' : 'upcoming'}
										{#if i > 0}
											<div class="wf-connector {idx <= wfCurrentIdx ? 'wf-conn-done' : 'wf-conn-todo'}"></div>
										{/if}
										<div class="wf-node wf-{nodeState}">
											<div class="wf-circle">
												{#if nodeState === 'done'}
													<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
												{:else}
													{i + 1}
												{/if}
											</div>
											<div class="wf-node-label">{wfStageLabel(stage)}</div>
											{#if wfStageOwner(stage)}
												<div class="wf-node-owner">{wfStageOwner(stage)}</div>
											{/if}
										</div>
									{/each}
								</div>
								{#if workflowData.current_stage === 'EXPIRED' || workflowData.current_stage === 'TERMINATED'}
									<div class="wf-terminal">
										<span class="badge {wfPhaseBadge(workflowData.current_stage)}">Terminal state: {wfStageLabel(workflowData.current_stage)}</span>
									</div>
								{/if}
							</div>

							<!-- Current stage banner -->
							<div class="wf-banner card">
								<div class="wf-banner-main">
									<span class="wf-banner-label">{wfStageLabel(workflowData.current_stage)}</span>
									<span class="wf-banner-sub text-tertiary">{workflowData.party} · Round {workflowData.round}</span>
								</div>
								{#if workflowData.gate_blocked || wfPending.length > 0}
									<span class="badge badge-warning">Awaiting {wfGateRole} approval</span>
								{/if}
							</div>

							<!-- Actions -->
							<div class="wf-actions">
								<h4 class="wf-subtitle">Advance workflow</h4>
								<input class="wf-note-input" type="text" placeholder="Add a note (optional)…" bind:value={transitionNote} />
								<div class="wf-action-btns">
									{#each workflowData.allowed_transitions || [] as t}
										{#if t.gated && !hasApproval(t.to_stage)}
											<button type="button" class="btn btn-secondary btn-compact" disabled={!t.can_drive} onclick={() => requestApproval(t.to_stage)}>
												Request {t.gate_role} approval
											</button>
										{:else}
											<button type="button" class="btn btn-primary btn-compact" disabled={!t.can_drive} onclick={() => doTransition(t.to_stage, transitionNote)}>
												Move to {t.label}
											</button>
										{/if}
									{/each}
									{#if !(workflowData.allowed_transitions || []).length}
										<span class="text-tertiary font-size-12">No further transitions available from this stage.</span>
									{/if}
								</div>
							</div>

							<!-- Pending approval cards -->
							{#if wfPending.length > 0}
								<div class="wf-approvals flex-col gap-16">
									<h4 class="wf-subtitle">Pending approvals</h4>
									{#each wfPending as ap (ap.id)}
										{@const canDecide = authState.user?.role === ap.assigned_role || authState.user?.role === 'org_admin'}
										<div class="resolution-card bg-panel-glow">
											<div class="rc-header">
												<div class="rc-header-left">
													<span class="clause-type-tag">{ap.artifact_type}</span>
													<span class="badge badge-purple badge-sm">{ap.assigned_role}</span>
													{#if ap.target_stage}
														<span class="badge badge-secondary badge-sm">→ {wfStageLabel(ap.target_stage)}</span>
													{/if}
												</div>
												<span class="badge badge-warning badge-sm">Pending</span>
											</div>
											{#if ap.artifact_type === 'vendor_email' && ap.artifact_ref}
												<div class="wf-email-block">
													{#if ap.artifact_ref.subject}<div class="wf-email-subject">{ap.artifact_ref.subject}</div>{/if}
													{#if ap.artifact_ref.body}<pre class="wf-email-body">{ap.artifact_ref.body}</pre>{/if}
												</div>
											{/if}
											{#if canDecide}
												<div class="wf-approval-actions">
													<button type="button" class="btn btn-primary btn-compact" onclick={() => decideApproval(ap.id, 'APPROVED')}>Approve</button>
													<button type="button" class="btn btn-secondary btn-compact" onclick={() => decideApproval(ap.id, 'CHANGES_REQUESTED')}>Request changes</button>
													<button type="button" class="btn btn-secondary btn-compact" onclick={() => decideApproval(ap.id, 'REJECTED')}>Reject</button>
												</div>
											{:else}
												<p class="wf-await-note text-tertiary">Awaiting {ap.assigned_role} approval</p>
											{/if}
										</div>
									{/each}
								</div>
							{/if}

							<!-- Transition history trace -->
							<div class="wf-trace-section">
								<h4 class="wf-subtitle">Transition history</h4>
								{#if (workflowData.transitions || []).length > 0}
									<ul class="wf-trace">
										{#each workflowData.transitions as tr}
											<li class="wf-trace-item">
												<span class="wf-trace-edge">{wfStageLabel(tr.from_stage)} → {wfStageLabel(tr.to_stage)}</span>
												<span class="wf-trace-meta text-tertiary">{tr.actor} · {timeAgo(tr.created_at)}</span>
											</li>
										{/each}
									</ul>
								{:else}
									<p class="text-tertiary font-size-12">No transitions recorded yet.</p>
								{/if}
							</div>
						{/if}
					</div>
				{/if}

				<!-- HISTORY TAB (version round-trip timeline) -->
				{#if activeTab === 'history' && contract.status === 'COMPLETED'}
					<div class="tab-content flex-col gap-16">
						<h3 class="subsection-title">Version Round-Trip</h3>
						{#if workflowLoading && !lineageData}
							<div class="wf-loading"><span class="spinner spinner-md"></span><p class="text-tertiary">Loading revision history…</p></div>
						{:else if !lineageData || (lineageData.versions || []).length <= 1}
							<div class="empty-state bg-panel-glow">
								<p class="text-secondary">No revisions yet — this is the original draft.</p>
							</div>
						{:else}
							<div class="hist-timeline">
								{#each lineageData.versions as v (v.id)}
									{@const isCp = v.party === 'counterparty'}
									<div class="hist-step" class:hist-current={v.is_current}>
										<div class="hist-icon {isCp ? 'hist-cp' : 'hist-internal'}"></div>
										<div class="hist-content">
											<div class="hist-row-top">
												<span class="badge {isCp ? 'badge-purple' : 'badge-blue'} badge-sm">{isCp ? 'Counterparty' : 'Our draft'}</span>
												<span class="hist-ver">v{v.version_number} · Round {v.round}</span>
												{#if v.is_current}<span class="badge badge-success badge-sm">Current</span>{/if}
												<span class="hist-time text-tertiary">{timeAgo(v.created_at)}</span>
											</div>
											<div class="hist-meta text-tertiary">
												{wfStageLabel(v.lifecycle_stage)}
												{#if v.changed_clauses > 0}
													· {v.changed_clauses} clauses changed / {v.resolved_clauses} resolved
												{/if}
											</div>
											<div class="hist-links">
												<button type="button" class="hist-link" onclick={() => goto('/contracts/' + v.id)}>Open</button>
												{#if v.changed_clauses > 0}
													<button type="button" class="hist-link" onclick={() => goto('/contracts/' + v.id + '?tab=changes')}>View changes</button>
												{/if}
											</div>
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</div>
			{#if contract.status === 'COMPLETED'}
				<div class="risk-legend" class:open={riskLegendOpen}>
					<button type="button" class="risk-legend-toggle" onclick={() => (riskLegendOpen = !riskLegendOpen)} aria-expanded={riskLegendOpen}>
						<span class="rl-chips" aria-hidden="true">
							<span class="rl-chip rl-low">Low</span>
							<span class="rl-chip rl-medium">Medium</span>
							<span class="rl-chip rl-high">High</span>
							<span class="rl-chip rl-critical">Critical</span>
						</span>
						<span class="rl-title">How risk is scored</span>
						<svg class="rl-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
					</button>
					{#if riskLegendOpen}
						<div class="risk-legend-body">
							<p>Each clause is reviewed by an AI legal-counsel model that scores <strong>7 risk dimensions</strong> — termination, payment, liability, indemnification, IP, confidentiality and dispute resolution — from 0 to 1, plus a <strong>confidence</strong> score.</p>
							<p>The <strong>Low / Medium / High / Critical</strong> level is the model's direct legal judgment, <strong>not</strong> a numeric threshold. The <strong>composite score</strong> shown in a clause's technical details is simply the single highest dimension, provided for transparency.</p>
							<p>A deviation's <strong>“risk of change”</strong> measures the <em>added</em> risk a counterparty's edit introduces versus our standard language — not the clause's absolute risk.</p>
							<p class="rl-fine">If the AI model is unavailable, a keyword heuristic estimates the level (flagged as a fallback); broad IP-assignment grants are always raised to at least High.</p>
						</div>
					{/if}
				</div>
			{/if}
		</div>
	</div>
{/if}

{#if emailModalOpen}
	<div class="modal-root">
		<button type="button" class="modal-backdrop" aria-label="Close" onclick={() => (emailModalOpen = false)}></button>
		<div class="modal-content modal-content-wide email-composer" role="dialog" aria-modal="true">
			<!-- macOS Window Controls Decoration -->
			<div class="email-mac-buttons">
				<button class="mac-dot mac-close" onclick={() => (emailModalOpen = false)} aria-label="Close"></button>
				<div class="mac-dot mac-minimize"></div>
				<div class="mac-dot mac-maximize"></div>
			</div>
			
			<div class="modal-header email-composer-header">
				<div class="modal-icon warning" style="background: rgba(var(--ai-rgb), 0.12); color: var(--ai); border: none;">
					<svg class="ai-icon animate-pulse" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 6l1.2 4.3L18 12l-4.8 1.7L12 18l-1.2-4.3L6 12l4.8-1.7L12 6z"/></svg>
				</div>
				<div>
					<h3 class="font-semibold" style="margin: 0; font-size: 16px;">AI Negotiation Counsel</h3>
					<div class="text-tertiary" style="font-size: 11px; margin-top: 2px;">Drafts polished supplier communications explaining recommended contract changes.</div>
				</div>
			</div>
			
			<div class="modal-body email-composer-body">
				<!-- Settings controls -->
				<div class="email-composer-controls">
					<div class="control-group">
						<label for="email-tone-select">Tone</label>
						<div class="custom-select-wrapper">
							<select id="email-tone-select" class="custom-select" bind:value={emailTone}>
								<option value="professional">Professional</option>
								<option value="friendly">Friendly</option>
								<option value="firm">Firm</option>
							</select>
						</div>
					</div>
					<div class="control-group">
						<label for="email-include-select">Include</label>
						<div class="custom-select-wrapper">
							<select id="email-include-select" class="custom-select" bind:value={emailInclude}>
								<option value="unresolved">Unresolved only</option>
								<option value="all">All items</option>
							</select>
						</div>
					</div>
					
					<div class="control-spacer"></div>
					
					<button class="btn btn-secondary btn-compact btn-ai btn-regenerate" onclick={generateVendorEmail} disabled={isEmailLoading}>
						<svg class="icon-spin {isEmailLoading ? 'spin' : ''}" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
						Regenerate
					</button>
				</div>

				{#if !emailDraft || isEmailLoading}
					<div class="email-loading-pane bg-panel-glow">
						<div class="email-loading-status">
							<span class="spinner spinner-sm"></span>
							<span class="email-loading-text">AI Counsel is drafting your vendor email…</span>
						</div>
						<div class="loading-envelope-headers">
							<div class="loading-row">
								<div class="loading-label skeleton"></div>
								<div class="loading-value skeleton"></div>
							</div>
							<div class="loading-row">
								<div class="loading-label skeleton"></div>
								<div class="loading-value skeleton"></div>
							</div>
							<div class="loading-row">
								<div class="loading-label skeleton"></div>
								<div class="loading-value skeleton"></div>
							</div>
						</div>
						<div class="loading-body">
							<div class="loading-line skeleton width-70"></div>
							<div class="loading-line skeleton width-90"></div>
							<div class="loading-line skeleton width-80"></div>
							<div class="loading-line skeleton width-50"></div>
						</div>
					</div>
				{:else}
					<!-- CRM Email Workspace -->
					<div class="email-workspace bg-panel-glow">
						<!-- Mail Header Info -->
						<div class="email-envelope-headers">
							<div class="envelope-row">
								<span class="env-label">From:</span>
								<div class="env-value">
									<span class="env-tag env-tag-ai">AI Procurement Agent</span>
									<span class="env-address">&lt;assistant@contractspulse.ai&gt;</span>
								</div>
							</div>
							<div class="envelope-row">
								<span class="env-label">To:</span>
								<div class="env-value">
									<span class="env-tag">{contract?.metadata_json?.company || 'Vendor Representative'}</span>
									{#if contract?.metadata_json?.company}
										<span class="env-address">&lt;contracts@{(contract?.metadata_json?.company || '').toLowerCase().replace(/[^a-z0-9]/g, '') || 'vendor'}.com&gt;</span>
									{/if}
								</div>
							</div>
							<div class="envelope-row">
								<span class="env-label">Subject:</span>
								<div class="env-value subject-value font-semibold">{emailDraft.subject}</div>
							</div>
						</div>
						
						<!-- Mail Message Body Pane -->
						<div class="email-body-pane">
							<pre class="email-body-text">{emailDraft.body}</pre>
						</div>
					</div>
				{/if}
			</div>
			
			<div class="modal-footer email-composer-footer">
				<button class="btn btn-secondary" onclick={() => (emailModalOpen = false)}>Close</button>
				<button class="btn btn-primary btn-copy-draft {isCopied ? 'copied' : ''}" onclick={copyEmailDraft} disabled={!emailDraft || isEmailLoading}>
					<div class="copy-state-wrapper">
						<span class="copy-state copy-state-default {isCopied ? 'state-hidden' : ''}">
							<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
								<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
							</svg>
							<span>Copy Email Draft</span>
						</span>
						<span class="copy-state copy-state-success {isCopied ? 'state-visible' : ''}">
							<svg class="copy-icon-success" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
							<span>Copied!</span>
						</span>
					</div>
				</button>
			</div>
		</div>
	</div>
{/if}

<!-- Delete Confirmation Modal -->
{#if deleteModalOpen}
	<div class="modal-root">
		<button type="button" class="modal-backdrop" aria-label="Close" onclick={() => deleteModalOpen = false}></button>
		<div class="modal-content" role="dialog" aria-modal="true">
			<div class="modal-header">
				<div class="modal-icon warning">
					<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>
				</div>
				<h3>Delete Contract</h3>
			</div>
			<div class="modal-body">
				<p>Are you sure you want to completely delete this contract? This will remove all parsed text segments, AI evaluations, and historical traces. This action is final.</p>
			</div>
			<div class="modal-footer flex-end gap-12">
				<button class="btn btn-secondary" onclick={() => deleteModalOpen = false}>Cancel</button>
				<button class="btn btn-danger" onclick={handleDelete}>Delete Permanently</button>
			</div>
		</div>
	</div>
{/if}

<!-- Upload Revision Modal -->
{#if uploadRevisionModalOpen}
	<div class="modal-root">
		<button type="button" class="modal-backdrop" aria-label="Close" onclick={() => { uploadRevisionModalOpen = false; revisionFile = null; }}></button>
		<div class="modal-content" role="dialog" aria-modal="true">
			<div class="modal-header">
				<div class="modal-icon">
					<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
				</div>
				<h3>Upload Revised Version</h3>
			</div>
			<div class="modal-body">
				<div class="rev-party">
					<div class="rev-party-label">Who produced this version?</div>
					<div class="rev-party-opts">
						<button type="button" class="rev-party-opt" class:active={revisionParty === 'internal'} onclick={() => (revisionParty = 'internal')}>
							<span class="badge badge-blue badge-sm">Our revision</span>
							<span class="rev-party-hint">We edited this draft</span>
						</button>
						<button type="button" class="rev-party-opt" class:active={revisionParty === 'counterparty'} onclick={() => (revisionParty = 'counterparty')}>
							<span class="badge badge-purple badge-sm">Counterparty</span>
							<span class="rev-party-hint">Supplier's returned redlines</span>
						</button>
					</div>
				</div>

				<div class="tabs-nav-modal" role="tablist" aria-label="Upload Method">
					<button class="tab-nav-modal-btn" role="tab" aria-selected={revisionInputType === 'file'} class:active={revisionInputType === 'file'} onclick={() => { revisionInputType = 'file'; revisionFile = null; }}>
						Upload Document
					</button>
					<button class="tab-nav-modal-btn" role="tab" aria-selected={revisionInputType === 'text'} class:active={revisionInputType === 'text'} onclick={() => { revisionInputType = 'text'; revisionText = ''; }}>
						Paste contract text
					</button>
				</div>

				{#if revisionInputType === 'file'}
					<div class="upload-dropzone" role="button" tabindex="0" onclick={() => revisionFileInput?.click()} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); revisionFileInput?.click(); } }}>
						<input type="file" bind:this={revisionFileInput} onchange={(e) => {
							const target = e.target as HTMLInputElement;
							if (target.files && target.files.length > 0) {
								revisionFile = target.files[0];
							}
						}} style="display: none;" accept=".pdf,.docx,.txt" />
						<div class="dropzone-label">
							<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
							<span>Click to select contract document</span>
							<span class="dropzone-subtitle">PDF, DOCX, or TXT up to 10MB</span>
						</div>
					</div>
					{#if revisionFile}
						<div class="selected-file-banner">
							<div class="flex-row gap-8 align-center">
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
								<span>{revisionFile.name} ({(revisionFile.size / 1024).toFixed(0)} KB)</span>
							</div>
							<button class="btn-remove-file" onclick={() => revisionFile = null} aria-label="Remove selected file" title="Remove file">
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
							</button>
						</div>
					{/if}
				{:else}
					<div class="textarea-container flex-col gap-6">
						<label for="revision-paste-text" class="text-secondary font-size-12 font-weight-500">Paste contract text</label>
						<textarea id="revision-paste-text" bind:value={revisionText} placeholder="Paste the updated agreement text here..." rows="8" class="input-field" style="resize: vertical; font-family: monospace; font-size: 12px;"></textarea>
					</div>
				{/if}
			</div>
			<div class="modal-footer flex-end gap-12">
				<button class="btn btn-secondary" onclick={() => { uploadRevisionModalOpen = false; revisionFile = null; revisionText = ''; }} disabled={isRevisionUploading}>Cancel</button>
				<button class="btn btn-primary" onclick={handleRevisionUpload} disabled={isRevisionUploading || (revisionInputType === 'file' && !revisionFile) || (revisionInputType === 'text' && !revisionText.trim())}>
					{#if isRevisionUploading}
						<span class="spinner spinner-sm"></span> Processing...
					{:else}
						Start Version Analysis
					{/if}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	/* Loading Screen */
	.cockpit-loading {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		flex: 1;
		gap: 16px;
		background: var(--bg-app);
		color: var(--text-secondary);
		height: 100vh;
	}

	/* Compact Header */
	.cockpit-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 16px 24px;
		background: var(--bg-sidebar);
		border-bottom: 1px solid var(--border-subtle);
		height: 68px;
		flex-shrink: 0;
	}

	.cockpit-header .breadcrumbs {
		margin-bottom: 0;
	}

	.crumb-link {
		color: var(--text-tertiary);
		text-decoration: none;
		transition: color 150ms ease;
	}

	.crumb-link:hover {
		color: var(--text-secondary);
	}

	.cockpit-actions {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.btn-compact {
		height: 28px;
		padding: 0 10px;
		font-size: 12px;
	}

	.btn-danger-action {
		background: rgba(248, 81, 73, 0.08);
		border-color: rgba(248, 81, 73, 0.2);
		color: var(--color-high);
	}

	.btn-danger-action:hover {
		background: rgba(248, 81, 73, 0.15);
		border-color: rgba(248, 81, 73, 0.35);
	}

	/* Main Split Screen Wrapper */
	.cockpit-wrapper {
		display: grid;
		/* Left column min is 0 so the document panel shrinks (its body scrolls) instead of
		   pushing the analysis panel — and its tab row — off-screen when the docked Assist
		   widget reserves 400px on the right. */
		grid-template-columns: minmax(0, 58%) minmax(360px, 42%);
		height: calc(100vh - 68px);
		background: var(--bg-app);
		overflow: hidden;
	}

	@media (max-width: 1024px) {
		.cockpit-wrapper {
			grid-template-columns: 1fr;
			height: auto;
			overflow-y: auto;
		}
		
		.document-panel, .analysis-panel {
			height: 600px !important;
		}
	}

	/* Left Panel: OCR Document */
	.document-panel {
		display: flex;
		flex-direction: column;
		border-right: 1px solid var(--border-subtle);
		height: 100%;
		overflow: hidden;
		background: var(--bg-app);
	}

	.pane-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 16px 20px;
		background: var(--bg-sidebar);
		border-bottom: 1px solid var(--border-subtle);
		height: 48px;
		flex-shrink: 0;
	}

	.pane-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text-primary);
		gap: 8px;
	}

	.document-meta-info {
		font-size: 12px;
	}

	/* Original PDF vs annotated text toggle */
	.pane-header-right {
		display: flex;
		align-items: center;
		gap: 10px;
		min-width: 0;
	}
	.doc-toggle {
		display: flex;
		border: 1px solid var(--border-subtle);
		border-radius: var(--radius-pill, 980px);
		overflow: hidden;
		flex-shrink: 0;
	}
	.doc-toggle-btn {
		border: none;
		background: transparent;
		color: var(--text-secondary);
		font-size: 11px;
		font-weight: 600;
		padding: 3px 12px;
		cursor: pointer;
		transition: color 120ms var(--ease-out), background 120ms var(--ease-out);
	}
	.doc-toggle-btn:hover { color: var(--text-primary); }
	.doc-toggle-btn.active { background: var(--brand-primary, #0071e3); color: #fff; }
	.pdf-frame {
		width: 100%;
		height: 100%;
		min-height: 600px;
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		background: #fff;
	}

	.document-body {
		position: relative;
		flex: 1;
		overflow-y: auto;
		padding: 24px;
		display: flex;
		justify-content: center;
		background: var(--bg-app);
	}

	.clause-minimap {
		position: absolute;
		right: 10px;
		top: 12px;
		bottom: 12px;
		width: 12px;
		border-radius: 999px;
		background: rgba(0, 0, 0, 0.03);
		border: 1px solid var(--border-subtle);
		overflow: hidden;
		z-index: 5;
	}
	:global([data-theme="dark"]) .clause-minimap {
		background: rgba(255, 255, 255, 0.04);
	}
	.minimap-dot {
		position: absolute;
		left: 50%;
		transform: translate(-50%, -50%);
		width: 8px;
		height: 8px;
		border-radius: 999px;
		border: 1px solid rgba(255, 255, 255, 0.65);
		opacity: 0.85;
		cursor: pointer;
		padding: 0;
	}
	.minimap-dot:hover {
		opacity: 1;
		transform: translate(-50%, -50%) scale(1.15);
	}
	.minimap-dot.active {
		box-shadow: var(--ring-strong);
		opacity: 1;
	}
	.minimap-dot.risk-low { background: var(--color-low); }
	.minimap-dot.risk-medium { background: var(--color-medium); }
	.minimap-dot.risk-high { background: var(--color-high); }
	.minimap-dot.risk-critical { background: var(--color-critical); }

	.document-paper {
		width: 100%;
		max-width: 800px;
		background: var(--bg-panel);
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		padding: 32px;
		font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
		font-size: 13px;
		line-height: 1.7;
		color: var(--text-primary);
		white-space: pre-wrap;
		overflow-wrap: break-word;
		box-shadow: var(--shadow-premium);
		height: fit-content;
		min-height: 100%;
	}

	.document-placeholder {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 200px;
		color: var(--text-tertiary);
		gap: 12px;
		width: 100%;
	}

	/* Right Panel: AI Analysis Workspace */
	.analysis-panel {
		display: flex;
		flex-direction: column;
		height: 100%;
		overflow: hidden;
		background: var(--bg-app);
	}

	.analysis-tabs {
		display: flex;
		padding: 8px 16px;
		background: var(--bg-sidebar);
		border-bottom: 1px solid var(--border-subtle);
		gap: 6px;
		height: 48px;
		flex-shrink: 0;
		align-items: center;
		/* Tabs scroll horizontally rather than clip — so Workflow/History/Trace stay
		   reachable even when the docked Assist widget narrows the panel. Scrollbar hidden
		   (matches .pill-nav); the right-edge fade hints there's more to scroll. */
		flex-wrap: nowrap;
		overflow-x: auto;
		overflow-y: hidden;
		scrollbar-width: none;
		-webkit-overflow-scrolling: touch;
		-webkit-mask-image: linear-gradient(to right, #000 calc(100% - 22px), transparent 100%);
		mask-image: linear-gradient(to right, #000 calc(100% - 22px), transparent 100%);
	}
	.analysis-tabs::-webkit-scrollbar {
		display: none;
	}

	.tab-btn {
		background: transparent;
		border: 1px solid transparent;
		color: var(--text-secondary);
		padding: 4px 10px;
		border-radius: 4px;
		font-size: 12px;
		font-weight: 500;
		cursor: pointer;
		transition: color 120ms var(--ease-out), background 120ms var(--ease-out), border-color 120ms var(--ease-out), transform 120ms var(--ease-out);
		display: flex;
		align-items: center;
		gap: 6px;
		user-select: none;
		flex-shrink: 0;
		white-space: nowrap;
	}

	.tab-btn:hover {
		color: var(--text-primary);
		background: var(--bg-hover);
	}

	.tab-btn.active {
		color: var(--text-primary);
		background: var(--bg-active);
		border-color: var(--border-strong);
	}

	.tab-btn:active {
		transform: scale(0.96);
	}

	.analysis-viewport {
		flex: 1;
		overflow-y: auto;
		background: var(--bg-app);
	}

	/* Per-deviation reviewer decisions (Slice 5) */
	.decision-bar {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 6px;
		margin-top: 12px;
		padding-top: 10px;
		border-top: 1px dashed var(--border-subtle);
	}
	.decision-label { font-size: 11px; font-weight: 600; color: var(--text-tertiary); margin-right: 2px; }
	.decision-btn {
		font-size: 11px;
		font-weight: 600;
		padding: 3px 10px;
		border-radius: var(--radius-pill, 980px);
		border: 1px solid var(--border-subtle);
		background: transparent;
		color: var(--text-secondary);
		cursor: pointer;
		transition: color 120ms var(--ease-out), background 120ms var(--ease-out), border-color 120ms var(--ease-out);
	}
	.decision-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
	.decision-btn:disabled { opacity: 0.5; cursor: default; }
	.decision-btn.decision-accept.active { background: var(--color-low, #22c55e); border-color: transparent; color: #fff; }
	.decision-btn.decision-fallback.active { background: var(--brand-primary, #0071e3); border-color: transparent; color: #fff; }
	.decision-btn.decision-reject.active { background: var(--color-high, #ef4444); border-color: transparent; color: #fff; }
	.decision-meta { font-size: 11px; color: var(--text-tertiary); margin-left: auto; }

	/* Unified Changes tab (Slice 3) */
	.changes-subnav {
		display: flex;
		gap: 6px;
		position: sticky;
		top: 0;
		z-index: 2;
		padding-bottom: 8px;
		background: var(--bg-app);
	}
	.cs-tab {
		background: transparent;
		border: 1px solid var(--border-subtle);
		color: var(--text-secondary);
		padding: 5px 12px;
		border-radius: var(--radius-pill, 980px);
		font-size: 12px;
		font-weight: 600;
		cursor: pointer;
		transition: color 120ms var(--ease-out), background 120ms var(--ease-out), border-color 120ms var(--ease-out);
		white-space: nowrap;
	}
	.cs-tab:hover { color: var(--text-primary); background: var(--bg-hover); }
	.cs-tab.active { color: #fff; background: var(--brand-primary, #0071e3); border-color: transparent; }
	.wc-summary {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		font-size: 11px;
		font-weight: 600;
	}
	.wc-stat { padding: 2px 9px; border-radius: 999px; }
	.wc-stat.wc-mod { color: var(--color-medium); background: color-mix(in srgb, var(--color-medium) 14%, transparent); }
	.wc-stat.wc-add { color: var(--color-low-text, #15803d); background: color-mix(in srgb, var(--color-low, #22c55e) 16%, transparent); }
	.wc-stat.wc-rem { color: var(--color-high-text, #b91c1c); background: color-mix(in srgb, var(--color-high, #ef4444) 14%, transparent); }
	.wc-stat.wc-unc { color: var(--text-tertiary); background: var(--bg-hover); }
	.wc-card { padding: 12px 14px; border-radius: var(--radius-md); border: 1px solid var(--border-subtle); }
	.wc-card.wc-modified { border-left: 3px solid var(--color-medium); }
	.wc-card.wc-added { border-left: 3px solid var(--color-low, #22c55e); }
	.wc-card.wc-removed { border-left: 3px solid var(--color-high, #ef4444); }
	.wc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
	.wc-kind { font-size: 10px; font-weight: 700; letter-spacing: 0.04em; padding: 1px 7px; border-radius: 4px; color: #fff; }
	.wc-kind-modified { background: var(--color-medium); }
	.wc-kind-added { background: var(--color-low, #22c55e); }
	.wc-kind-removed { background: var(--color-high, #ef4444); }
	.wc-body { font-size: 12px; line-height: 1.6; color: var(--text-secondary); }
	.wc-removed-body { text-decoration: line-through; opacity: 0.75; }
	.wc-fallback-btn { margin-top: 8px; }
	.trend-list { }
	.trend-row {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 8px 12px;
		border-radius: var(--radius-md);
		border: 1px solid var(--border-subtle);
	}
	.trend-row.current { border-color: var(--brand-primary, #0071e3); }
	.trend-party { font-size: 12px; color: var(--text-secondary); flex-shrink: 0; }
	.trend-metrics { display: flex; gap: 6px; margin-left: auto; }
	.trend-chip { font-size: 10px; font-weight: 600; padding: 1px 8px; border-radius: 999px; }
	.trend-chip.trend-off { color: var(--color-high-text, #b91c1c); background: color-mix(in srgb, var(--color-high, #ef4444) 12%, transparent); }
	.trend-chip.trend-chg { color: var(--text-secondary); background: var(--bg-hover); }

	/* Lifecycle next-action banner (E5) */
	.next-action {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 14px;
		border-radius: var(--radius-md);
		border: 1px solid var(--border-subtle);
		background: var(--bg-panel-glow, var(--bg-sidebar));
		margin-bottom: 4px;
	}
	.next-action .na-icon {
		flex-shrink: 0;
		width: 30px;
		height: 30px;
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: 999px;
		color: #fff;
		background: var(--color-medium);
	}
	.next-action-info .na-icon { background: var(--brand-primary, #0071e3); }
	.next-action-warn .na-icon { background: var(--color-high); }
	.next-action-wait .na-icon { background: var(--color-medium); }
	.next-action-ok .na-icon { background: var(--color-low); }
	.next-action .na-body { flex: 1; min-width: 0; }
	.next-action .na-title { font-size: 13px; font-weight: 600; color: var(--text-primary); }
	.next-action .na-detail { font-weight: 400; color: var(--text-secondary); }
	.next-action .na-cta { flex-shrink: 0; }
	.next-action-info { border-left: 3px solid var(--brand-primary, #0071e3); }
	.next-action-warn { border-left: 3px solid var(--color-high); }
	.next-action-wait { border-left: 3px solid var(--color-medium); }
	.next-action-ok { border-left: 3px solid var(--color-low); }

	/* Risk-methodology footer legend (E4) — pinned below the scroll region */
	.risk-legend {
		flex-shrink: 0;
		border-top: 1px solid var(--border-subtle);
		background: var(--bg-sidebar);
	}
	.risk-legend-toggle {
		width: 100%;
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 8px 16px;
		background: none;
		border: none;
		cursor: pointer;
		color: var(--text-secondary);
	}
	.risk-legend-toggle:hover { background: var(--bg-hover); }
	.rl-chips { display: flex; gap: 4px; }
	.rl-chip {
		font-size: 10px;
		font-weight: 600;
		padding: 1px 7px;
		border-radius: 999px;
		color: #fff;
		line-height: 1.6;
	}
	.rl-chip.rl-low { background: var(--color-low); }
	.rl-chip.rl-medium { background: var(--color-medium); }
	.rl-chip.rl-high { background: var(--color-high); }
	.rl-chip.rl-critical { background: var(--color-critical); }
	.rl-title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
	.rl-caret { margin-left: auto; transition: transform 180ms var(--ease-out); }
	.risk-legend.open .rl-caret { transform: rotate(180deg); }
	.risk-legend-body {
		padding: 4px 16px 14px;
		max-height: 40vh;
		overflow-y: auto;
	}
	.risk-legend-body p {
		font-size: 12px;
		line-height: 1.55;
		color: var(--text-secondary);
		margin: 6px 0;
	}
	.risk-legend-body strong { color: var(--text-primary); font-weight: 600; }
	.risk-legend-body .rl-fine { font-size: 11px; color: var(--text-tertiary); }
	@media (prefers-reduced-motion: reduce) {
		.rl-caret { transition: none; }
	}

	.tab-content {
		padding: 24px;
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.flex-col {
		display: flex;
		flex-direction: column;
	}

	.gap-6 { gap: 6px; }
	.gap-8 { gap: 8px; }

	.margin-top-16 { margin-top: 16px; }
	.margin-top-24 { margin-top: 24px; }
	.font-semibold { font-weight: 600; }
	.font-medium { font-weight: 500; }
	.font-bold { font-weight: 700; }
	.font-mono { font-family: monospace; }
	.truncate { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

	/* Overview tab styling */
	.subsection-title {
		font-size: 13px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.5px;
		color: var(--text-secondary);
		margin-bottom: 12px;
	}

	.overview-section {
		display: flex;
		flex-direction: column;
	}

	.metadata-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 12px;
	}

	.meta-card {
		padding: 14px;
		border-radius: 6px;
		border: 1px solid var(--border-subtle);
		display: flex;
		flex-direction: column;
		gap: 6px;
		min-width: 0;
	}

	.bg-panel-glow {
		background: var(--bg-panel);
		transition: border-color 150ms ease;
	}
	.bg-panel-glow:hover {
		border-color: var(--border-strong);
	}

	.mc-label {
		font-size: 11px;
		color: var(--text-tertiary);
		text-transform: uppercase;
		font-weight: 500;
	}

	.mc-value {
		font-size: 13px;
		color: var(--text-primary);
		font-weight: 500;
	}

	.spinner-badge {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 2px 8px;
	}

	/* Risk matrix styling */
	.risk-matrix {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		gap: 8px;
	}

	.matrix-item {
		padding: 12px 8px;
		border-radius: 6px;
		border: 1px solid var(--border-subtle);
		text-align: center;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.matrix-count {
		font-size: 20px;
		font-weight: 700;
	}

	.matrix-label {
		font-size: 11px;
		color: var(--text-secondary);
		font-weight: 500;
	}

	/* Risk glowing ambient tokens */
	.bg-critical-glow { background: var(--glow-critical); border-color: var(--glow-critical-border); }
	.bg-high-glow { background: var(--glow-high); border-color: var(--glow-high-border); }
	.bg-medium-glow { background: var(--glow-medium); border-color: var(--glow-medium-border); }
	.bg-low-glow { background: var(--glow-low); border-color: var(--glow-low-border); }

	.text-critical { color: var(--color-critical); }
	.text-high { color: var(--color-high); }
	.text-medium { color: var(--color-medium); }
	.text-low { color: var(--color-low); }

	.routing-card {
		padding: 16px;
		border-radius: 8px;
		border: 1px solid var(--border-subtle);
		font-size: 13px;
		line-height: 1.6;
		color: var(--text-primary);
	}

	/* Top Risks tab styling */
	.risks-list {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.risk-glow-card {
		padding: 16px;
		border-radius: 8px;
		border: 1px solid var(--border-subtle);
		display: flex;
		flex-direction: column;
		gap: 12px;
		transition: border-color 200ms var(--ease-out), box-shadow 200ms var(--ease-out), background 200ms var(--ease-out);
	}

	.risk-glow-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.risk-glow-type {
		font-size: 14px;
		font-weight: 600;
		color: var(--text-primary);
	}

	.auto-renewal-badge {
		display: inline-flex;
		align-items: center;
		gap: 8px;
		padding: 6px 10px;
		border-radius: 6px;
		font-size: 12px;
		background: rgba(210, 153, 34, 0.08);
		border: 1px solid rgba(210, 153, 34, 0.2);
		color: var(--color-medium);
	}

	.risk-glow-reasoning {
		font-size: 13px;
		line-height: 1.5;
		color: var(--text-primary);
	}

	.risk-glow-excerpt {
		font-family: monospace;
		font-size: 12px;
		background: var(--bg-hover);
		padding: 10px 12px;
		border-radius: 6px;
		border: 1px solid var(--border-subtle);
		color: var(--text-secondary);
		line-height: 1.5;
	}

	/* Ambient card glows by risk level */
	.risk-critical {
		background: var(--glow-critical);
		border-color: var(--glow-critical-border);
	}
	.risk-critical:hover {
		border-color: rgba(255, 59, 48, 0.4);
		box-shadow: 0 4px 20px rgba(255, 59, 48, 0.06);
	}

	.risk-high {
		background: var(--glow-high);
		border-color: var(--glow-high-border);
	}
	.risk-high:hover {
		border-color: rgba(248, 81, 73, 0.35);
		box-shadow: 0 4px 20px rgba(248, 81, 73, 0.05);
	}

	.risk-medium {
		background: var(--glow-medium);
		border-color: var(--glow-medium-border);
	}
	.risk-medium:hover {
		border-color: rgba(210, 153, 34, 0.3);
		box-shadow: 0 4px 16px rgba(210, 153, 34, 0.04);
	}

	.risk-low {
		background: var(--glow-low);
		border-color: var(--glow-low-border);
	}
	.risk-low:hover {
		border-color: rgba(63, 185, 80, 0.3);
		box-shadow: 0 4px 16px rgba(63, 185, 80, 0.03);
	}

	/* Clauses Filters and Lists */
	.clauses-filters {
		display: flex;
		flex-direction: column;
		gap: 12px;
		padding: 14px;
		border-radius: 8px;
		border: 1px solid var(--border-subtle);
	}

	.search-input-wrapper {
		position: relative;
		display: flex;
		align-items: center;
		width: 100%;
	}

	.search-icon {
		position: absolute;
		left: 12px;
		color: var(--text-tertiary);
		pointer-events: none;
	}

	.clause-search-bar {
		width: 100%;
		padding: 8px 12px 8px 34px;
		background: var(--bg-hover);
		border: 1px solid var(--border-subtle);
		border-radius: 6px;
		color: var(--text-primary);
		font-size: 13px;
		outline: none;
		transition: border-color 150ms ease;
	}

	.clause-search-bar:focus {
		border-color: var(--border-strong);
	}

	.filter-pills {
		overflow-x: auto;
		padding-bottom: 2px;
	}

	.filter-pill {
		background: transparent;
		border: 1px solid var(--border-subtle);
		color: var(--text-secondary);
		padding: 3px 10px;
		font-size: 11px;
		font-weight: 500;
		border-radius: 6px;
		cursor: pointer;
		transition: color 120ms ease, background 120ms ease, border-color 120ms ease;
		user-select: none;
	}

	.filter-pill:hover {
		color: var(--text-primary);
		border-color: var(--border-strong);
	}

	.filter-pill.active {
		color: var(--text-on-accent);
		border-color: var(--border-strong);
		background: var(--bg-hover);
	}

	.filter-pill-critical.active {
		background: rgba(255, 59, 48, 0.15);
		color: var(--color-critical);
		border-color: rgba(255, 59, 48, 0.4);
	}

	.filter-pill-high.active {
		background: rgba(248, 81, 73, 0.15);
		color: var(--color-high);
		border-color: rgba(248, 81, 73, 0.4);
	}

	.filter-pill-medium.active {
		background: rgba(210, 153, 34, 0.12);
		color: var(--color-medium);
		border-color: rgba(210, 153, 34, 0.35);
	}

	.filter-pill-low.active {
		background: rgba(63, 185, 80, 0.12);
		color: var(--color-low);
		border-color: rgba(63, 185, 80, 0.35);
	}

	/* Interactive Clause Card */
	.clauses-list {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.clause-interactive-card {
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		padding: 14px;
		display: flex;
		flex-direction: column;
		gap: 10px;
		cursor: pointer;
		transition: border-color 200ms var(--ease-out), background 200ms var(--ease-out), box-shadow 200ms var(--ease-out), transform 200ms var(--ease-out);
		user-select: none;
	}

	.clause-interactive-card:active {
		transform: scale(0.99);
	}

	.clause-interactive-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
	}

	.chevron-icon {
		color: var(--text-tertiary);
		transition: transform 180ms var(--ease-out);
	}

	.chevron-icon.rotated {
		transform: rotate(90deg);
		color: var(--text-secondary);
	}

	.clause-interactive-excerpt {
		font-size: 12.5px;
		color: var(--text-secondary);
		line-height: 1.6;
		padding-left: 24px;
	}

	.clause-interactive-card.expanded {
		cursor: default;
	}
	.clause-interactive-card.expanded:active {
		transform: none;
	}

	.clause-expanded-section {
		padding-left: 24px;
		display: flex;
		flex-direction: column;
		gap: 12px;
		margin-top: 4px;
		animation: slideDown 220ms var(--ease-out) forwards;
	}

	@keyframes slideDown {
		from { opacity: 0; transform: translateY(-4px); }
		to { opacity: 1; transform: translateY(0); }
	}

	.clause-reasoning {
		font-size: 13px;
		line-height: 1.5;
		color: var(--text-primary);
	}

	.clause-redline {
		border: 1px solid var(--border-subtle);
		border-radius: 6px;
		background: var(--bg-hover);
		overflow: hidden;
	}

	.clause-redline-head {
		padding: 8px 12px;
		border-bottom: 1px solid var(--border-subtle);
		background: var(--bg-active);
		font-size: 12px;
		color: var(--text-secondary);
	}

	.clause-redline-block {
		padding: 12px;
		font-family: 'JetBrains Mono', 'SF Mono', Consolas, monospace;
		font-size: 12px;
		line-height: 1.6;
		color: var(--color-low);
		white-space: pre-wrap;
		overflow-wrap: break-word;
		margin: 0;
	}

	.clause-tech {
		border: 1px solid var(--border-subtle);
		border-radius: 6px;
		padding: 8px 12px;
		background: var(--bg-hover);
	}

	.clause-tech[open] summary {
		margin-bottom: 10px;
		color: var(--text-primary);
	}

	.tech-grid {
		display: grid;
		grid-template-columns: repeat(2, 1fr);
		gap: 8px;
		font-size: 11.5px;
	}

	.tech-row {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.tech-label {
		color: var(--text-tertiary);
		text-transform: uppercase;
		font-size: 9.5px;
		font-weight: 500;
	}

	.tech-value {
		color: var(--text-secondary);
	}

	/* Processing States in Workspace */
	.processing-stats {
		padding: 14px;
		border-radius: 6px;
		border: 1px solid var(--border-subtle);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.ps-row {
		display: flex;
		justify-content: space-between;
		font-size: 13px;
	}

	.ps-label {
		color: var(--text-secondary);
	}

	.ps-value {
		color: var(--text-primary);
		font-weight: 500;
	}

	.progress-bar-container {
		width: 100%;
		height: 4px;
		background: var(--bg-hover);
		border-radius: 99px;
		overflow: hidden;
		margin-top: 4px;
	}

	.progress-bar-fill {
		height: 100%;
		background: var(--accent-primary);
		border-radius: 99px;
		transition: width 300ms ease;
	}

	/* System Trace Timeline */
	.trace-timeline {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.timeline-step {
		display: flex;
		gap: 12px;
		position: relative;
	}

	.timeline-step::before {
		content: '';
		position: absolute;
		left: 7px;
		top: 18px;
		bottom: -22px;
		width: 1px;
		background: var(--border-subtle);
	}

	.timeline-step:last-child::before {
		display: none;
	}

	.timeline-icon {
		width: 16px;
		height: 16px;
		border-radius: 50%;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
		z-index: 1;
		font-size: 8px;
		font-weight: bold;
	}

	.timeline-icon.done {
		background: rgba(63, 185, 80, 0.15);
		border: 1px solid rgba(63, 185, 80, 0.4);
		color: var(--color-low);
	}

	.timeline-icon.active {
		background: var(--bg-hover);
		border: 1px solid var(--border-strong);
	}

	.timeline-content {
		display: flex;
		justify-content: space-between;
		width: 100%;
		align-items: baseline;
		font-size: 13px;
	}

	.timeline-text {
		color: var(--text-primary);
		font-weight: 500;
	}

	.timeline-time {
		font-size: 12px;
		font-family: monospace;
	}

	.time-active {
		color: var(--accent-primary);
	}

	.empty-tab-state, .clauses-loading {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 180px;
		color: var(--text-tertiary);
		gap: 10px;
	}

	/* Global modal overrides for dynamic cockpit context */
	.modal-root {
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 200;
	}

	.modal-backdrop {
		position: absolute;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background: rgba(0,0,0,0.6);
		backdrop-filter: blur(4px);
		border: none;
		width: 100%;
		height: 100%;
		cursor: default;
	}

	.modal-content {
		background: var(--bg-panel);
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		width: 100%;
		max-width: 440px;
		padding: 24px;
		display: flex;
		flex-direction: column;
		gap: 16px;
		z-index: 201;
		box-shadow: 0 20px 50px rgba(0,0,0,0.6);
		animation: modalReveal 180ms var(--ease-out) forwards;
	}

	@keyframes modalReveal {
		from { opacity: 0; transform: scale(0.96); }
		to { opacity: 1; transform: scale(1); }
	}

	.modal-header {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.modal-icon {
		width: 32px;
		height: 32px;
		border-radius: 50%;
		display: flex;
		align-items: center;
		justify-content: center;
		flex-shrink: 0;
	}

	.modal-icon.warning {
		background: rgba(248, 81, 73, 0.1);
		border: 1px solid rgba(248, 81, 73, 0.3);
		color: var(--color-high);
	}

	.modal-header h3 {
		font-size: 15px;
		font-weight: 600;
		margin: 0;
	}

	.modal-body {
		font-size: 13px;
		color: var(--text-secondary);
		line-height: 1.6;
	}

	.modal-footer {
		display: flex;
		gap: 8px;
	}

	.clause-interactive-card.active-card {
		border-color: var(--accent-primary) !important;
		box-shadow: 0 4px 16px var(--glow-low);
	}

	/* Version select and dropdown */
	.version-select-container {
		position: relative;
		display: inline-block;
	}
	.version-dropdown-trigger {
		background: transparent;
		border: none;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		cursor: pointer;
		font-size: 13px;
		font-weight: 500;
		color: var(--text-primary);
		padding: 2px 6px;
		border-radius: 6px;
		transition: background-color 150ms var(--ease-out);
	}
	.version-dropdown-trigger:hover {
		background-color: var(--bg-hover);
	}
	.version-dropdown-trigger:active {
		transform: scale(0.97);
	}
	.version-badge {
		background: var(--bg-active);
		color: var(--text-secondary);
		font-size: 10px;
		padding: 1px 5px;
		border-radius: 4px;
		font-weight: 600;
	}
	.dropdown-chevron {
		transition: transform 150ms var(--ease-out);
		color: var(--text-tertiary);
	}
	.dropdown-chevron.open {
		transform: rotate(180deg);
	}
	.version-dropdown-menu {
		position: absolute;
		top: calc(100% + 4px);
		left: 0;
		z-index: 1000;
		min-width: 220px;
		background: var(--bg-sidebar);
		border: 1px solid var(--border-subtle);
		border-radius: 6px;
		box-shadow: var(--shadow-lg);
		padding: 4px;
		display: flex;
		flex-direction: column;
	}
	.dropdown-header {
		font-size: 11px;
		text-transform: uppercase;
		font-weight: 600;
		color: var(--text-tertiary);
		padding: 6px 12px;
		letter-spacing: 0.05em;
	}
	.version-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 8px 12px;
		border-radius: 6px;
		text-decoration: none;
		color: var(--text-secondary);
		transition: background-color 120ms var(--ease-out), color 120ms var(--ease-out);
	}
	.version-item:hover {
		background-color: var(--bg-hover);
		color: var(--text-primary);
	}
	.version-item.active {
		background-color: var(--bg-active);
		color: var(--text-primary);
		font-weight: 500;
	}
	.version-item-left {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}
	.version-item-badge {
		font-size: 12px;
	}
	.version-item-date {
		font-size: 10px;
		color: var(--text-tertiary);
	}
	.check-icon {
		color: var(--accent-primary);
	}

	/* Verification tab styles */
	.verification-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 20px;
		border-radius: 8px;
		border: 1px solid var(--border-subtle);
		gap: 16px;
	}
	.vh-left {
		display: flex;
		align-items: center;
		gap: 12px;
	}
	.verify-icon {
		color: #3fb950;
	}
	.vh-stats {
		display: flex;
		gap: 8px;
	}
	.vstat-badge {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		padding: 6px 12px;
		border-radius: 6px;
		min-width: 68px;
		border: 1px solid transparent;
	}
	.vstat-badge.success {
		background: rgba(46, 160, 67, 0.08);
		border-color: rgba(46, 160, 67, 0.15);
		color: #3fb950;
	}
	.vstat-badge.warning {
		background: rgba(210, 153, 34, 0.08);
		border-color: rgba(210, 153, 34, 0.15);
		color: #d29922;
	}
	.vstat-badge.danger {
		background: rgba(248, 81, 73, 0.08);
		border-color: rgba(248, 81, 73, 0.15);
		color: #f85149;
	}
	.vstat-num {
		font-size: 16px;
		font-weight: 700;
		line-height: 1;
	}
	.vstat-label {
		font-size: 9px;
		text-transform: uppercase;
		font-weight: 600;
		opacity: 0.8;
		margin-top: 2px;
	}

	.resolution-card {
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		transition: transform 180ms var(--ease-out), box-shadow 180ms var(--ease-out);
	}
	.resolution-card:hover {
		transform: translateY(-1px);
		box-shadow: var(--shadow-md);
	}
	.rc-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 12px 16px;
		background: var(--bg-hover);
		border-bottom: 1px solid var(--border-subtle);
	}
	.rc-header-left {
		display: flex;
		align-items: center;
		gap: 10px;
	}
	.clause-type-tag {
		font-weight: 600;
		font-size: 12px;
		color: var(--text-primary);
		text-transform: uppercase;
		letter-spacing: 0.02em;
	}
	.risk-pill {
		display: inline-flex;
		align-items: center;
		gap: 6px;
		padding: 4px 8px;
		border-radius: 999px;
		border: 1px solid var(--border-subtle);
		background: var(--bg-panel);
		white-space: nowrap;
	}
	.rp-label {
		font-size: 11px;
		color: var(--text-tertiary);
		font-weight: 650;
	}
	.rp-level {
		font-size: 11px;
		font-weight: 750;
		text-transform: uppercase;
	}
	.risk-pill.risk-critical .rp-level { color: var(--color-critical); }
	.risk-pill.risk-high .rp-level { color: var(--color-high); }
	.risk-pill.risk-medium .rp-level { color: var(--color-medium); }
	.risk-pill.risk-low .rp-level { color: var(--color-low); }

	.rc-comparison-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		border-bottom: 1px solid var(--border-subtle);
		background: var(--bg-panel);
	}
	.rc-comparison-grid .pane {
		padding: 16px;
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.rc-comparison-grid .pane-original {
		border-right: 1px solid var(--border-subtle);
		background: rgba(248, 81, 73, 0.01);
	}
	.rc-comparison-grid .pane-revised {
		background: rgba(46, 160, 67, 0.01);
	}
	.pane-label {
		font-size: 10px;
		font-weight: 600;
		color: var(--text-tertiary);
		text-transform: uppercase;
		letter-spacing: 0.05em;
	}
	.pane-content {
		font-size: 13px;
		line-height: 1.6;
		color: var(--text-secondary);
	}
	.text-strikethrough {
		text-decoration: line-through;
		opacity: 0.75;
	}
	.highlight-revised {
		color: var(--text-primary);
	}
	.redline-rec {
		margin-top: 10px;
		padding: 10px 12px;
		border-radius: 6px;
		border-left: 3px solid var(--accent-primary);
	}
	.rr-label {
		font-size: 10px;
		font-weight: 600;
		color: var(--accent-primary);
		margin-bottom: 4px;
	}
	.rr-text {
		font-size: 12px;
		line-height: 1.5;
		color: var(--text-secondary);
	}

	.rc-explanation {
		padding: 12px 16px;
		display: flex;
		flex-direction: column;
		gap: 4px;
		border-top: 1px dashed var(--border-subtle);
	}
	.ex-header {
		display: flex;
		align-items: center;
		gap: 6px;
		font-size: 11px;
		font-weight: 600;
		color: var(--text-primary);
	}
	.ex-body {
		font-size: 12px;
		line-height: 1.5;
		color: var(--text-secondary);
		margin: 0;
	}

	/* Revision upload modal specific styles */
	.tabs-nav-modal {
		display: flex;
		border-bottom: 1px solid var(--border-subtle);
		margin-bottom: 20px;
	}
	.tab-nav-modal-btn {
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		padding: 8px 16px;
		font-size: 13px;
		font-weight: 500;
		color: var(--text-secondary);
		cursor: pointer;
		transition: all 120ms var(--ease-out);
	}
	.tab-nav-modal-btn:hover {
		color: var(--text-primary);
	}
	.tab-nav-modal-btn.active {
		color: var(--accent-primary);
		border-bottom-color: var(--accent-primary);
	}
	.tab-nav-modal-btn:active {
		transform: scale(0.97);
	}
	.upload-dropzone {
		border: 2px dashed var(--border-subtle);
		border-radius: 8px;
		padding: 32px 20px;
		text-align: center;
		cursor: pointer;
		background: var(--bg-hover);
		transition: border-color 150ms var(--ease-out), background-color 150ms var(--ease-out);
	}
	.upload-dropzone:hover {
		border-color: var(--accent-primary);
		background: var(--bg-active);
	}
	.dropzone-label {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		color: var(--text-secondary);
		font-size: 13px;
	}
	.dropzone-subtitle {
		font-size: 11px;
		color: var(--text-tertiary);
	}
	.selected-file-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 10px 14px;
		background: var(--bg-active);
		border: 1px solid var(--border-subtle);
		border-radius: 6px;
		margin-top: 12px;
		font-size: 13px;
		color: var(--text-primary);
	}
	.btn-remove-file {
		background: transparent;
		border: none;
		color: var(--text-tertiary);
		cursor: pointer;
	}
	.btn-remove-file:hover {
		color: var(--color-high);
	}
	.btn-remove-file:active {
		transform: scale(0.97);
	}

	/* Obligations */
	.obligations-list {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.obligation-card {
		padding: 12px;
		border-radius: 12px;
		border: 1px solid var(--border-subtle);
	}
	.obligation-meta {
		display: grid;
		gap: 6px;
		font-size: 12px;
		color: var(--text-secondary);
	}

	/* Trace events */
	.events-list {
		margin-top: 14px;
		padding: 10px;
		border-radius: 12px;
		border: 1px solid var(--border-subtle);
		max-height: 520px;
		overflow: auto;
	}
	.event-row {
		border: 1px solid var(--border-subtle);
		border-radius: 12px;
		background: var(--bg-panel);
		margin-bottom: 8px;
		overflow: hidden;
	}
	.event-summary {
		display: grid;
		grid-template-columns: auto 1fr auto;
		align-items: center;
		gap: 10px;
		padding: 10px 10px;
		cursor: pointer;
	}
	.event-message {
		color: var(--text-primary);
		font-size: 13px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.event-payload {
		margin: 0;
		padding: 10px 12px;
		border-top: 1px solid var(--border-subtle);
		background: var(--bg-app);
		font-size: 12px;
		white-space: pre-wrap;
	}

	/* Redline Cockpit Layout Tweaks */
	.vh-right {
		display: flex;
		align-items: center;
		gap: 16px;
	}
	.vh-divider {
		width: 1px;
		height: 28px;
		background: var(--border-subtle);
	}
	/* Premium Email Composer Modal override */
	.modal-backdrop {
		transition: background-color 280ms var(--ease-drawer), backdrop-filter 280ms var(--ease-drawer);
	}
	@starting-style {
		.modal-backdrop {
			background-color: rgba(0, 0, 0, 0) !important;
			backdrop-filter: blur(0px) !important;
		}
	}

	.modal-content-wide.email-composer {
		max-width: 760px;
		background: rgba(15, 18, 25, 0.82); /* Glass dark obsidian styling */
		backdrop-filter: blur(24px) saturate(190%);
		-webkit-backdrop-filter: blur(24px) saturate(190%);
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: var(--radius-md);
		position: relative;
		overflow: hidden;
		gap: 0;
		padding: 0;
		box-shadow: 0 30px 70px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.05);
		opacity: 1;
		transform: scale(1) translateY(0);
		transition: opacity 280ms var(--ease-drawer), transform 280ms var(--ease-drawer);
	}
	@starting-style {
		.modal-content-wide.email-composer {
			opacity: 0;
			transform: scale(0.96) translateY(16px);
		}
	}

	.email-mac-buttons {
		position: absolute;
		top: 22px;
		left: 20px;
		display: flex;
		gap: 8px;
		z-index: 210;
	}
	.mac-dot {
		width: 12px;
		height: 12px;
		border-radius: 50%;
		border: none;
		cursor: pointer;
		position: relative;
		padding: 0;
		transition: transform 120ms var(--ease-out), filter 120ms var(--ease-out), opacity 120ms var(--ease-out);
		box-shadow: inset 0 0.5px 1px rgba(255, 255, 255, 0.15);
	}
	.mac-dot:active {
		transform: scale(0.9);
	}
	.mac-close {
		background: #ff5f56;
	}
	.mac-close:hover {
		background: #e0443e;
	}
	.mac-minimize {
		background: #ffbd2e;
	}
	.mac-maximize {
		background: #27c93f;
	}
	.email-mac-buttons:hover .mac-dot {
		filter: brightness(0.85);
	}

	.email-composer-header {
		padding: 22px 24px 18px 24px;
		border-bottom: 1px solid rgba(255, 255, 255, 0.06);
		display: flex;
		align-items: center;
		gap: 12px;
		margin-left: 68px; /* push to the right of mac buttons */
	}

	.email-composer-body {
		padding: 16px 24px 20px 24px;
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.email-composer-controls {
		display: flex;
		align-items: center;
		gap: 16px;
		background: rgba(22, 27, 34, 0.45);
		padding: 10px 16px;
		border-radius: var(--radius-sm);
		border: 1px solid rgba(255, 255, 255, 0.05);
		backdrop-filter: blur(10px);
	}

	.control-group {
		display: flex;
		align-items: center;
		gap: 8px;
	}
	.control-group label {
		font-size: 11px;
		color: #8b949e;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.custom-select-wrapper {
		position: relative;
		display: inline-flex;
		align-items: center;
	}
	.custom-select {
		background: rgba(13, 17, 23, 0.65);
		border: 1px solid rgba(255, 255, 255, 0.08);
		border-radius: 6px;
		color: #e6edf3;
		font-size: 12px;
		font-weight: 550;
		height: 32px;
		padding: 0 28px 0 12px;
		outline: none;
		cursor: pointer;
		appearance: none;
		-webkit-appearance: none;
		min-width: 140px;
		transition: border-color 150ms var(--ease-out), box-shadow 150ms var(--ease-out), background-color 150ms var(--ease-out);
	}
	.custom-select:hover {
		border-color: rgba(255, 255, 255, 0.18);
		background-color: rgba(13, 17, 23, 0.85);
	}
	.custom-select:focus {
		border-color: var(--ai);
		box-shadow: 0 0 0 3px rgba(var(--ai-rgb), 0.25);
	}
	.custom-select-wrapper::after {
		content: "";
		position: absolute;
		right: 12px;
		top: 50%;
		transform: translateY(-50%);
		width: 8px;
		height: 8px;
		background: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%238b949e' stroke-width='3' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E") no-repeat center center;
		background-size: contain;
		pointer-events: none;
		transition: transform 180ms var(--ease-out);
	}
	.custom-select-wrapper:focus-within::after {
		transform: translateY(-50%) rotate(180deg);
	}

	.control-spacer {
		flex: 1;
	}

	.btn-regenerate {
		height: 32px;
		font-size: 12px;
		font-weight: 600;
		padding: 0 14px;
		border-radius: 6px;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		gap: 6px;
		transition: transform 160ms var(--ease-out), background-color 200ms var(--ease-out), border-color 200ms var(--ease-out), box-shadow 200ms var(--ease-out);
	}
	.btn-regenerate:active {
		transform: scale(0.97);
	}
	.btn-regenerate:hover .icon-spin {
		transform: rotate(45deg);
	}

	.email-loading-pane {
		min-height: 380px;
		background: rgba(22, 27, 34, 0.45);
		border-radius: 10px;
		border: 1px solid rgba(255, 255, 255, 0.05);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		box-shadow: inset 0 2px 12px rgba(0, 0, 0, 0.2);
	}

	.email-loading-status {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 14px 20px;
		border-bottom: 1px solid rgba(255, 255, 255, 0.05);
		background: rgba(var(--ai-rgb), 0.06);
	}
	.email-loading-text {
		font-size: 12.5px;
		font-weight: 600;
		color: var(--ai);
		letter-spacing: 0.01em;
	}

	.loading-envelope-headers {
		padding: 18px 20px;
		border-bottom: 1px solid rgba(255, 255, 255, 0.05);
		display: flex;
		flex-direction: column;
		gap: 12px;
	}
	.loading-row {
		display: flex;
		align-items: center;
		gap: 16px;
	}
	.loading-label {
		width: 48px;
		height: 10px;
		border-radius: 4px;
		background: rgba(255, 255, 255, 0.04);
	}
	.loading-value {
		width: 180px;
		height: 10px;
		border-radius: 4px;
	}
	.loading-body {
		padding: 24px 20px;
		display: flex;
		flex-direction: column;
		gap: 14px;
	}
	.loading-line {
		height: 10px;
		border-radius: 4px;
	}
	.width-70 { width: 70%; }
	.width-80 { width: 80%; }
	.width-90 { width: 90%; }
	.width-50 { width: 50%; }

	.skeleton {
		background: linear-gradient(90deg, rgba(255, 255, 255, 0.02) 25%, rgba(255, 255, 255, 0.07) 50%, rgba(255, 255, 255, 0.02) 75%);
		background-size: 200% 100%;
		animation: shimmer 1.6s infinite var(--ease-in-out);
	}
	@keyframes shimmer {
		0% { background-position: 200% 0; }
		100% { background-position: -200% 0; }
	}

	.email-workspace {
		background: rgba(22, 27, 34, 0.45);
		border: 1px solid rgba(255, 255, 255, 0.05);
		border-radius: 10px;
		display: flex;
		flex-direction: column;
		box-shadow: inset 0 2px 12px rgba(0, 0, 0, 0.3), 0 4px 24px rgba(0, 0, 0, 0.2);
		overflow: hidden;
	}

	.email-envelope-headers {
		padding: 16px 20px;
		background: rgba(13, 17, 23, 0.25);
		border-bottom: 1px solid rgba(255, 255, 255, 0.05);
		display: flex;
		flex-direction: column;
		gap: 10px;
	}

	.envelope-row {
		display: flex;
		align-items: center;
		font-size: 13px;
	}
	.env-label {
		width: 72px;
		color: #8b949e;
		font-weight: 600;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}
	.env-value {
		display: flex;
		align-items: center;
		gap: 8px;
		flex: 1;
		color: #c9d1d9;
	}
	.subject-value {
		color: #f0f6fc !important;
		font-size: 14px;
	}
	.env-tag {
		font-size: 11px;
		padding: 3px 10px;
		border-radius: 6px;
		background: rgba(255, 255, 255, 0.04);
		border: 1px solid rgba(255, 255, 255, 0.08);
		color: #c9d1d9;
		font-weight: 500;
		box-shadow: 0 1px 2px rgba(0,0,0,0.05);
	}
	.env-tag-ai {
		background: rgba(var(--ai-rgb), 0.12) !important;
		border-color: rgba(var(--ai-rgb), 0.25) !important;
		color: var(--ai) !important;
		font-weight: 600;
	}
	.env-address {
		font-family: var(--font-mono);
		font-size: 11px;
		color: #57606a;
	}

	.email-body-pane {
		background: rgba(13, 17, 23, 0.2);
		padding: 24px 20px;
		min-height: 240px;
		max-height: 340px;
		overflow-y: auto;
		border-top: none;
		box-shadow: inset 0 2px 8px rgba(0, 0, 0, 0.15);
	}
	.email-body-pane::-webkit-scrollbar {
		width: 6px;
	}
	.email-body-pane::-webkit-scrollbar-track {
		background: transparent;
	}
	.email-body-pane::-webkit-scrollbar-thumb {
		background-color: rgba(255, 255, 255, 0.08);
		border-radius: 99px;
	}
	:global([data-theme="light"]) .email-body-pane::-webkit-scrollbar-thumb {
		background-color: rgba(0, 0, 0, 0.12);
	}
	.email-body-pane::-webkit-scrollbar-thumb:hover {
		background-color: rgba(255, 255, 255, 0.16);
	}
	:global([data-theme="light"]) .email-body-pane::-webkit-scrollbar-thumb:hover {
		background-color: rgba(0, 0, 0, 0.2);
	}

	.email-body-text {
		margin: 0;
		white-space: pre-wrap;
		font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
		font-size: 14px;
		line-height: 1.6;
		color: #e6edf3;
		letter-spacing: -0.010em;
		selection-background-color: rgba(var(--ai-rgb), 0.3);
	}

	.email-composer-footer {
		padding: 16px 24px 20px 24px;
		border-top: 1px solid rgba(255, 255, 255, 0.05);
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		background: rgba(13, 17, 23, 0.15);
	}

	/* ---- Light theme adaptation for the email composer ---- */
	:global([data-theme="light"]) .modal-content-wide.email-composer {
		background: rgba(255, 255, 255, 0.94);
		border: 1px solid var(--border-subtle);
		box-shadow: 0 30px 70px rgba(0, 0, 0, 0.18), inset 0 1px 0 rgba(255, 255, 255, 0.7);
	}
	:global([data-theme="light"]) .email-composer-header {
		border-bottom: 1px solid var(--border-subtle);
	}
	:global([data-theme="light"]) .email-composer-controls {
		background: var(--bg-hover);
		border: 1px solid var(--border-subtle);
	}
	:global([data-theme="light"]) .control-group label {
		color: var(--text-secondary);
	}
	:global([data-theme="light"]) .custom-select {
		background: #ffffff;
		border: 1px solid var(--border-subtle);
		color: var(--text-primary);
	}
	:global([data-theme="light"]) .custom-select:hover {
		background-color: var(--bg-hover);
		border-color: var(--border-strong);
	}
	:global([data-theme="light"]) .email-loading-pane {
		background: var(--bg-hover);
		border: 1px solid var(--border-subtle);
		box-shadow: inset 0 2px 12px rgba(0, 0, 0, 0.05);
	}
	:global([data-theme="light"]) .email-loading-status,
	:global([data-theme="light"]) .loading-envelope-headers {
		border-bottom: 1px solid var(--border-subtle);
	}
	:global([data-theme="light"]) .loading-label {
		background: rgba(0, 0, 0, 0.05);
	}
	:global([data-theme="light"]) .skeleton {
		background: linear-gradient(90deg, rgba(0, 0, 0, 0.04) 25%, rgba(0, 0, 0, 0.08) 50%, rgba(0, 0, 0, 0.04) 75%);
		background-size: 200% 100%;
	}
	:global([data-theme="light"]) .email-workspace {
		background: #ffffff;
		border: 1px solid var(--border-subtle);
		box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.04), 0 4px 24px rgba(0, 0, 0, 0.06);
	}
	:global([data-theme="light"]) .email-envelope-headers {
		background: var(--bg-app);
		border-bottom: 1px solid var(--border-subtle);
	}
	:global([data-theme="light"]) .env-label {
		color: var(--text-secondary);
	}
	:global([data-theme="light"]) .env-value {
		color: var(--text-primary);
	}
	:global([data-theme="light"]) .subject-value {
		color: var(--text-primary) !important;
	}
	:global([data-theme="light"]) .env-tag {
		background: var(--bg-hover);
		border: 1px solid var(--border-subtle);
		color: var(--text-secondary);
	}
	:global([data-theme="light"]) .env-address {
		color: var(--text-tertiary);
	}
	:global([data-theme="light"]) .email-body-pane {
		background: #ffffff;
		box-shadow: inset 0 1px 4px rgba(0, 0, 0, 0.03);
	}
	:global([data-theme="light"]) .email-body-text {
		color: var(--text-primary);
	}
	:global([data-theme="light"]) .email-composer-footer {
		border-top: 1px solid var(--border-subtle);
		background: var(--bg-app);
	}

	.btn-copy-draft {
		position: relative;
		min-width: 170px;
		height: 34px;
		padding: 0 16px;
		cursor: pointer;
		background: var(--accent-primary);
		border: 1px solid rgba(255, 255, 255, 0.1);
		transition: transform 160ms var(--ease-out), background-color 200ms var(--ease-out), border-color 200ms var(--ease-out), box-shadow 200ms var(--ease-out);
		overflow: hidden;
	}
	.btn-copy-draft:active {
		transform: scale(0.97);
	}
	.btn-copy-draft.copied {
		background: #1a7f37 !important;
		border-color: rgba(255, 255, 255, 0.15) !important;
		color: #ffffff !important;
		box-shadow: 0 0 16px rgba(46, 160, 67, 0.35) !important;
	}

	.copy-state-wrapper {
		position: relative;
		display: flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		height: 100%;
	}
	.copy-state {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
		transition: opacity 180ms var(--ease-out), transform 180ms var(--ease-spring-gentle), filter 180ms var(--ease-out);
		white-space: nowrap;
	}
	.copy-state-default {
		opacity: 1;
		transform: scale(1);
		filter: blur(0px);
	}
	.copy-state-default.state-hidden {
		opacity: 0;
		transform: scale(0.94);
		filter: blur(2px);
		pointer-events: none;
		position: absolute;
	}
	.copy-state-success {
		opacity: 0;
		transform: scale(0.94);
		filter: blur(2px);
		pointer-events: none;
		position: absolute;
	}
	.copy-state-success.state-visible {
		opacity: 1;
		transform: scale(1);
		filter: blur(0px);
		position: relative;
		pointer-events: auto;
	}
	.copy-icon-success {
		stroke: #ffffff;
		animation: scaleIn 220ms var(--ease-spring);
	}
	@keyframes scaleIn {
		from { transform: scale(0.5); opacity: 0; }
		to { transform: scale(1); opacity: 1; }
	}
	
	.icon-spin.spin {
		animation: spin 1.2s linear infinite;
	}
	@keyframes spin {
		from { transform: rotate(0deg); }
		to { transform: rotate(360deg); }
	}

	/* ------------------------------------------------------------
	   Template Deviation tab (first-party paper)
	   ------------------------------------------------------------- */
	.dev-picker-card {
		border: 1px solid var(--border-subtle);
		border-radius: var(--radius-lg);
		padding: 20px;
		background: var(--bg-panel);
	}
	.dev-picker-row {
		display: flex;
		gap: 10px;
		align-items: center;
		flex-wrap: wrap;
	}
	.dev-select {
		flex: 1;
		min-width: 220px;
		height: 36px;
		padding: 0 12px;
		border-radius: var(--radius-md);
		border: 1.5px solid var(--border-subtle);
		background: var(--bg-hover);
		color: var(--text-primary);
		font-size: 13px;
		outline: none;
	}
	.dev-select:focus {
		border-color: var(--accent-primary);
		background: #fff;
		box-shadow: var(--ring);
	}
	.dev-kind-tag {
		display: inline-flex;
		align-items: center;
		padding: 2px 10px;
		border-radius: 999px;
		font-size: 10px;
		font-weight: 700;
		letter-spacing: 0.05em;
	}
	.dev-kind-deleted {
		background: rgba(var(--color-critical-rgb), 0.12);
		color: var(--color-critical-text);
		border: 1px solid rgba(var(--color-critical-rgb), 0.3);
	}
	.dev-kind-added {
		background: rgba(var(--color-high-rgb), 0.12);
		color: var(--color-high-text);
		border: 1px solid rgba(var(--color-high-rgb), 0.3);
	}
	.dev-kind-modified {
		background: rgba(var(--accent-primary-rgb), 0.1);
		color: var(--accent-primary);
		border: 1px solid rgba(var(--accent-primary-rgb), 0.25);
	}
	.dev-card.dev-deleted {
		border-color: rgba(var(--color-critical-rgb), 0.35);
		box-shadow: 0 4px 24px rgba(var(--color-critical-rgb), 0.08);
	}
	.dev-removed {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 6px;
		min-height: 120px;
		height: 100%;
		border: 1.5px dashed rgba(var(--color-critical-rgb), 0.45);
		border-radius: 10px;
		background: rgba(var(--color-critical-rgb), 0.05);
		color: var(--color-critical-text);
		font-weight: 700;
		font-size: 12px;
		letter-spacing: 0.05em;
		text-align: center;
		padding: 16px;
	}
	.dev-removed-sub {
		font-weight: 400;
		font-size: 11.5px;
		letter-spacing: 0;
		color: var(--text-secondary);
	}
	.dev-absent {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 120px;
		height: 100%;
		border: 1.5px dashed var(--border-strong);
		border-radius: 10px;
		color: var(--text-tertiary);
		font-size: 12px;
		padding: 16px;
		text-align: center;
	}
	.dev-matched-note {
		color: var(--color-low-text);
		font-size: 12.5px;
		padding: 10px 14px;
		background: rgba(var(--color-low-rgb), 0.07);
		border: 1px solid rgba(var(--color-low-rgb), 0.2);
		border-radius: 10px;
	}
	.dev-rerun-row {
		display: flex;
		gap: 10px;
		align-items: center;
		padding-top: 4px;
	}

	/* Portfolio intelligence (overview tab) */
	.intel-card {
		display: flex;
		flex-direction: column;
		gap: 18px;
		padding: 16px;
		border: 1px solid var(--border-subtle);
		border-radius: 12px;
	}
	.intel-subsection {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.intel-heading {
		margin: 0;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.6px;
		color: var(--text-tertiary);
	}
	.intel-heading-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 10px;
	}
	.intel-toggle {
		background: none;
		border: 1px solid var(--border-subtle);
		border-radius: var(--radius-pill);
		padding: 2px 10px;
		font-size: 11px;
		font-weight: 600;
		color: var(--accent-primary);
		cursor: pointer;
	}
	.intel-toggle:hover {
		background: var(--bg-hover);
	}
	.intel-row {
		display: flex;
		align-items: flex-start;
		gap: 10px;
		padding: 9px 12px;
		border: 1px solid var(--border-subtle);
		border-radius: 10px;
		background: var(--bg-panel);
	}
	.intel-row .badge {
		flex-shrink: 0;
		margin-top: 1px;
	}
	.intel-row-link {
		width: 100%;
		text-align: left;
		font: inherit;
		cursor: pointer;
		transition: background 0.15s var(--ease-out);
	}
	.intel-row-link:hover {
		background: var(--bg-hover);
	}
	.intel-row-main {
		display: flex;
		flex-direction: column;
		gap: 2px;
		min-width: 0;
		flex: 1;
	}
	.intel-row-title {
		font-size: 13px;
		font-weight: 600;
		color: var(--text-primary);
		overflow-wrap: anywhere;
	}
	.intel-caption {
		font-size: 12px;
		color: var(--text-tertiary);
	}
	.intel-excerpt {
		margin: 4px 0 0;
		padding: 8px 10px;
		font-family: 'SF Mono', ui-monospace, monospace;
		font-size: 11.5px;
		line-height: 1.5;
		color: var(--text-secondary);
		background: var(--bg-hover);
		border-radius: 8px;
		white-space: pre-wrap;
		overflow: hidden;
		display: -webkit-box;
		-webkit-line-clamp: 3;
		-webkit-box-orient: vertical;
	}
	.intel-btn {
		flex-shrink: 0;
		padding: 4px 12px;
		font-size: 12px;
	}
	.intel-attr-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 8px;
	}
	.intel-attr {
		display: flex;
		flex-direction: column;
		gap: 2px;
		padding: 8px 12px;
		border: 1px solid var(--border-subtle);
		border-radius: 10px;
		background: var(--bg-panel);
	}
	.intel-attr-key {
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.4px;
		color: var(--text-tertiary);
	}
	.intel-attr-value {
		font-size: 13px;
		color: var(--text-primary);
		overflow-wrap: anywhere;
	}

	/* --- Workflow cockpit --- */
	.wf-strip {
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
	}
	.wf-loading {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 10px;
		padding: 40px 0;
	}
	.wf-flow-card {
		border: 1px solid var(--border-subtle);
		border-radius: 12px;
		padding: 22px 18px;
	}
	.wf-stepper {
		display: flex;
		align-items: flex-start;
		gap: 0;
		overflow-x: auto;
		padding-bottom: 6px;
	}
	.wf-node {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 6px;
		width: 90px;
		flex-shrink: 0;
		text-align: center;
	}
	.wf-circle {
		width: 30px;
		height: 30px;
		border-radius: 50%;
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 12px;
		font-weight: 700;
		border: 2px solid var(--border-subtle);
		background: var(--bg-panel);
		color: var(--text-tertiary);
		flex-shrink: 0;
	}
	.wf-done .wf-circle {
		background: var(--accent-primary);
		border-color: var(--accent-primary);
		color: #fff;
	}
	.wf-current .wf-circle {
		border-color: var(--accent-primary);
		color: var(--accent-primary);
		box-shadow: var(--ring);
	}
	.wf-node-label {
		font-size: 11px;
		font-weight: 600;
		color: var(--text-secondary);
		line-height: 1.25;
	}
	.wf-current .wf-node-label { color: var(--text-primary); }
	.wf-upcoming .wf-node-label { color: var(--text-tertiary); }
	.wf-node-owner {
		font-size: 9px;
		text-transform: uppercase;
		letter-spacing: 0.4px;
		color: var(--text-tertiary);
	}
	.wf-connector {
		flex: 1;
		min-width: 20px;
		height: 2px;
		margin-top: 14px;
		background: var(--border-subtle);
	}
	.wf-conn-done { background: var(--accent-primary); }
	.wf-terminal {
		margin-top: 14px;
		display: flex;
		justify-content: center;
	}
	.wf-banner {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 12px;
		padding: 16px 18px;
	}
	.wf-banner-main {
		display: flex;
		flex-direction: column;
		gap: 3px;
	}
	.wf-banner-label {
		font-size: 18px;
		font-weight: 700;
		color: var(--text-primary);
	}
	.wf-banner-sub { font-size: 12px; }
	.wf-subtitle {
		font-size: 13px;
		font-weight: 600;
		color: var(--text-primary);
		margin: 0 0 10px 0;
	}
	.wf-actions {
		display: flex;
		flex-direction: column;
		gap: 10px;
	}
	.wf-note-input {
		width: 100%;
		padding: 8px 12px;
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		background: var(--bg-panel);
		color: var(--text-primary);
		font-size: 13px;
	}
	.wf-note-input:focus {
		outline: none;
		border-color: var(--accent-primary);
		box-shadow: var(--ring);
	}
	.wf-action-btns {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
	}
	.wf-email-block {
		margin-top: 10px;
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		padding: 10px 12px;
		background: var(--bg-hover);
	}
	.wf-email-subject {
		font-size: 12px;
		font-weight: 600;
		color: var(--text-primary);
		margin-bottom: 6px;
	}
	.wf-email-body {
		font-family: ui-monospace, monospace;
		font-size: 11px;
		color: var(--text-secondary);
		white-space: pre-wrap;
		word-break: break-word;
		margin: 0;
	}
	.wf-approval-actions {
		display: flex;
		flex-wrap: wrap;
		gap: 8px;
		margin-top: 12px;
	}
	.wf-await-note {
		font-size: 12px;
		margin: 10px 0 0 0;
	}
	.wf-trace {
		list-style: none;
		margin: 0;
		padding: 0;
		display: flex;
		flex-direction: column;
		gap: 8px;
	}
	.wf-trace-item {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 10px;
		font-size: 12px;
		padding: 8px 12px;
		border: 1px solid var(--border-subtle);
		border-radius: 8px;
		background: var(--bg-panel);
	}
	.wf-trace-edge {
		font-weight: 600;
		color: var(--text-primary);
	}
	.wf-trace-meta { font-size: 11px; }

	/* --- Version round-trip history --- */
	.hist-timeline {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.hist-step {
		display: flex;
		gap: 14px;
		position: relative;
		padding-bottom: 18px;
	}
	.hist-step::before {
		content: '';
		position: absolute;
		left: 6px;
		top: 16px;
		bottom: -2px;
		width: 1px;
		background: var(--border-subtle);
	}
	.hist-step:last-child::before { display: none; }
	.hist-icon {
		width: 13px;
		height: 13px;
		border-radius: 50%;
		flex-shrink: 0;
		margin-top: 3px;
		z-index: 1;
		border: 2px solid var(--bg-panel);
	}
	.hist-internal {
		background: var(--accent-primary);
		box-shadow: 0 0 0 1px var(--accent-primary);
	}
	.hist-cp {
		background: #af52de;
		box-shadow: 0 0 0 1px #af52de;
	}
	.hist-current .hist-icon { box-shadow: 0 0 0 3px rgba(var(--accent-primary-rgb), 0.2); }
	.hist-content {
		display: flex;
		flex-direction: column;
		gap: 5px;
		flex: 1;
		min-width: 0;
	}
	.hist-row-top {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 8px;
	}
	.hist-ver {
		font-size: 13px;
		font-weight: 600;
		color: var(--text-primary);
	}
	.hist-time { font-size: 11px; margin-left: auto; }
	.hist-meta { font-size: 12px; }
	.hist-links {
		display: flex;
		gap: 14px;
		margin-top: 2px;
	}
	.hist-link {
		background: none;
		border: none;
		padding: 0;
		cursor: pointer;
		font-size: 12px;
		font-weight: 500;
		color: var(--accent-primary);
	}
	.hist-link:hover { text-decoration: underline; }

	.rev-party {
		margin-bottom: 16px;
	}
	.rev-party-label {
		font-size: 12px;
		font-weight: 600;
		color: var(--text-secondary);
		margin-bottom: 8px;
	}
	.rev-party-opts {
		display: flex;
		gap: 10px;
	}
	.rev-party-opt {
		flex: 1;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 4px;
		padding: 10px 12px;
		border: 1.5px solid var(--border-subtle);
		border-radius: var(--radius-md);
		background: var(--bg-hover);
		cursor: pointer;
		transition: border-color 150ms ease, background 150ms ease;
	}
	.rev-party-opt.active {
		border-color: var(--accent-primary);
		background: #fff;
		box-shadow: var(--ring);
	}
	.rev-party-hint {
		font-size: 11px;
		color: var(--text-tertiary);
	}

</style>
