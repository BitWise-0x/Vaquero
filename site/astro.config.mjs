import { defineConfig } from 'astro/config';
import { satteri } from '@astrojs/markdown-satteri';
import starlight from '@astrojs/starlight';

// the docs are the repo's docs/*.md, written with GitHub-style links (install.md, using.md#file-types);
// this rewrites them to the site's /docs/<page>/ routes
const docsLinks = {
	name: 'docs-links',
	link(node, ctx) {
		const m = /^([\w-]+)\.md(#.*)?$/.exec(node.url);
		if (m) ctx.setProperty(node, 'url', `/docs/${m[1]}/${m[2] ?? ''}`);
	},
};

export default defineConfig({
	site: 'https://vaquero.bitwise0x.com',
	markdown: { processor: satteri({ mdastPlugins: [docsLinks], features: { smartPunctuation: false } }) },
	integrations: [
		starlight({
			title: 'Vaquero',
			description: 'Find and round up every photo of one person, by face, on your Mac.',
			logo: { src: '../assets/Vaquero.png', alt: 'Vaquero' },
			favicon: '/favicon.png',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/BitWise-0x/Vaquero' }],
			editLink: { baseUrl: 'https://github.com/BitWise-0x/Vaquero/edit/main/site/' },
			customCss: ['./src/styles/starlight.css'],
			sidebar: [
				{
					label: 'Docs',
					items: [
						'docs/install',
						'docs/using',
						'docs/reference-photos',
						'docs/files',
						'docs/how-it-works',
						'docs/troubleshooting',
					],
				},
			],
		}),
	],
});
