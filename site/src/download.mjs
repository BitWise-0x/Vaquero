// the download link names the newest app release's DMG, which carries its version, so it is resolved at build time;
// the release workflow rebuilds the site once a new DMG is attached. The repo also holds model releases, so GitHub's
// own "latest" is not used, as in the app's update check.
const repo = 'https://github.com/BitWise-0x/Vaquero';
const fallback = `${repo}/releases/latest`;

async function newestDmg() {
	const headers = { Accept: 'application/vnd.github+json' };
	if (process.env.GITHUB_TOKEN) headers.Authorization = `Bearer ${process.env.GITHUB_TOKEN}`;
	const response = await fetch('https://api.github.com/repos/BitWise-0x/Vaquero/releases?per_page=30', {
		headers,
		signal: AbortSignal.timeout(15000),
	});
	if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
	for (const release of await response.json()) {
		if (!release.tag_name?.startsWith('v') || release.draft || release.prerelease) continue;
		// a release whose DMG is still being built is passed over for the one before it
		const dmg = release.assets?.find((asset) => asset.name.endsWith('.dmg'));
		if (dmg) return dmg.browser_download_url;
	}
	throw new Error('no app release has a DMG');
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
