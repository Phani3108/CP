import adapterVercel from '@sveltejs/adapter-vercel';
import adapterStatic from '@sveltejs/adapter-static';

// STATIC_BUILD=1 → self-contained SPA (served by FastAPI / any static host — used for the
// single-origin localhost build). Otherwise the explicit Vercel adapter for a frontend-only
// Vercel deploy (the backend runs on Cloud Run and /api is rewritten there — see
// deploy/DEPLOY-VERCEL.md). Explicit adapter avoids adapter-auto's "could not detect" ambiguity.
const adapter =
	process.env.STATIC_BUILD === '1'
		? adapterStatic({ fallback: 'index.html' })
		: adapterVercel();

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
		runes: ({ filename }) => (filename.split(/[/\\\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter
	}
};

export default config;
