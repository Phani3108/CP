import { apiFetch } from './api';

export type AssistAction = {
	type: 'open_contract' | 'view_clause' | 'view_deviations' | 'view_changes' | 'copy_redline' | 'draft_email' | string;
	label: string;
	contract_id?: string;
	clause_type?: string;
	text?: string;
};

export type AssistSource = {
	contract_id?: string;
	contract_name?: string;
	clause_type?: string;
	risk_level?: string;
	section?: string;
	text_excerpt?: string;
};

export type AssistMeta = {
	route?: string;
	query_scope?: string;
	conversation_mode?: string;
	grounded?: boolean | null;
	grounding_score?: number | null;
};

export type AssistResultRow = {
	id: string;
	filename: string;
	counterparty?: string | null;
	company?: string | null;
	contract_type?: string | null;
	expiry_date?: string | null;
	total_value?: number | null;
	currency?: string | null;
	business_unit?: string | null;
};

export type AssistMsg = {
	role: 'user' | 'assistant';
	content: string;
	error?: boolean;
	sources?: AssistSource[];
	actions?: AssistAction[];
	suggested?: string[];
	results?: AssistResultRow[];
	meta?: AssistMeta;
};

export type Conversation = {
	id: string;              // server uuid once known; "local-…" before first send
	title: string;
	updatedAt: number;
	messages: AssistMsg[];
	sessionId?: string;
	serverId?: string | null;
	loaded: boolean;         // messages hydrated from server
	messageCount?: number;
	contextContractId?: string | null;
};

export type AssistPageContext = { contract_id: string; contract_name?: string } | null;

const MODE_KEY = 'cp_assist_mode';
const browser = typeof window !== 'undefined';

