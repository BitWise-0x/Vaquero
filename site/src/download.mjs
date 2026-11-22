// the download link as built: the newest app release's DMG, which carries its version. The release workflow
// rebuilds the site once a new DMG is attached, and each page asks GitHub again when it loads (SocialIcons.astro).
import { RELEASES_API, newestDmgUrl } from './release.mjs';

const repo = 'https://github.com/BitWise-0x/Vaquero';
const fallback = `${repo}/releases/latest`;

async function newestDmg() {
	const headers = { Accept: 'application/vnd.github+json' };
	if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
	const response = await fetch(RELEASES_API, { headers, signal: AbortSignal.timeout(15000) });
	if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
	const url = newestDmgUrl(await response.json());
	if (!url) throw new Error('no app release has a DMG');
	return url;
}

async function resolve() {
	try {
		return await newestDmg();
	} catch (error) {
		console.warn(`download link falls back to ${fallback}: ${error.message}`);
		return fallback;
	}
}

export const download = await resolve();
