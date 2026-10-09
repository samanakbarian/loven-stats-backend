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
export async function renderaFilm(match, { mapp, ffmpeg = 'ffmpeg', chromiumPath } = {}) {
  fs.mkdirSync(mapp, { recursive: true });
  const rutor = path.join(mapp, 'rutor');
  fs.rmSync(rutor, { recursive: true, force: true });
  fs.mkdirSync(rutor);

  const browser = await chromium.launch(chromiumPath ? { executablePath: chromiumPath } : {});
  let tider;
  try {
    const sida = await browser.newPage({ viewport: { width: 720, height: 1280 } });
    await sida.addInitScript(`window.MATCH = ${JSON.stringify(match)}; window.RENDERA_RUTOR = true;`);
    await sida.goto('file://' + path.join(HAR, 'film.html'));
    await sida.evaluate(() => document.fonts.ready);
    tider = await sida.evaluate(() => window.TIDER);
    const skarm = await sida.$('#skarm');
    const antal = Math.round(tider.total * FPS);
    for (let i = 0; i < antal; i++) {
      await sida.evaluate(t => window.render(t), i / FPS);
      await skarm.screenshot({ path: path.join(rutor, `${String(i).padStart(4, '0')}.png`) });
    }
    // Stillbilden: slutresultatet, när "SLUT" står stilla.
    await sida.evaluate(t => window.render(t), tider.slut + 3);
    await skarm.screenshot({ path: path.join(mapp, 'poster.jpg'), type: 'jpeg', quality: 85 });
  } finally {
    await browser.close();
  }

  const wavFil = path.join(mapp, 'ljud.wav');
  fs.writeFileSync(wavFil, ljudspar(match, tider));
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
