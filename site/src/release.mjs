// shared by the build (download.mjs) and the page, which asks again on every visit so a release made after the
// site was built, or still cached in the browser, is what the download links carry. The repo also holds model
// releases, so GitHub's own "latest" is not used, as in the app's update check.
export const RELEASES_API = 'https://api.github.com/repos/BitWise-0x/Vaquero/releases?per_page=30';

/** The newest app release's DMG among GitHub's releases, newest first, or null when none has one. */
export function newestDmgUrl(releases) {
	for (const release of releases) {
		if (!release.tag_name?.startsWith('v') || release.draft || release.prerelease) continue;
		// a release whose DMG is still being built is passed over for the one before it
		const dmg = release.assets?.find((asset) => asset.name.endsWith('.dmg'));
		if (dmg) return dmg.browser_download_url;
	}
	return null;
}
