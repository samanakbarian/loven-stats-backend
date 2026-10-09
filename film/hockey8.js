/**
 * Åttabitarsscenen i säsongsfilmen: Löven mot seriesnittet, som ett gammalt
 * tv-spel. Varje mått är ett anfall. Är Löven bättre än snittet blir det mål,
 * annars räddar målvakten, och är det lika tar pucken i stolpen.
 *
 * Ritas på en canvas i 180 x 320 som skalas upp fyra gånger utan utjämning,
 * så varje bildpunkt blir ett tydligt block. Som resten av filmen är allt en
 * funktion av tiden.
 */
// Egen räckvidd: sidans skript har egna ease, mitt och text.
(() => {
const FONT = {"A": ".###.#...##...#######...##...##...#", "B": "####.#...##...#####.#...##...#####.", "C": ".###.#...##....#....#....#...#.###.", "D": "####.#...##...##...##...##...#####.", "E": "######....#....####.#....#....#####", "F": "######....#....####.#....#....#....", "G": ".###.#...##....#.####...##...#.####", "H": "#...##...##...#######...##...##...#", "I": ".###...#....#....#....#....#...###.", "J": "..###...#....#....#....#.#..#..##..", "K": "#...##..#.#.#..##...#.#..#..#.#...#", "L": "#....#....#....#....#....#....#####", "M": "#...###.###.#.##.#.##...##...##...#", "N": "#...##...###..##.#.##..###...##...#", "O": ".###.#...##...##...##...##...#.###.", "P": "####.#...##...#####.#....#....#....", "Q": ".###.#...##...##...##.#.##..#..##.#", "R": "####.#...##...#####.#.#..#..#.#...#", "S": ".#####....#.....###.....#....#####.", "T": "#####..#....#....#....#....#....#..", "U": "#...##...##...##...##...##...#.###.", "V": "#...##...##...##...##...#.#.#...#..", "W": "#...##...##...##.#.##.#.##.#.#.#.#.", "X": "#...##...#.#.#...#...#.#.#...##...#", "Y": "#...##...#.#.#...#....#....#....#..", "Z": "#####....#...#...#...#...#....#####", "Å": "..#...#.#...#...###.#...#######...#", "Ä": "#...#......###.#...#######...##...#", "Ö": "#...#......###.#...##...##...#.###.", "0": ".###.#...##..###.#.###..##...#.###.", "1": "..#...##....#....#....#....#...###.", "2": ".###.#...#....#...#...#...#...#####", "3": "####.....#....#.###.....#....#####.", "4": "...#...##..#.#.#..#.#####...#....#.", "5": "######....####.....#....##...#.###.", "6": ".###.#....#....####.#...##...#.###.", "7": "#####....#...#...#...#....#....#...", "8": ".###.#...##...#.###.#...##...#.###.", "9": ".###.#...##...#.####....#....#.###.", ",": "...........................#...#...", ".": "................................#..", "-": "................###................", "!": "..#....#....#....#....#.........#..", "%": "##..###.#....#...#...#....#.###..##", "/": "....#...#....#...#...#....#...#....", ":": ".......#...................#.......", "·": ".................#................."};

const P = {
  svart: '#000000', vit: '#fcfcfc', is: '#d8ecfc', isSkugga: '#b8d4f0', rod: '#d82800', bla: '#0058f8',
  morkbla: '#0000a8', gul: '#f8b800', gron: '#00a844', ljusgron: '#58d854', gra: '#7c7c7c', ljusgra: '#bcbcbc',
  hud: '#fca044', brun: '#a85000', cyan: '#3cbcfc',
};

const SKRIDSKO = [
  ['..LL.LL.', '..LL.LL.', '.KK..KK.'],
  ['..LL..LL', '.LL...LL', 'KK....KK'],
  ['.LL..LL.', 'LL..LL..', 'KK..KK..'],
];
const SPELARE = ['..HHHH..', '.HHHHHH.', '..HHHH..', '.JJJJJJ.', 'JJJJJJJJ', 'JJSSSSJJ', 'JJJJJJJJ', '.JJJJJJ.', '..BB.BB.'];
const MALVAKT = ['...HHHH...', '..HMMMMH..', '...HHHH...', '.JJJJJJJJ.', 'JJJJJJJJJJ', 'JJSSSSSSJJ', 'PPJJJJJJPP', 'PP.BBBB.PP', 'PP.BB.BB.PP', 'PP......PP', 'PPP....PPP'];

function lag(farg) {
  return { H: farg.hjalm, J: farg.troja, S: farg.rand, B: P.svart, L: farg.troja, K: P.ljusgra, M: P.gra, P: P.vit };
}
const LOVEN = lag({ hjalm: P.gul, troja: P.gron, rand: P.gul });
const SERIEN = lag({ hjalm: P.vit, troja: P.gra, rand: P.vit });

let ctx;
const px = (x, y, c) => { ctx.fillStyle = c; ctx.fillRect(Math.round(x), Math.round(y), 1, 1); };
const ruta = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h)); };