function uid(): string {
	return 'local-' + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export const STARTER_QUESTIONS = [
	'How many contracts expire before 2027?',
	'Compare termination clauses across all contracts',
	'Which contracts have auto-renewal?'
];

export const STARTER_QUESTIONS_CONTRACT = [
	'Summarize the riskiest clauses in this contract',
	'What are the termination terms here?',
	'Does this auto-renew, and what is the notice window?'
];

function msgFromServer(m: any): AssistMsg {
	const meta = m.meta_json || {};
	return {
		role: m.role,
		content: m.content,
		sources: meta.sources || [],
		actions: meta.actions || [],
		suggested: meta.suggested_questions || [],
		results: meta.results || [],
		meta: { route: meta.route, query_scope: meta.query_scope, grounded: meta.grounded, grounding_score: meta.grounding_score }
	};
}

class AssistState {
	open = $state(false);
	/** floater | docked — fullscreen is the /assist route */
	mode = $state<'floater' | 'docked'>('floater');
	conversations = $state<Conversation[]>([]);
	activeId = $state<string | null>(null);
	loading = $state(false);
	listLoaded = $state(false);
	pageContext = $state<AssistPageContext>(null);

	constructor() {
		if (browser) {
			const m = localStorage.getItem(MODE_KEY);
			if (m === 'docked' || m === 'floater') this.mode = m;
		}
	}

	get sorted(): Conversation[] {
		return [...this.conversations].sort((a, b) => b.updatedAt - a.updatedAt);
	}

	get active(): Conversation | null {
		return this.conversations.find((c) => c.id === this.activeId) ?? null;
	}

	setPageContext(ctx: AssistPageContext) {
		this.pageContext = ctx;
	}

	setMode(mode: 'floater' | 'docked') {
		this.mode = mode;
		this.open = true;
		if (browser) localStorage.setItem(MODE_KEY, mode);
	}

	toggle() {
		this.open = !this.open;
		if (this.open) void this.ensureLoaded();
	}

	/** Hydrate the conversation rail from the server (once per session). */
	async ensureLoaded(): Promise<void> {
		if (this.listLoaded) return;
		try {
			const res = await apiFetch('/api/v1/conversations');
			if (!res.ok) return;
			const data = await res.json();
			const locals = this.conversations.filter((c) => !c.serverId && c.messages.length > 0);
			this.conversations = [
				...locals,
				...(data.conversations || []).map((c: any) => ({
					id: c.id,
					serverId: c.id,
					title: c.title,
					updatedAt: c.updated_at ? new Date(c.updated_at).getTime() : Date.now(),
					messages: [],
					loaded: false,
					messageCount: c.message_count,
					contextContractId: c.context_contract_id
				}))
			];
			this.listLoaded = true;
			if (!this.activeId && this.sorted.length > 0) this.activeId = this.sorted[0].id;
		} catch {
			/* offline — rail stays empty */
		}
	}

	async setActive(id: string) {
		this.activeId = id;
		const convo = this.conversations.find((c) => c.id === id);
		if (!convo || convo.loaded || !convo.serverId) return;
		try {
			const res = await apiFetch(`/api/v1/conversations/${convo.serverId}`);
			if (!res.ok) return;
			const data = await res.json();
			convo.messages = (data.messages || []).map(msgFromServer);
			convo.loaded = true;
			this.conversations = [...this.conversations];
		} catch {
			/* keep unloaded */
		}
	}

	newConversation(): Conversation {
		const convo: Conversation = {
			id: uid(),
			serverId: null,
			title: 'New conversation',
			updatedAt: Date.now(),
			messages: [],
			loaded: true
		};
		this.conversations = [convo, ...this.conversations];
		this.activeId = convo.id;
		return convo;
	}

	async deleteConversation(id: string) {
		const convo = this.conversations.find((c) => c.id === id);
		if (convo?.serverId) {
			try {
				await apiFetch(`/api/v1/conversations/${convo.serverId}`, { method: 'DELETE' });
			} catch {
				/* best effort */
			}
		}
		this.conversations = this.conversations.filter((c) => c.id !== id);
		if (this.activeId === id) this.activeId = this.sorted[0]?.id ?? null;
	}

	private ensureActive(): Conversation {
		return this.active ?? this.newConversation();
	}

	private touch(convo: Conversation) {
		convo.updatedAt = Date.now();
		this.conversations = [...this.conversations];
	}

	async send(text: string): Promise<void> {
		const q = (text || '').trim();
		if (!q || this.loading) return;
		void this.ensureLoaded();
		const convo = this.ensureActive();
		if (convo.messages.length === 0 && convo.title === 'New conversation') {
			convo.title = q.length > 44 ? q.slice(0, 44) + '…' : q;
		}
		convo.messages = [...convo.messages, { role: 'user', content: q }];
		this.touch(convo);
		this.loading = true;
		try {
			const res = await apiFetch('/api/v1/assistant/chat', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					question: q,
					session_id: convo.sessionId,
					conversation_id: convo.serverId,
					context: this.pageContext ?? undefined
				})
			});
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Assistant request failed');
			if (json.session_id) convo.sessionId = json.session_id;
			if (json.conversation_id && !convo.serverId) {
				convo.serverId = json.conversation_id;
				const oldId = convo.id;
				convo.id = json.conversation_id;
				if (this.activeId === oldId) this.activeId = convo.id;
			}
			convo.loaded = true;
			convo.messages = [
				...convo.messages,
				{
					role: 'assistant',
					content: json.answer || 'I could not generate an answer.',
					sources: json.sources || [],
					actions: json.actions || [],
					suggested: json.suggested_questions || [],
					results: json.results || [],
					meta: {
						route: json.route,
						query_scope: json.query_scope,
						conversation_mode: json.conversation_mode,
						grounded: json.grounded,
						grounding_score: json.grounding_score
					}
				}
			];
		} catch (e: any) {
			convo.messages = [
				...convo.messages,
				{ role: 'assistant', content: e?.message || 'Request failed.', error: true }
			];
		} finally {
			this.loading = false;
			this.touch(convo);
		}
	}

	async resend(): Promise<void> {
		const convo = this.active;
		if (!convo) return;
		const last = convo.messages[convo.messages.length - 1];
		if (last && last.role === 'assistant' && last.error) {
			convo.messages = convo.messages.slice(0, -1);
		}
		const lastUser = [...convo.messages].reverse().find((m) => m.role === 'user');
		if (!lastUser) return;
		const idx = convo.messages.lastIndexOf(lastUser);
		convo.messages = convo.messages.slice(0, idx);
		this.touch(convo);
		await this.send(lastUser.content);
	}

	/** In-chat "Draft vendor email" action — result lands in the thread. */
	async draftEmail(contractId: string): Promise<void> {
		const convo = this.ensureActive();
		this.loading = true;
		try {
			const res = await apiFetch(`/api/v1/contracts/${contractId}/redlines/email`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ tone: 'professional', include: 'all' })
			});
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Email draft failed');
			const subject = json?.email?.subject || 'Vendor email draft';
			const body = json?.email?.body || '';
			convo.messages = [
				...convo.messages,
				{
					role: 'assistant',
					content: `**Draft vendor email**\n\n**Subject:** ${subject}\n\n${body}`,
					actions: []
				}
			];
		} catch (e: any) {
			convo.messages = [
				...convo.messages,
				{ role: 'assistant', content: e?.message || 'Email draft failed.', error: true }
			];
		} finally {
			this.loading = false;
			this.touch(convo);
		}
	}
}

export const assist = new AssistState();

export function relativeTime(ts: number): string {
	const diff = Date.now() - ts;
	const m = Math.floor(diff / 60000);
	if (m < 1) return 'just now';
	if (m < 60) return `${m}m ago`;
	const h = Math.floor(m / 60);
	if (h < 24) return `${h}h ago`;
	const d = Math.floor(h / 24);
	if (d <= 7) return `${d}d ago`;
	return new Date(ts).toLocaleDateString();
}
