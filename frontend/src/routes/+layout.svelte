<script lang="ts">
	import '../app.css';
	import { toasts, toast } from '$lib/toastStore';
	import { page } from '$app/stores';
	import { authState } from '$lib/auth.svelte';
	import { apiFetch, getApiBase } from '$lib/api';
	import { onMount } from 'svelte';
	import { navigating } from '$app/stores';
	import { assist } from '$lib/assist.svelte';
	import JaggaerAssist from '$lib/JaggaerAssist.svelte';

	let { children } = $props();

	let isLogin = $state(true);
	let email = $state('');
	let password = $state('');
	let loginError = $state('');
	let loading = $state(false);

	const NAV = [
		{ href: '/', label: 'Dashboard', match: (p: string) => p === '/' },
		{ href: '/contracts', label: 'Contracts', match: (p: string) => p.startsWith('/contracts') },
		{ href: '/risk', label: 'Risk', match: (p: string) => p.startsWith('/risk') },
		{ href: '/templates', label: 'Templates', match: (p: string) => p.startsWith('/templates') },
		{ href: '/calendar', label: 'Calendar', match: (p: string) => p.startsWith('/calendar') },
		{ href: '/vendors', label: 'Vendors', match: (p: string) => p.startsWith('/vendors') },
		{ href: '/assist', label: 'Assist', match: (p: string) => p.startsWith('/assist') },
		{ href: '/traces', label: 'Traces', match: (p: string) => p.startsWith('/traces') }
	];

	const dockActive = $derived(
		assist.open && assist.mode === 'docked' && !$page.url.pathname.startsWith('/assist')
	);

	onMount(async () => {
		// Light-only design language (CI). Keep the attribute so page-level
		// [data-theme="light"] overrides continue to apply.
		document.documentElement.setAttribute('data-theme', 'light');
		try {
			const statusRes = await apiFetch('/api/v1/auth/signup-status');
			if (statusRes.ok) {
				const statusData = await statusRes.json();
				authState.signupDisabled = statusData.signup_disabled;
			}
		} catch (e) {
			console.error('Failed to fetch signup status', e);
		}

		if (authState.token) {
			try {
				const meRes = await apiFetch('/api/v1/auth/me');
				if (meRes.ok) {
					const meData = await meRes.json();
					authState.setUser(meData);
				} else {
					authState.logout();
				}
			} catch (e) {
				console.error('Token validation failed', e);
				authState.logout();
			}
		}
		authState.initialized = true;
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		if (loading) return;
		loading = true;
		loginError = '';

		const endpoint = isLogin ? '/api/v1/auth/login' : '/api/v1/auth/signup';
		try {
			const res = await apiFetch(endpoint, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ email, password })
			});

			const data = await res.json();
			if (res.ok) {
				authState.setToken(data.access_token);
				authState.setUser(data.user);
				toast.success(isLogin ? 'Logged in successfully' : 'Account created successfully');
				email = '';
				password = '';
			} else {
				loginError = data.detail || 'Authentication failed.';
				toast.error(loginError);
			}
		} catch (err: any) {
			loginError = `Connection error — API not reachable at ${getApiBase()}.`;
			toast.error(loginError);
		} finally {
			loading = false;
		}
	}
</script>

