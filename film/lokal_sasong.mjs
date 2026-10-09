/**
 * Gör säsongsfilmen lokalt, för att testa.
 *
 *   node film/lokal_sasong.mjs <utmapp> [säsong ...]     (t.ex. ha_2526)
 *
 * Utan säsong blir det den aktiva. FFMPEG och CHROMIUM som i lokal.mjs.
 */
import fs from 'node:fs';
import path from 'node:path';
import { ljudSasong } from './ljud_sasong.mjs';
import { renderaSida } from './rendera.mjs';
import { hamtaSasong, kontrolleraSasong } from './sasong.mjs';

const API = process.env.API_URL || 'https://loven-stats-api-324947473206.europe-west1.run.app';
const [, , ut, ...sasonger] = process.argv;

for (const season of sasonger.length ? sasonger : ['']) {
  const data = await hamtaSasong(API, season);
  const fel = kontrolleraSasong(data);
  if (fel.length) { console.log(season || 'aktiv', 'HOPPAD:', fel.join('; ')); continue; }
  const mapp = path.join(ut, season || 'aktiv');
  const t0 = Date.now();
  const { mp4, tider } = await renderaSida({
    sida: 'sasong.html', global: 'SASONG', data, ljud: ljudSasong,
    // Stillbilden: tabellen när loppet är klart och poängen ifyllda.
    posterTid: tider => tider.kurva - 0.3,
    mapp, ffmpeg: process.env.FFMPEG || 'ffmpeg', chromiumPath: process.env.CHROMIUM,
  });
  console.log(season || 'aktiv', 'gjord', mp4, `${tider.total.toFixed(1)} s film`, `${Math.round((Date.now() - t0) / 1000)} s rendering`, fs.statSync(mp4).size, 'B');
}
