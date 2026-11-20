import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

// the link preview's URL carries a hash of the image, so apps that cache previews by URL fetch a re-cut card fresh;
// the path is relative to site/, where every build and dev command runs
const hash = createHash('sha256').update(readFileSync('public/og-image.png')).digest('hex').slice(0, 8);

export const ogImage = `https://vaquero.bitwise0x.com/og-image.png?v=${hash}`;
