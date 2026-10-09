/**
 * Renderar en matchfilm: bildrutor ur film.html, ljud ur ljud.mjs, ihop med
 * ffmpeg till en mp4. Plus en stillbild till förhandsvisningen.
 *
 * Filmen är en funktion av tiden (window.render(t)), så den renderas ruta för
 * ruta i stället för att spelas in i realtid: samma data ger alltid samma
 * film, oavsett hur belastad maskinen är.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { ljudspar } from './ljud.mjs';

const HAR = path.dirname(fileURLToPath(import.meta.url));
const FPS = 25;

function kor(cmd, args) {
  return new Promise((ok, fel) => {
    const p = spawn(cmd, args, { stdio: ['ignore', 'ignore', 'pipe'] });
    let err = '';
    p.stderr.on('data', d => { err += d; });
    p.on('close', kod => (kod === 0 ? ok() : fel(new Error(`${cmd} avslutades med ${kod}: ${err.slice(-400)}`))));
  });
}

/**
 * match: svaret från /api/v1/match/{id}, plus `serie` ("SHL 26/27").
 * Returnerar sökvägarna till filmen och stillbilden, och filmens tidslinje.
 */
export function renderaFilm(match, { mapp, ffmpeg = 'ffmpeg', chromiumPath } = {}) {
  return renderaSida({
    sida: 'film.html', global: 'MATCH', data: match, ljud: ljudspar,
    // Stillbilden: slutresultatet, när "SLUT" står stilla.
    posterTid: tider => tider.slut + 3,
    mapp, ffmpeg, chromiumPath,
  });
}

/**
 * En film ur en HTML-sida med window.render(t) och window.TIDER. Datan läggs
 * i window[global] innan sidan laddas; ljud(data, tider) ger en WAV.
 */
export async function renderaSida({ sida, global, data, ljud, posterTid, mapp, ffmpeg = 'ffmpeg', chromiumPath }) {
  fs.mkdirSync(mapp, { recursive: true });
  const rutor = path.join(mapp, 'rutor');
  fs.rmSync(rutor, { recursive: true, force: true });
  fs.mkdirSync(rutor);

  const browser = await chromium.launch(chromiumPath ? { executablePath: chromiumPath } : {});
  let tider;
  try {
    const flik = await browser.newPage({ viewport: { width: 720, height: 1280 } });
    await flik.addInitScript(`window[${JSON.stringify(global)}] = ${JSON.stringify(data)}; window.RENDERA_RUTOR = true;`);
    await flik.goto('file://' + path.join(HAR, sida));
    await flik.evaluate(() => document.fonts.ready);
    tider = await flik.evaluate(() => window.TIDER);
    const skarm = await flik.$('#skarm');
    const antal = Math.round(tider.total * FPS);
    for (let i = 0; i < antal; i++) {
      await flik.evaluate(t => window.render(t), i / FPS);
      await skarm.screenshot({ path: path.join(rutor, `${String(i).padStart(4, '0')}.png`) });
    }
    await flik.evaluate(t => window.render(t), posterTid(tider));
    await skarm.screenshot({ path: path.join(mapp, 'poster.jpg'), type: 'jpeg', quality: 85 });
  } finally {
    await browser.close();
  }

  const wavFil = path.join(mapp, 'ljud.wav');
  fs.writeFileSync(wavFil, ljud(data, tider));
  const mp4 = path.join(mapp, 'film.mp4');
  await kor(ffmpeg, [
    '-hide_banner', '-loglevel', 'error', '-y',
    '-framerate', String(FPS), '-i', path.join(rutor, '%04d.png'),
    '-i', wavFil,
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20', '-preset', 'medium',
    '-c:a', 'aac', '-b:a', '128k', '-shortest', '-movflags', '+faststart',
    mp4,
  ]);
  fs.rmSync(rutor, { recursive: true, force: true });
  return { mp4, poster: path.join(mapp, 'poster.jpg'), tider };
}
