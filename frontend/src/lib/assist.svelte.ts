import { apiFetch } from './api';

export type AssistAction = {
	type: 'open_contract' | 'view_clause' | 'view_deviations' | 'copy_redline' | 'draft_email' | string;
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

export type AssistMeta = { route?: string; query_scope?: string; conversation_mode?: string };

export type AssistMsg = {
	role: 'user' | 'assistant';
	content: string;
	error?: boolean;
	sources?: AssistSource[];
	actions?: AssistAction[];
	suggested?: string[];
	meta?: AssistMeta;
};

export type Conversation = {
	id: string;
	title: string;
	updatedAt: number;
	messages: AssistMsg[];
	sessionId?: string;
};

const CONVOS_KEY = 'cp_assist_convos';
const MODE_KEY = 'cp_assist_mode';
const browser = typeof window !== 'undefined';

function uid(): string {
	return Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
}

export const STARTER_QUESTIONS = [
	'How many contracts expire before 2026?',
	'Compare termination clauses across all contracts',
	'Which contracts have auto-renewal?'
];

class AssistState {
	open = $state(false);
	/** floater | docked — fullscreen is the /assist route */
	mode = $state<'floater' | 'docked'>('floater');
	conversations = $state<Conversation[]>([]);
	activeId = $state<string | null>(null);
	loading = $state(false);

	constructor() {
		if (browser) {
			try {
				const raw = localStorage.getItem(CONVOS_KEY);
				if (raw) this.conversations = JSON.parse(raw);
			} catch {
				this.conversations = [];
			}
			const m = localStorage.getItem(MODE_KEY);
			if (m === 'docked' || m === 'floater') this.mode = m;
			if (this.conversations.length > 0) {
				this.activeId = [...this.conversations].sort((a, b) => b.updatedAt - a.updatedAt)[0].id;
			}
		}
	}

	get sorted(): Conversation[] {
		return [...this.conversations].sort((a, b) => b.updatedAt - a.updatedAt);
	}

	get active(): Conversation | null {
		return this.conversations.find((c) => c.id === this.activeId) ?? null;
	}

	persist() {
		if (!browser) return;
		try {
			// keep the 30 most recent, cap stored messages per convo
			const slim = this.sorted.slice(0, 30).map((c) => ({ ...c, messages: c.messages.slice(-60) }));
			localStorage.setItem(CONVOS_KEY, JSON.stringify(slim));
		} catch {
			/* storage full — ignore */
		}
	}

	setMode(mode: 'floater' | 'docked') {
		this.mode = mode;
		this.open = true;
		if (browser) localStorage.setItem(MODE_KEY, mode);
	}

	toggle() {
		this.open = !this.open;
	}

	newConversation(): Conversation {
		const convo: Conversation = { id: uid(), title: 'New conversation', updatedAt: Date.now(), messages: [] };
		this.conversations = [convo, ...this.conversations];
		this.activeId = convo.id;
		this.persist();
		return convo;
	}

	setActive(id: string) {
		this.activeId = id;
	}

	deleteConversation(id: string) {
		this.conversations = this.conversations.filter((c) => c.id !== id);
		if (this.activeId === id) this.activeId = this.sorted[0]?.id ?? null;
		this.persist();
	}

	private ensureActive(): Conversation {
		return this.active ?? this.newConversation();
	}

	private touch(convo: Conversation) {
		convo.updatedAt = Date.now();
		// reassign to trigger reactivity on the array
		this.conversations = [...this.conversations];
		this.persist();
	}

	/** Remove a trailing errored assistant turn (used by resend). */
	popLastAssistantIfError(convo: Conversation) {
		const last = convo.messages[convo.messages.length - 1];
		if (last && last.role === 'assistant' && last.error) {
			convo.messages = convo.messages.slice(0, -1);
		}
	}

	async send(text: string): Promise<void> {
		const q = (text || '').trim();
		if (!q || this.loading) return;
		const convo = this.ensureActive();
		if (convo.messages.length === 0) {
			convo.title = q.length > 44 ? q.slice(0, 44) + '…' : q;
		}
		const history = convo.messages
			.filter((m) => !m.error)
			.map((m) => ({ role: m.role, content: m.content }));
		convo.messages = [...convo.messages, { role: 'user', content: q }];
		this.touch(convo);
		this.loading = true;
		try {
			const res = await apiFetch('/api/v1/assistant/chat', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ question: q, history, session_id: convo.sessionId })
			});
			const json = await res.json().catch(() => ({}));
			if (!res.ok) throw new Error(json?.detail || 'Assistant request failed');
			if (json.session_id) convo.sessionId = json.session_id;
			convo.messages = [
				...convo.messages,
				{
					role: 'assistant',
					content: json.answer || 'I could not generate an answer.',
					sources: json.sources || [],
					actions: json.actions || [],
					suggested: json.suggested_questions || [],
					meta: {
						route: json.route,
						query_scope: json.query_scope,
						conversation_mode: json.conversation_mode
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
		this.popLastAssistantIfError(convo);
		const lastUser = [...convo.messages].reverse().find((m) => m.role === 'user');
		if (!lastUser) return;
		// drop the trailing user turn too — send() re-adds it
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
