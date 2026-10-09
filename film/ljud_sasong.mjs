/**
 * Ljudet till säsongsfilmen. Samma ljudvärld som matchfilmen (ljud.mjs):
 * syntetiserat, inget samplat och ingen musik.
 *
 * Varje omgång får ett tick. Klättrar Löven går en stigande ton, tappar de
 * en fallande, så man hör säsongen även med blicken någon annanstans.
 *
 * Hjälpfunktionerna är kopior av ljud.mjs. Matchfilmernas version räknas ur
 * den filen, så den lämnas orörd tills de görs om ändå.
 */
const SR = 44100;

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

export function ljudSasong(data, T) {
  const n = Math.floor(T.total * SR);
  const mix = new Float32Array(n);
  const r = slump(378);

  const lagg = (ljud, start, gain = 1) => {
    const i = Math.floor(start * SR);
    for (let k = 0; k < ljud.length && i + k < n; k++) if (i + k >= 0) mix[i + k] += ljud[k] * gain;
  };
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
  const lur = (s, vol = 0.16) => {
    const m = Math.floor(s * SR);
    const x = new Float32Array(m);
    for (let k = 0; k < m; k++) {
      const t = k / SR;
      x[k] = vol * ((2 * ((t * 233) % 1) - 1) + (2 * ((t * 349) % 1) - 1) * 0.7);
    }
    return env(x, 0.03, 0.35);
  };

  // Brus, "sidan hittad", rubriken skrivs.
  const brus = new Float32Array(Math.floor(T.brus * SR));
  for (let k = 0; k < brus.length; k++) brus[k] = (r() * 2 - 1) * 0.18;
  lagg(env(brus, 0.01, 0.15), 0);
  lagg(noter([1046, 1568], 0.07, 'fyrkant', 0.18), T.rubrik);
  skrivljud(T.titel, 12, 24);
  // Tabellen rullar fram rad för rad.
  for (let k = 0; k < 14; k++) lagg(env(ton(700 + k * 30, 0.02, 'sinus', 0.05), 0.001, 0.015), T.tabell + k / 14);

  // Omgångarna. Långa säsonger får tystare tick, annars blir det ett surr.
  const tyst = T.N > 20 ? 0.5 : 1;
  for (const h of T.HANDELSER) {
    lagg(env(ton(1000, 0.015, 'sinus', 0.07 * tyst), 0.001, 0.012), h.t);
    if (h.flytt > 0) lagg(noter([523, 784], 0.07, 'fyrkant', 0.16 * tyst), h.t + 0.15);
    else if (h.flytt < 0) lagg(noter([392, 294], 0.09, 'såg', 0.12 * tyst), h.t + 0.15);
  }
  lagg(lur(1.4, 0.14), T.loppSlut);

  // Kurvan ritas: en svag stigande ton under ritningen.
  {
    const s = 3.2, m = Math.floor(s * SR);
    const x = new Float32Array(m);
    let fas = 0;
    for (let k = 0; k < m; k++) {
      fas += 2 * Math.PI * (300 + 300 * k / m) / SR;
      x[k] = Math.sin(fas) * 0.035;
    }
    lagg(env(x, 0.2, 0.4), T.kurva + 0.6);
  }
  for (let i = 0; i < 3; i++) lagg(noter([[784, 523, 659][i]], 0.12, 'sinus', 0.16), T.kurva + 4.2 + i * 0.9);

  // Åttabitarsduellerna: en liten fanfar in, skär i isen, skottet och utfallet.
  if (T.DUELLER.length) {
    lagg(noter([392, 523, 659, 784, 659, 784, 1046], 0.08, 'fyrkant', 0.14), T.duell + 0.2);
    for (const d of T.DUELLER) {
      // Skären: kort brus i takt med benen.
      for (let k = 0; k < 6; k++) {
        const m = Math.floor(0.05 * SR), x = new Float32Array(m);
        for (let j = 0; j < m; j++) x[j] = (r() * 2 - 1) * 0.05 * Math.sin(Math.PI * j / m);
        lagg(x, d.t + 0.35 + k * 0.23);
      }
      const knall = new Float32Array(Math.floor(0.04 * SR));
      for (let j = 0; j < knall.length; j++) knall[j] = (r() * 2 - 1) * 0.3 * Math.exp(-6 * j / knall.length);
      lagg(knall, d.t + 1.7);
      if (d.utfall === 'mal') {
        lagg(lur(0.9, 0.12), d.t + 1.95);
        lagg(noter([523, 659, 784, 1046, 784, 1046], 0.07, 'fyrkant', 0.16), d.t + 2.0);
      } else if (d.utfall === 'stolpe') {
        lagg(env(ton(1760, 0.35, 'sinus', 0.18), 0.001, 0.3), d.t + 1.95);
      } else {
        lagg(env(ton(98, 0.18, 'fyrkant', 0.16), 0.002, 0.1), d.t + 1.95);
        lagg(noter([330, 247], 0.1, 'fyrkant', 0.1), d.t + 2.15);
      }
    }
    // Slutsignal: en drill i visselpipan.
    const vissla = new Float32Array(Math.floor(0.7 * SR));
    for (let j = 0; j < vissla.length; j++) vissla[j] = Math.sin(2 * Math.PI * (2500 + 120 * Math.sin(2 * Math.PI * 28 * j / SR)) * j / SR) * 0.08;
    lagg(env(vissla, 0.01, 0.1), T.fakta - 2.9);
  }

  // Siffrorna skrivs in, rad för rad.
  for (let k = 0; k < 7; k++) skrivljud(T.fakta + 0.8 + k * 0.7, 10, 60);
  lagg(noter([523, 784, 1046], 0.14, 'sinus', 0.16), T.total - 2.2);

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
