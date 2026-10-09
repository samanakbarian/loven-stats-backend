/**
 * Ljudspåret till matchfilmen, syntetiserat och synkat mot filmens tidslinje.
 *
 * Inget samplat material och ingen musik: allt är vågformer, så det finns
 * inga rättigheter att hålla reda på. Tiderna kommer från filmen själv
 * (window.TIDER i film.html), så bild och ljud följer samma tidslinje även
 * när matchen går till förlängning.
 */

const SR = 44100;
const CPS_HANDELSE = 35;

/** En enkel, sådd slump, så samma match alltid ger samma ljud. */
function slump(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function ljudspar(match, T) {
  const n = Math.floor(T.total * SR);
  const mix = new Float32Array(n);
  const r = slump(377);
  const SLUTSEK = T.SLUTSEK;

  const lagg = (ljud, start, gain = 1) => {
    const i = Math.floor(start * SR);
    for (let k = 0; k < ljud.length && i + k < n; k++) if (i + k >= 0) mix[i + k] += ljud[k] * gain;
  };
  /** Kort attack och avklingning, så inget knäpper. */
  const env = (x, a = 0.005, rel = 0.08) => {
    const ai = Math.floor(a * SR), ri = Math.floor(rel * SR);
    for (let k = 0; k < ai && k < x.length; k++) x[k] *= k / ai;
    for (let k = 0; k < ri && k < x.length; k++) x[x.length - 1 - k] *= k / ri;
    return x;
  };
  const ton = (f, s, form = 'fyrkant', vol = 0.3) => {
    const m = Math.floor(s * SR);
    const x = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      const t = k / SR;
      x[k] = vol * (form === 'sinus' ? Math.sin(2 * Math.PI * f * t)
        : form === 'såg' ? 2 * ((t * f) % 1) - 1
          : ((t * f) % 1 < 0.5 ? 1 : -1));
    }
    return env(x, 0.004, Math.min(0.12, s * 0.5));
  };
  const noter = (hz, steg = 0.11, form = 'fyrkant', vol = 0.25) => {
    const ut = new Float32Array(Math.floor((hz.length * steg + 0.3) * SR));
    hz.forEach((f, k) => {
      const x = ton(f, steg * 1.6, form, vol);
      const i = Math.floor(k * steg * SR);
      for (let j = 0; j < x.length && i + j < ut.length; j++) ut[i + j] += x[j];
    });
    return ut;
  };
  const knapp = () => {
    const m = Math.floor(0.012 * SR);
    const x = new Float32Array(m);
    for (let k = 0; k < m; k++) x[k] = (r() * 2 - 1) * Math.exp(-8 * k / m) * 0.12;
    return x;
  };
  const skrivljud = (start, tecken, cps) => { for (let k = 0; k < tecken; k++) lagg(knapp(), start + k / cps); };
  /** Mistlur: två sågtänder i en ofullständig kvint. */
  const lur = (s, vol = 0.16) => {
    const m = Math.floor(s * SR);
    const x = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      const t = k / SR;
      x[k] = vol * ((2 * ((t * 233) % 1) - 1) + (2 * ((t * 349) % 1) - 1) * 0.7);
    }
    return env(x, 0.03, 0.35);
  };

  const sek = x => { const [m, s] = x.split(':').map(Number); return m * 60 + s; };
  const spelTillFilm = s => T.klockStart + Math.min(s, SLUTSEK) / SLUTSEK * (T.klockSlut - T.klockStart);

  // Brus i början, som när kanalen letar, sedan "sidan hittad".
  const brus = new Float32Array(Math.floor(T.brus * SR));
  for (let k = 0; k < brus.length; k++) brus[k] = (r() * 2 - 1) * 0.18;
  lagg(env(brus, 0.01, 0.15), 0);
  lagg(noter([1046, 1568], 0.07, 'fyrkant', 0.18), T.rubrik);
  skrivljud(T.titel, 22, 20);

  // Klockan tickar: ett tick per spelminut, lite ljusare varannan.
  for (let minut = 0; minut <= Math.floor(SLUTSEK / 60); minut++) {
    const s = T.klockStart + minut * 60 / SLUTSEK * (T.klockSlut - T.klockStart);
    lagg(env(ton(minut % 2 ? 1400 : 1000, 0.015, 'sinus', 0.07), 0.001, 0.012), s);
  }

  for (const g of match.goals) {
    const s = spelTillFilm(sek(g.time));
    if (g.team_code === 'IFB' && sek(g.time) > 3600) {
      // Avgörande mål i förlängningen: längre fanfar och lur.
      lagg(noter([523, 659, 784, 1046, 784, 1046, 1318, 1568], 0.1, 'fyrkant', 0.24), s);
      lagg(lur(1.2, 0.12), s + 0.9);
    } else if (g.team_code === 'IFB') {
      lagg(noter([523, 659, 784, 1046, 1318], 0.09, 'fyrkant', 0.22), s);
    } else {
      lagg(noter([392, 311], 0.18, 'såg', 0.16), s);
    }
    skrivljud(s, 24, CPS_HANDELSE);
  }

  const sett = new Set();
  for (const p of match.penalties) {
    if (!p.minutes) continue;
    const k = p.time + p.player;
    if (sett.has(k)) continue;
    sett.add(k);
    const s = spelTillFilm(sek(p.time));
    lagg(ton(110, 0.22, 'fyrkant', 0.12), s);
    skrivljud(s, 22, CPS_HANDELSE);
  }

  for (const per of (T.FORL ? [2, 3, 4] : [2, 3])) lagg(lur(0.6, 0.10), spelTillFilm((per - 1) * 1200));
  lagg(lur(1.6, 0.16), T.slut);

  for (let i = 0; i < 3; i++) lagg(noter([[784, 659, 523][i]], 0.12, 'sinus', 0.18), T.basta + 0.8 + i * 1.0);
  lagg(noter([523, 784, 1046], 0.14, 'sinus', 0.16), T.total - 2.2);

  // Svag brumton under allt, som en gammal tv.
  for (let k = 0; k < n; k++) mix[k] += Math.sin(2 * Math.PI * 50 * k / SR) * 0.015;

  let topp = 0;
  for (let k = 0; k < n; k++) topp = Math.max(topp, Math.abs(mix[k]));
  const skala = topp > 0.9 ? 0.9 / topp : 1;
  const ut = Math.floor(0.5 * SR);
  for (let k = 0; k < n; k++) {
    mix[k] *= skala;
    if (k > n - ut) mix[k] *= (n - k) / ut;
  }
  return wav(mix);
}

function wav(samples) {
  const buf = Buffer.alloc(44 + samples.length * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples.length * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(SR, 24); buf.writeUInt32LE(SR * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(samples.length * 2, 40);
  for (let k = 0; k < samples.length; k++) buf.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(samples[k] * 32767))), 44 + k * 2);
  return buf;
}
