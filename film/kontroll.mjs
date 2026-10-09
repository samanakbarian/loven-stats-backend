/**
 * Stämmer av en match innan den blir film. Hellre ingen film än en som säger
 * något annat än matchrapporten.
 *
 * Returnerar en lista med problem; tom lista betyder att filmen får göras.
 */
export function kontrollera(m) {
  const fel = [];
  if (m.status !== 'ok') fel.push(`status ${m.status}`);
  if (m.provisional) fel.push('protokollet är preliminärt');
  if (!m.teams?.ours || !m.teams?.theirs) fel.push('lagens summering saknas');
  const res = String(m.result || '').match(/^\s*(\d+)\s*-\s*(\d+)/);
  if (!res) {
    fel.push(`inget resultat (${m.result})`);
    return fel;
  }
  const [hemma, borta] = [Number(res[1]), Number(res[2])];
  const viHemma = !!m.teams?.ours?.is_home;
  const vara = viHemma ? hemma : borta;
  const deras = viHemma ? borta : hemma;

  // Målen per lag ska ge resultatet. Ett avgörande straffmål räknas i
  // resultatet men är ingen målhändelse, så vinnaren får ett mål till godo.
  const perioder = String(m.period_results || '').split(',').length;
  const straffar = perioder >= 5 || (m.goals || []).some(g => /GWS/.test(g.score_state || ''));
  const spelmal = (m.goals || []).filter(g => !/GWS/.test(g.score_state || ''));
  const vi = spelmal.filter(g => g.team_code === 'IFB').length;
  const de = spelmal.length - vi;
  const ok = straffar
    ? (vi === vara && de === deras) || (vi + 1 === vara && de === deras) || (vi === vara && de + 1 === deras)
    : vi === vara && de === deras;
  if (!ok) fel.push(`målen ${vi}-${de} ger inte resultatet ${vara}-${deras}`);
  if ((m.goals || []).some(g => !g.scorer && !/GWS/.test(g.score_state || ''))) fel.push('mål utan målskytt');

  // Skotten per period ska ge lagets skott.
  for (const sida of ['ours', 'theirs']) {
    const t = m.teams?.[sida];
    if (!t) continue;
    const per = String(t.shots_by_period || '').split(',').map(Number).filter(x => !Number.isNaN(x));
    const summa = per.reduce((a, b) => a + b, 0);
    if (!per.length) fel.push(`skott per period saknas (${sida})`);
    else if (summa !== Number(t.shots)) fel.push(`skott per period ${summa} ≠ ${t.shots} (${sida})`);
  }
  if (!(m.best || []).length) fel.push('Matchens bästa saknas');
  return fel;
}

/** "SHL 2026/27" -> "SHL 26/27", "HockeyAllsvenskan 2025/26" -> "HOCKEYALLSVENSKAN 25/26". */
export function serieRad(sasong) {
  return String(sasong || '').toUpperCase().replace(/20(\d\d)\/(\d\d)/, '$1/$2');
}