{#if !authState.initialized}
	<div class="auth-container">
		<span class="spinner spinner-lg"></span>
	</div>
{:else if !authState.isAuthenticated}
	<div class="auth-container">
		<div class="auth-card card animate-fadeIn">
			<div class="auth-header">
				<div class="brand-mark brand-mark-lg" style="margin: 0 auto 16px;" aria-label="ContractsPulse logo">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
						<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z"/>
						<path d="M14 3v4h4"/>
						<path d="M8 13h2l1.5-3 2 6 1.5-3H16"/>
					</svg>
				</div>
				<h2>{isLogin ? 'Log in to ContractsPulse' : 'Create an Account'}</h2>
				<p>{isLogin ? 'Enter your credentials to access your workspace.' : 'Register a new account to get started.'}</p>
			</div>

			{#if !authState.signupDisabled}
				<div class="auth-tabs" role="tablist" aria-label="Authentication Mode">
					<button
						type="button"
						role="tab"
						aria-selected={isLogin}
						class="auth-tab"
						class:active={isLogin}
						onclick={() => { isLogin = true; loginError = ''; }}
					>
						Log In
					</button>
					<button
						type="button"
						role="tab"
						aria-selected={!isLogin}
						class="auth-tab"
						class:active={!isLogin}
						onclick={() => { isLogin = false; loginError = ''; }}
					>
						Sign Up
					</button>
				</div>
			{:else if !isLogin}
				<!-- Fallback if user somehow got to signup page when disabled -->
				{ (isLogin = true, '') }
			{/if}

			<form onsubmit={handleSubmit} class="auth-form">
				<div class="form-group">
					<label for="email">Email</label>
					<input
						type="email"
						id="email"
						bind:value={email}
						placeholder="you@domain.com"
						class="input-field"
						required
					/>
				</div>
				<div class="form-group">
					<label for="password">Password</label>
					<input
						type="password"
						id="password"
						bind:value={password}
						placeholder="••••••••"
						class="input-field"
						required
						minlength={isLogin ? undefined : 8}
					/>
				</div>

				{#if loginError}
					<div class="error-msg">{loginError}</div>
				{/if}

				<button type="submit" class="btn btn-primary btn-block" style="margin-top: 8px;" disabled={loading}>
					{#if loading}
						<span class="spinner spinner-sm" style="border-top-color: #fff;"></span>
					{:else}
						Continue
					{/if}
				</button>
			</form>
		</div>
	</div>
{:else}
	<div class="app-shell" class:dock-active={dockActive}>
		<header class="topbar">
			<a href="/" class="brand">
				<span class="brand-mark" aria-label="ContractsPulse logo">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
						<path d="M14 3H7a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V7z"/>
						<path d="M14 3v4h4"/>
						<path d="M8 13h2l1.5-3 2 6 1.5-3H16"/>
					</svg>
				</span>
				<span class="brand-name">ContractsPulse</span>
			</a>

			<nav class="pill-nav" aria-label="Primary">
				{#each NAV as item (item.href)}
					<a href={item.href} class="pill-link" class:active={item.match($page.url.pathname)}>
						{item.label}
					</a>
				{/each}
			</nav>

			<div class="topbar-right">
				{#if authState.user}
					<div class="user-chip" title={authState.user.email}>
						<span class="user-avatar">{authState.user.email[0].toUpperCase()}</span>
						<span class="user-email">{authState.user.email}</span>
					</div>
				{/if}
				<button type="button" class="signout-btn" onclick={() => authState.logout()} title="Sign out" aria-label="Sign out">
					<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
				</button>
			</div>
		</header>

		<main class="main-content">
			{@render children()}
		</main>
	</div>

	{#if $navigating}
		<div class="nav-loading-bar"></div>
	{/if}

	<JaggaerAssist />

	<!-- Toast Container -->
	<div class="toast-viewport">
		{#each $toasts as toast (toast.id)}
			<div class="toast" class:toast-error={toast.type === 'error'} class:toast-success={toast.type === 'success'}>
				<div class="toast-content">
					{#if toast.type === 'success'}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
					{:else if toast.type === 'error'}
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
					{:else}
						<span class="spinner spinner-md"></span>
					{/if}
					<span>{toast.message}</span>
				</div>
			</div>
		{/each}
	</div>
{/if}

<style>
	:global(.toast-viewport) {
		position: fixed;
		top: 70px;
		right: 24px;
		display: flex;
		flex-direction: column;
		gap: 8px;
		z-index: 200;
		pointer-events: none;
	}

	:global(.toast) {
		background: var(--bg-panel);
		border: 1px solid var(--border-subtle);
		color: var(--text-primary);
		padding: 12px 16px;
		border-radius: 12px;
		font-size: 13px;
		font-weight: 500;
		box-shadow: var(--shadow-lg);
		pointer-events: auto;
		animation: slideIn 220ms var(--ease-out) forwards;
		transform-origin: top center;
	}

	:global(.toast-error) {
		border-color: var(--glow-critical-border);
		box-shadow: 0 8px 30px var(--glow-critical);
		color: var(--color-critical-text);
	}

	:global(.toast-success) {
		border-color: var(--glow-low-border);
		box-shadow: 0 8px 30px var(--glow-low);
		color: var(--color-low-text);
	}

	:global(.toast-content) {
		display: flex;
		align-items: center;
		gap: 10px;
	}

	@keyframes slideIn {
		from { opacity: 0; transform: translateY(-12px) scale(0.95); }
		to { opacity: 1; transform: translateY(0) scale(1); }
	}

	/* ------------------------------------------------------------
	   App shell — CI gradient topbar + content
	   ------------------------------------------------------------- */
	.app-shell {
		display: flex;
		flex-direction: column;
		height: 100vh;
		overflow: hidden;
		transition: padding-right 260ms var(--ease-drawer);
	}
	.app-shell.dock-active {
		padding-right: 400px;
	}

	.topbar {
		position: sticky;
		top: 0;
		z-index: 100;
		height: 56px;
		flex-shrink: 0;
		display: flex;
		align-items: center;
		gap: 18px;
		padding: 0 18px;
		background: var(--brand-gradient);
		color: #fff;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 10px;
		text-decoration: none;
		color: #fff;
		flex-shrink: 0;
	}
	.brand-mark {
		width: 28px;
		height: 28px;
		border-radius: 8px;
		background: rgba(255, 255, 255, 0.18);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		color: #fff;
	}
	.brand-mark svg {
		width: 16px;
		height: 16px;
	}
	.brand-name {
		font-size: 14.5px;
		font-weight: 700;
		letter-spacing: -0.01em;
		white-space: nowrap;
	}

	.pill-nav {
		display: flex;
		align-items: center;
		gap: 4px;
		flex: 1;
		justify-content: center;
		overflow-x: auto;
		scrollbar-width: none;
	}
	.pill-nav::-webkit-scrollbar {
		display: none;
	}
	.pill-link {
		padding: 6px 16px;
		border-radius: var(--radius-pill);
		color: rgba(255, 255, 255, 0.85);
		text-decoration: none;
		font-size: 13px;
		font-weight: 500;
		white-space: nowrap;
		transition: background 150ms ease, color 150ms ease;
	}
	.pill-link:hover {
		background: rgba(255, 255, 255, 0.12);
		color: #fff;
	}
	.pill-link.active {
		background: rgba(255, 255, 255, 0.2);
		color: #fff;
		font-weight: 600;
	}

	.topbar-right {
		display: flex;
		align-items: center;
		gap: 8px;
		flex-shrink: 0;
	}
	.user-chip {
		display: flex;
		align-items: center;
		gap: 8px;
		background: rgba(255, 255, 255, 0.12);
		border-radius: var(--radius-pill);
		padding: 4px 12px 4px 4px;
		max-width: 220px;
	}
	.user-avatar {
		width: 24px;
		height: 24px;
		border-radius: 999px;
		background: rgba(255, 255, 255, 0.25);
		color: #fff;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		font-size: 11px;
		font-weight: 700;
		flex-shrink: 0;
	}
	.user-email {
		font-size: 12px;
		color: rgba(255, 255, 255, 0.9);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.signout-btn {
		width: 30px;
		height: 30px;
		border-radius: 999px;
		border: none;
		background: rgba(255, 255, 255, 0.12);
		color: rgba(255, 255, 255, 0.9);
		display: inline-flex;
		align-items: center;
		justify-content: center;
		cursor: pointer;
		transition: background 150ms ease;
	}
	.signout-btn:hover {
		background: rgba(255, 255, 255, 0.24);
	}

	.main-content {
		flex: 1;
		overflow-y: auto;
		display: flex;
		flex-direction: column;
	}

	.nav-loading-bar {
		position: fixed;
		left: 0;
		right: 0;
		top: 0;
		height: 2px;
		background: linear-gradient(90deg, rgba(226, 43, 131, 0), rgba(226, 43, 131, 0.9), rgba(226, 43, 131, 0));
		animation: shimmer 800ms linear infinite;
		z-index: 220;
	}
	@keyframes shimmer {
		0% { transform: translateX(-30%); }
		100% { transform: translateX(30%); }
	}

	@media (max-width: 900px) {
		.brand-name { display: none; }
		.user-chip { display: none; }
		.app-shell.dock-active { padding-right: 0; }
	}

	/* ------------------------------------------------------------
	   Auth screen
	   ------------------------------------------------------------- */
	.auth-container {
		display: flex;
		align-items: center;
		justify-content: center;
		height: 100vh;
	}

	.auth-card {
		width: 100%;
		max-width: 380px;
		padding: 36px 32px;
	}

	.auth-header {
		text-align: center;
		margin-bottom: 24px;
	}

	.auth-header h2 {
		font-size: 18px;
		margin-bottom: 8px;
	}

	.auth-header p {
		color: var(--text-secondary);
		font-size: 13px;
	}

	:global(.brand-mark-lg) {
		width: 52px;
		height: 52px;
		border-radius: 14px;
		background: var(--brand-gradient) !important;
		color: #fff;
		display: flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 8px 24px rgba(83, 0, 206, 0.3);
	}

	:global(.brand-mark-lg svg) {
		width: 28px;
		height: 28px;
	}

	.auth-tabs {
		display: flex;
		background: var(--bg-hover);
		border: 1px solid var(--border-subtle);
		border-radius: var(--radius-pill);
		padding: 3px;
		margin-bottom: 24px;
	}

	.auth-tab {
		flex: 1;
		background: transparent;
		border: none;
		color: var(--text-secondary);
		font-size: 13px;
		font-weight: 500;
		padding: 7px 0;
		border-radius: var(--radius-pill);
		cursor: pointer;
		transition: background 150ms var(--ease-out), color 150ms var(--ease-out), transform 100ms ease;
		user-select: none;
	}

	.auth-tab:active {
		transform: scale(0.97);
	}

	.auth-tab.active {
		background: #ffffff;
		color: var(--text-primary);
		box-shadow: var(--shadow-sm);
	}

	.form-group {
		margin-bottom: 16px;
	}

	.form-group label {
		display: block;
		font-size: 12px;
		font-weight: 500;
		color: var(--text-secondary);
		margin-bottom: 6px;
	}

	.input-field {
		width: 100%;
		padding: 11px 14px;
		background: var(--bg-hover);
		border: 1.5px solid var(--border-subtle);
		border-radius: var(--radius-md);
		color: var(--text-primary);
		font-size: 13px;
		transition: border-color 150ms var(--ease-out), background 150ms ease, box-shadow 150ms ease;
		outline: none;
	}

	.input-field:focus {
		border-color: var(--accent-primary);
		background: #ffffff;
		box-shadow: var(--ring);
	}

	.btn-block {
		width: 100%;
	}

	.error-msg {
		color: var(--color-critical-text);
		font-size: 12px;
		margin-top: 8px;
		text-align: center;
	}
</style>
