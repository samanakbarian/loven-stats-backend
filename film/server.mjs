/**
 * Matchfilmerna. En Cloud Run-tjänst som schemaläggaren anropar efter varje
 * skörd (GET /kor). Den går igenom säsongens senaste spelade matcher, och för
 * varje match som är färdig och har ändrats sedan förra filmen:
 *
 *   1. hämtar matchrapporten ur API:t — samma data som sidan visar
 *   2. stämmer av den (kontroll.mjs); stämmer den inte blir det ingen film
 *   3. renderar filmen (rendera.mjs)
 *   4. lägger mp4, stillbild och en liten JSON i den publika bucketen
 *
 * "Ändrats" avgörs av ett fingeravtryck av matchdatan och av filmens egna
 * filer: rättar Swehockey protokollet, eller ändras filmen, görs den om.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Storage } from '@google-cloud/storage';
import { kontrollera, serieRad } from './kontroll.mjs';
import { renderaFilm } from './rendera.mjs';

const HAR = path.dirname(fileURLToPath(import.meta.url));
const API = process.env.API_URL || 'https://loven-stats-api-324947473206.europe-west1.run.app';
const BUCKET = process.env.FILM_BUCKET;
const DAGAR = Number(process.env.FILM_DAGAR || 14);
const PORT = Number(process.env.PORT || 8080);

// Filmens version: ändras film.html eller ljudet görs alla filmer om.
const VERSION = crypto.createHash('sha256')
  .update(fs.readFileSync(path.join(HAR, 'film.html')))
  .update(fs.readFileSync(path.join(HAR, 'ljud.mjs')))
  .digest('hex').slice(0, 12);

async function hamta(vag) {
  const r = await fetch(API + vag, { headers: { 'Accept-Encoding': 'gzip' } });
  if (!r.ok) throw new Error(`${vag}: HTTP ${r.status}`);
  return r.json();
}

/** Det i matchrapporten som syns i filmen. Ändras det, ändras filmen. */
function fingeravtryck(m) {
  const ur = {
    result: m.result, period_results: m.period_results, venue: m.venue, spectators: m.spectators,
    goals: (m.goals || []).map(g => [g.time, g.team_code, g.scorer, g.score_state]),
    penalties: (m.penalties || []).map(p => [p.time, p.team_code, p.player, p.minutes]),
    shots: ['ours', 'theirs'].map(s => [m.teams?.[s]?.shots, m.teams?.[s]?.shots_by_period]),
    best: (m.best || []).map(b => [b.name, b.score]),
    serie: m.serie, version: VERSION,
  };
  return crypto.createHash('sha256').update(JSON.stringify(ur)).digest('hex').slice(0, 16);
}

let pagar = false;

export async function kor({ gameId, alla, bucket = new Storage().bucket(BUCKET), ffmpeg = process.env.FFMPEG || 'ffmpeg', chromiumPath = process.env.CHROMIUM } = {}) {
  const stat = await hamta('/api/v1/statistics');
  const serie = serieRad(stat.season);
  const grans = new Date(Date.now() - DAGAR * 86400000).toISOString().slice(0, 10);
  let matcher = (stat.games || []).filter(g => g.game_id && /^\s*\d+\s*-\s*\d+/.test(g.result || ''));
  if (gameId) matcher = matcher.filter(g => String(g.game_id) === String(gameId));
  else if (!alla) matcher = matcher.filter(g => String(g.match_date).slice(0, 10) >= grans);

  const rapport = [];
  for (const g of matcher) {
    const id = g.game_id;
    try {
      const m = { ...(await hamta(`/api/v1/match/${id}`)), serie };
      const fel = kontrollera(m);
      if (fel.length) { rapport.push({ id, status: 'hoppad', fel }); continue; }
      const avtryck = fingeravtryck(m);
      const meta = bucket.file(`film/${id}.json`);
      const [finns] = await meta.exists();
      if (finns) {
        const [inne] = await meta.download();
        if (JSON.parse(inne.toString()).hash === avtryck) { rapport.push({ id, status: 'oförändrad' }); continue; }
      }
      const mapp = fs.mkdtempSync(path.join(os.tmpdir(), `film-${id}-`));
      const t0 = Date.now();
      const { mp4, poster, tider } = await renderaFilm(m, { mapp, ffmpeg, chromiumPath });
      const cache = 'public, max-age=300';
      await bucket.upload(mp4, { destination: `film/${id}.mp4`, metadata: { contentType: 'video/mp4', cacheControl: cache } });
      await bucket.upload(poster, { destination: `film/${id}.jpg`, metadata: { contentType: 'image/jpeg', cacheControl: cache } });
      // JSON sist: den är signalen till sidan att filmen finns.
      await meta.save(JSON.stringify({
        game_id: id, hash: avtryck, version: VERSION, generated_at: new Date().toISOString(),
        date: m.date, home_team: m.home_team, away_team: m.away_team, result: m.result,
        duration: Math.round(tider.total * 10) / 10,
      }), { contentType: 'application/json', metadata: { cacheControl: 'public, max-age=60' } });
      fs.rmSync(mapp, { recursive: true, force: true });
      rapport.push({ id, status: 'gjord', sek: Math.round((Date.now() - t0) / 1000) });
    } catch (e) {
      rapport.push({ id, status: 'fel', fel: [String(e.message || e).slice(0, 300)] });
    }
  }
  return { status: 'ok', version: VERSION, serie, matcher: matcher.length, rapport };
}

// Startas bara som tjänst, inte när testerna importerar kor().
if (process.argv[1] === fileURLToPath(import.meta.url)) http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  const svara = (kod, data) => {
    res.writeHead(kod, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(data));
  };
  if (url.pathname === '/health') return svara(200, { status: 'ok', version: VERSION });
  if (url.pathname !== '/kor') return svara(404, { status: 'not_found' });
  if (!BUCKET) return svara(500, { status: 'error', error: 'FILM_BUCKET saknas' });
  // En körning i taget. Schemaläggaren och en manuell körning ska inte rendera
  // samma match samtidigt.
  if (pagar) return svara(409, { status: 'busy' });
  pagar = true;
  try {
    const ut = await kor({ gameId: url.searchParams.get('game_id'), alla: url.searchParams.get('alla') === '1' });
    console.log(JSON.stringify(ut));
    svara(200, ut);
  } catch (e) {
    console.error(e);
    svara(500, { status: 'error', error: String(e.message || e) });
  } finally {
    pagar = false;
  }
}).listen(PORT, () => console.log(`matchfilm lyssnar på ${PORT}, version ${VERSION}`));
