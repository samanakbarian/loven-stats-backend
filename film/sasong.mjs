/**
 * Säsongsfilmen: data. Samlar det filmen behöver ur samma API som
 * Utvecklingsfliken läser, så filmen aldrig säger något annat än sidan.
 *
 *   /table-history  tabellplaceringen omgång för omgång, alla lag
 *   /analytics      Lövens matcher i ordning, sviter, publik, nästa match
 *   /statistics     säsongens namn och Lövens poängliga
 *   /league-trend   Löven mot seriesnittet, rullande, till duellerna
 */
import { serieRad } from './kontroll.mjs';

export async function hamtaSasong(api, season) {
  const q = season ? `?season=${encodeURIComponent(season)}` : '';
  const hamta = async vag => {
    const r = await fetch(api + vag + q);
    if (!r.ok) throw new Error(`${vag}: HTTP ${r.status}`);
    return r.json();
  };
  const [hist, ana, stat, trend] = await Promise.all([
    hamta('/api/v1/table-history'), hamta('/api/v1/analytics'), hamta('/api/v1/statistics'),
    hamta('/api/v1/league-trend').catch(e => ({ status: 'error', error: String(e.message || e) })),
  ]);
  const m = ana.modules || {};
  const skaters = stat.bjorkloven_skaters?.regular || [];
  const poangbast = [...skaters].sort((a, b) => b.points - a.points || b.goals - a.goals)[0];
  return {
    key: hist.season_key || '',
    serie: serieRad(stat.season || hist.season),
    rounds: hist.rounds,
    dates: hist.dates || [],
    settled: !!hist.table_settled_after_last_round,
    teams: hist.teams.map(t => ({ team: t.team, bjk: t.is_bjk, ranks: t.ranks, points: t.points, gp: t.games_played })),
    games: (m.timeline || []).map(g => ({
      date: g.date, opponent: g.opponent, home: g.isHome, gf: g.gf, ga: g.ga, pts: g.pts, cum: g.cumPts, beyond: g.beyond,
    })),
    streak: m.streaks?.longest_win || null,
    duell: dueller(trend),
    // Hur många spelade matcher som har båda lagens siffror. Snitten bygger
    // på dem, och kvällens matcher kommer in i olika hämtningar.
    tackning: trend?.coverage || null,
    trendFel: trend?.status === 'ok' ? null : trend?.error || trend?.status || 'saknas',
    publik: m.attendance?.trend?.length ? m.attendance.avg : null,
    poangbast: poangbast ? { name: poangbast.player_name, points: poangbast.points, goals: poangbast.goals, assists: poangbast.assists } : null,
    // Bara det filmen visar: sannolikheterna rör sig utan att något hänt.
    nasta: m.predictions?.next_game ? (({ opponent, is_home, date }) => ({ opponent, is_home, date }))(m.predictions.next_game) : null,
  };
}

/**
 * Samma tanke som kontroll.mjs: hellre ingen film än en som inte stämmer.
 *
 * kravSerie: seriens siffror måste vara kompletta, annars jämförs Löven mot
 * ett halvt snitt — eller så faller duellen bort helt när snittet för Lövens
 * senaste match saknas. Gäller den aktiva säsongen, där alla matcher hämtas;
 * äldre HA-säsonger har bara Lövens och får sin film utan duell.
 */
export function kontrolleraSasong(s, { kravSerie = false } = {}) {
  const fel = [];
  if (kravSerie) {
    if (s.trendFel) fel.push(`seriens siffror gick inte att hämta (${s.trendFel})`);
    else if (!s.tackning || s.tackning.games < s.tackning.played) {
      fel.push(`seriens siffror saknas för ${s.tackning ? s.tackning.played - s.tackning.games : '?'} spelade matcher`);
    } else if (!s.duell) fel.push('seriesnittet saknas för Lövens senaste match');
    else if (s.duell.matcher !== s.games.length) fel.push(`Lövens siffror finns för ${s.duell.matcher} av ${s.games.length} matcher`);
  }
  if (!s.rounds?.length) fel.push('inga omgångar');
  if (!s.teams?.some(t => t.bjk)) fel.push('Björklöven saknas i tabellen');
  for (const t of s.teams || []) if (t.ranks.length !== s.rounds.length) fel.push(`${t.team}: ${t.ranks.length} placeringar på ${s.rounds.length} omgångar`);
  // Varje omgång ska ha placeringarna 1..n en gång var.
  s.rounds?.forEach((_, i) => {
    const r = s.teams.map(t => t.ranks[i]).filter(x => x != null).sort((a, b) => a - b);
    if (r.some((x, k) => x !== k + 1)) fel.push(`omgång ${i + 1}: placeringarna går inte jämnt upp`);
  });
  const sista = s.games[s.games.length - 1];
  const vi = s.teams.find(t => t.bjk);
  if (sista && vi && sista.cum !== vi.points) fel.push(`poängen ${sista.cum} ≠ tabellens ${vi.points}`);
  return fel;
}

/**
 * Löven mot seriesnittet i samma fönster som Utvecklingsfliken (rullande,
 * högst tio matcher). Snittet tas vid Lövens senaste match, så båda sidor
 * räknas över lika många matcher. Saknas serien (som i äldre HA-säsonger,
 * där bara Lövens matcher finns) blir det inga dueller.
 */
function dueller(trend) {
  const vi = trend?.teams?.find(t => t.is_ours);
  const sist = vi?.points?.[vi.points.length - 1];
  const snitt = sist && trend.league?.find(l => l.match === sist.match);
  if (!snitt || (snitt.teams || 0) < 4) return null;
  const MATT = [
    ['gf_pg', 'MÅL PER MATCH', false, 1],
    ['ga_pg', 'INSLÄPPTA PER MATCH', true, 1],
    ['sf_pg', 'SKOTT PER MATCH', false, 1],
    ['sh_pct', 'SKOTTPROCENT', false, 1],
    ['sv_pct', 'RÄDDNINGSPROCENT', false, 1],
    ['pim_pg', 'UTVISNINGSMIN PER MATCH', true, 1],
  ];
  const rader = MATT
    .filter(([k]) => sist[k] != null && snitt[k] != null)
    .map(([k, namn, lagre, dec]) => ({ key: k, namn, lagre, vi: round(sist[k], dec), snitt: round(snitt[k], dec) }));
  return rader.length ? { fonster: sist.window, matcher: vi.games, rader } : null;
}
const round = (x, d) => Math.round(x * 10 ** d) / 10 ** d;
