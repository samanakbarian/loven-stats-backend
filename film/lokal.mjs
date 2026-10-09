/**
 * Gör filmer lokalt utan bucket, för att testa.
 *
 *   node film/lokal.mjs <utmapp> <game_id> [<game_id> ...]
 *
 * Kör samma kontroll och samma rendering som tjänsten. FFMPEG och
 * CHROMIUM pekar ut verktygen om de inte ligger i PATH.
 */
import fs from 'node:fs';
import path from 'node:path';
import { kontrollera, serieRad } from './kontroll.mjs';
import { renderaFilm } from './rendera.mjs';

const API = process.env.API_URL || 'https://loven-stats-api-324947473206.europe-west1.run.app';
const [, , ut, ...ids] = process.argv;
const hamta = async v => (await fetch(API + v)).json();

for (const id of ids) {
  const m = await hamta(`/api/v1/match/${id}`);
  const ar = String(m.date).slice(0, 4), man = Number(String(m.date).slice(5, 7));
  const start = man >= 8 ? Number(ar) : Number(ar) - 1;
  const liga = /Allsvenskan|HA/i.test(process.env.LIGA || '') ? 'HockeyAllsvenskan' : (process.env.LIGA || 'SHL');
  m.serie = serieRad(`${liga} ${start}/${String(start + 1).slice(2)}`);
  const fel = kontrollera(m);
  if (fel.length) { console.log(id, 'HOPPAD:', fel.join('; ')); continue; }
  const mapp = path.join(ut, String(id));
  const t0 = Date.now();
  const { mp4, tider } = await renderaFilm(m, { mapp, ffmpeg: process.env.FFMPEG || 'ffmpeg', chromiumPath: process.env.CHROMIUM });
  console.log(id, 'gjord', mp4, `${tider.total.toFixed(1)} s film`, `${Math.round((Date.now() - t0) / 1000)} s rendering`, fs.statSync(mp4).size, 'B');
}