function sprite(rader, palett, x, y) {
  rader.forEach((r, j) => { for (let i = 0; i < r.length; i++) if (r[i] !== '.') px(x + i, y + j, palett[r[i]]); });
}

/** Text i pixeltypsnittet; ett tecken är 6 punkter brett. */
function text(s, x, y, c, skala = 1) {
  s = String(s).toUpperCase();
  for (let k = 0; k < s.length; k++) {
    const g = FONT[s[k]];
    if (!g) continue;
    for (let j = 0; j < 7; j++) for (let i = 0; i < 5; i++) {
      if (g[j * 5 + i] === '#') ruta(x + (k * 6 + i) * skala, y + j * skala, skala, skala, c);
    }
  }
}
const bredd = (s, skala = 1) => (String(s).length * 6 - 1) * skala;
const mitt = (s, y, c, skala = 1) => text(s, Math.floor((180 - bredd(s, skala)) / 2), y, c, skala);

function cirkel(cx, cy, r, c, bara = null) {
  for (let a = 0; a < 360; a += 2) {
    const x = cx + r * Math.cos(a * Math.PI / 180), y = cy + r * Math.sin(a * Math.PI / 180);
    if (!bara || bara(x, y)) px(x, y, c);
  }
}

const RINK = { x: 10, y: 94, w: 160, h: 206, r: 22 };
const MAL_Y = 110, BLA1 = 152, MITT_Y = 197, BLA2 = 242, MAL2_Y = 284;

function rink() {
  ruta(0, 88, 180, 216, P.svart);
  const { x, y, w, h, r } = RINK;
  for (let yy = y; yy < y + h; yy++) {
    let inset = 0;
    if (yy < y + r) inset = r - Math.sqrt(r * r - (y + r - yy) ** 2);
    if (yy > y + h - r) inset = r - Math.sqrt(r * r - (yy - (y + h - r)) ** 2);
    ruta(x + inset - 2, yy, w - 2 * inset + 4, 1, P.vit);
    ruta(x + inset, yy, w - 2 * inset, 1, P.is);
  }
  ruta(x + r, y - 2, w - 2 * r, 2, P.vit);
  ruta(x + r, y + h, w - 2 * r, 2, P.vit);
  for (const ly of [MAL_Y, MAL2_Y]) ruta(x + 4, ly, w - 8, 1, P.rod);
  ruta(x, BLA1, w, 3, P.bla);
  ruta(x, BLA2, w, 3, P.bla);
  ruta(x, MITT_Y, w, 2, P.rod);
  cirkel(90, MITT_Y + 1, 16, P.bla);
  ruta(89, MITT_Y, 2, 2, P.bla);
  for (const [fx, fy] of [[50, 132], [130, 132], [50, 262], [130, 262]]) { cirkel(fx, fy, 9, P.rod); ruta(fx - 1, fy - 1, 2, 2, P.rod); }
  // Målområdena och målen.
  cirkel(90, MAL_Y, 10, P.bla, (_, cy) => cy > MAL_Y);
  cirkel(90, MAL2_Y, 10, P.bla, (_, cy) => cy < MAL2_Y);
  nat(90, MAL_Y, -1);
  nat(90, MAL2_Y, 1);
}

function nat(cx, ly, rikt) {
  const y0 = rikt < 0 ? ly - 7 : ly + 1;
  ruta(cx - 9, y0, 18, 7, P.vit);
  for (let i = 0; i < 18; i += 2) for (let j = 0; j < 7; j += 2) px(cx - 9 + i, y0 + j, P.ljusgra);
  ruta(cx - 9, y0, 1, 7, P.rod); ruta(cx + 8, y0, 1, 7, P.rod);
  ruta(cx - 9, rikt < 0 ? y0 : y0 + 6, 18, 1, P.rod);
}

/** Mål om Löven är bättre än snittet, räddning om sämre, stolpe om lika. */
const utfallAv = r => (r.vi === r.snitt ? 'stolpe' : (r.vi < r.snitt) === r.lagre ? 'mal' : 'raddning');

const ease = x => (x < 0 ? 0 : x > 1 ? 1 : x * x * (3 - 2 * x));
const lerp = (a, b, f) => a + (b - a) * f;
const tal = x => String(x.toFixed(1)).replace('.', ',');

/**
 * u: sekunder sedan scenen började. S: { intro, steg, dueller, slut } i
 * sekunder. D: datan (D.duell). serie: "SHL 26/27".
 */
function duellScen(c, u, S, D, serie) {
  ctx = c;
  ctx.imageSmoothingEnabled = false;
  ruta(0, 0, 180, 320, P.svart);
  const rader = D.rader;
  const n = rader.length;
  const i = u < S.intro ? -1 : Math.min(n - 1, Math.floor((u - S.intro) / S.steg));
  const v = i < 0 ? 0 : u - S.intro - i * S.steg;
  const slut = u >= S.intro + n * S.steg;

  const utfall = rader.map(utfallAv);
  // Ställningen räknas upp när pucken når målet.
  const SKOTT = 1.95;
  const klara = rader.filter((_, k) => k < i || (k === i && v >= SKOTT) || slut).map((_, k) => utfall[k]);
  const vi = klara.filter(x => x === 'mal').length;
  const de = klara.filter(x => x === 'raddning').length;

  // Resultattavlan.
  ruta(2, 2, 176, 34, P.vit);
  ruta(4, 4, 172, 30, P.morkbla);
  text('LÖVEN', 10, 9, P.gul);
  text('SNITT', 141, 9, P.vit);
  const lampa = i >= 0 && !slut && utfall[i] === 'mal' && v > SKOTT && v < SKOTT + 0.9 && Math.floor(v * 8) % 2 === 0;
  mitt(`${vi}-${de}`, 8, lampa ? P.rod : P.vit, 2);
  mitt(slut ? 'SLUT' : i < 0 ? serie : `ANFALL ${i + 1}/${n}`, 25, P.cyan);

  // Måttet och siffrorna, med en stapel per sida.
  if (i >= 0 && !slut) {
    const r = rader[i];
    const syns = Math.min(r.namn.length, Math.floor(v * 40));
    text(r.namn.slice(0, syns), 6, 44, P.gul);
    const max = Math.max(r.vi, r.snitt) || 1;
    const f = ease(v / 0.8);
    const lang = x => Math.max(1, Math.round(x / max * 100 * f));
    text('LÖVEN', 6, 58, P.ljusgron);
    ruta(40, 58, lang(r.vi), 7, P.gron);
    if (v > 0.5) text(tal(r.vi), 40 + lang(r.vi) + 3, 58, P.vit);
    text('SNITT', 6, 70, P.ljusgra);
    ruta(40, 70, lang(r.snitt), 7, P.gra);
    if (v > 0.5) text(tal(r.snitt), 40 + lang(r.snitt) + 3, 70, P.vit);
    if (v > 0.6) text(r.lagre ? 'LÄGRE ÄR BÄTTRE' : 'HÖGRE ÄR BÄTTRE', 6, 81, P.gra);
  } else if (i < 0) {
    mitt('LÖVEN MOT', 50, P.vit);
    mitt('SERIESNITTET', 62, P.gul);
    mitt(D.fonster < D.matcher ? `SENASTE ${D.fonster} MATCHERNA` : `ALLA ${D.matcher} MATCHER`, 76, P.ljusgra);
  } else {
    mitt(vi > de ? 'LÖVEN VANN' : vi < de ? 'SNITTET VANN' : 'OAVGJORT', 48, vi >= de ? P.gul : P.vit, 2);
    mitt(`BÄTTRE ÄN SNITTET I ${vi} AV ${n}`, 72, P.vit);
  }

  rink();

  // Spelarna. Löven anfaller uppåt; snittets backar och målvakt försvarar.
  const ben = k => SKRIDSKO[Math.floor(u * 9 + k) % 3];
  const gaende = i >= 0 && !slut;
  const fart = gaende ? ease((v - 0.3) / 1.4) : 0;
  const sida = i >= 0 ? (i % 2 ? 1 : -1) : 0;
  const ax = 86 + Math.sin(v * 5.5) * 14 * (1 - fart) + sida * 10 * fart;
  const ay = lerp(214, 138, fart);
  const utf = i >= 0 ? utfall[i] : null;

  // Pucken: vid klubban, sedan skottet.
  let pxp = ax + 9, pyp = ay + 10;
  const mal = { x: 90 + sida * 4, y: MAL_Y - 4 };
  const skott = gaende ? ease((v - 1.7) / 0.25) : 0;
  let mvx = 86 + (ax - 86) * 0.35;
  if (gaende && v >= 1.7) {
    if (utf === 'mal') {
      pxp = lerp(ax + 9, mal.x, skott); pyp = lerp(ay + 10, mal.y, skott);
      mvx = 86 - sida * 10 * skott; // målvakten går åt fel håll
    } else if (utf === 'stolpe') {
      const ut = Math.max(0, (v - 1.95) / 0.6);
      pxp = lerp(ax + 9, 90 + sida * 9, skott) + sida * 30 * ease(ut);
      pyp = lerp(ay + 10, MAL_Y, skott) + 26 * ease(ut);
    } else {
      const ut = Math.max(0, (v - 1.95) / 0.6);
      mvx = lerp(mvx, 86 + sida * 5, skott);
      pxp = lerp(ax + 9, 90 + sida * 5, skott) - sida * 34 * ease(ut);
      pyp = lerp(ay + 10, MAL_Y + 10, skott) + 30 * ease(ut);
    }
  }

  // Snittets backar glider mot puckföraren.
  const b1 = { x: lerp(52, ax - 18, fart * 0.6), y: lerp(160, 132, fart) };
  const b2 = { x: lerp(120, ax + 20, fart * 0.6), y: lerp(160, 132, fart) };
  for (const [b, k] of [[b1, 1], [b2, 2]]) { sprite(SPELARE, SERIEN, b.x, b.y); sprite(ben(k), SERIEN, b.x, b.y + 9); }
  sprite(MALVAKT, SERIEN, mvx, MAL_Y + 1);
  // Lövens egen målvakt längst ner, och en kedjekamrat som följer med.
  sprite(MALVAKT, LOVEN, 85, MAL2_Y - 12);
  const kx = lerp(130, ax + 30, fart), ky = lerp(232, ay + 26, fart);
  sprite(SPELARE, LOVEN, kx, ky); sprite(ben(3), LOVEN, kx, ky + 9);
  if (i >= 0 || u > 0.4) {
    sprite(SPELARE, LOVEN, ax, ay); sprite(ben(0), LOVEN, ax, ay + 9);
    // Klubban.
    const lyft = gaende && v > 1.55 && v < 1.75 ? -3 : 0;
    for (let k = 0; k < 4; k++) px(ax + 7 + k * 0.6, ay + 6 + k + lyft, P.brun);
  }
  if (!(slut || utf === 'mal' && v > 2.3)) ruta(pxp, pyp, 2, 2, P.svart);
  else if (utf === 'mal') ruta(mal.x, mal.y, 2, 2, P.svart);

  // Utfallet, stort mitt på isen.
  if (gaende && v > SKOTT + 0.05) {
    const blink = Math.floor(v * 6) % 2 === 0;
    if (utf === 'mal') {
      ruta(0, 214, 180, 22, blink ? P.rod : P.gul);
      mitt('MÅL!', 218, blink ? P.gul : P.rod, 2);
      // Mållampan.
      ruta(86, 96, 8, 4, blink ? P.rod : P.vit);
    } else if (utf === 'stolpe') {
      ruta(0, 214, 180, 22, P.morkbla);
      mitt('STOLPE', 218, P.vit, 2);
    } else {
      ruta(0, 214, 180, 22, P.morkbla);
      mitt('RÄDDNING', 218, P.vit, 2);
    }
  }

  ruta(0, 306, 180, 12, P.gul);
  text('SIDA377.SE', 6, 308, P.svart);
}

window.duellScen = duellScen;
window.utfallAv = utfallAv;
})();
