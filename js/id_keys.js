'use strict';

const KS_STORAGE = 'nacaduba-cpkey-v1';

let ksState = {
  couplets: [],
  speciesMap: new Map(),
  history: [],
  current: null
};

// ── Utilities ────────────────────────────────────────────────────
function ksEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// The real Corbet & Pendlebury lead number for a couplet (first int in `leads`).
function ksNumA(cp) {
  const m = String(cp && cp.leads || '').match(/\d+/);
  return m ? m[0] : String((cp && cp.label || '').replace(/^K/, ''));
}

// Where a Skip advances to: the first non-terminal branch, or null if both end.
function ksSkipNext(cp) {
  return cp.next_a || cp.next_b || null;
}

// "View all observations" link for a species. Uses the stored observations URL
// (built from the taxon id) or falls back to a name query for taxa without an id.
function ksInatUrl(sp, name) {
  if (sp && sp.inat_url) return sp.inat_url;
  return 'https://www.inaturalist.org/observations?verifiable=true&preferred_place_id=6734&taxon_name='
    + encodeURIComponent((sp && sp.name) || name || '');
}

// ── Persistence ──────────────────────────────────────────────────
function ksSave() {
  try {
    localStorage.setItem(KS_STORAGE, JSON.stringify({
      history: ksState.history,
      current: ksState.current
    }));
  } catch (e) {}
}

function ksLoad() {
  try {
    const raw = localStorage.getItem(KS_STORAGE);
    if (!raw) return;
    const data = JSON.parse(raw);
    ksState.history = Array.isArray(data.history) ? data.history : [];
    ksState.current = data.current || null;
  } catch (e) {}
}

// ── Scoring ──────────────────────────────────────────────────────
// Yes (choice A) = the lead statement matched; No (choice B) = it didn't.
function ksComputeScores() {
  const scores = new Map();
  ksState.speciesMap.forEach((_, name) => scores.set(name, 0));

  for (const entry of ksState.history) {
    if (entry.choice === 'S') continue;
    const cp = ksState.couplets.find(c => c.id === entry.id);
    if (!cp) continue;
    const pos = entry.choice === 'A' ? (cp.species_a || []) : (cp.species_b || []);
    const neg = entry.choice === 'A' ? (cp.species_b || []) : (cp.species_a || []);
    pos.forEach(sp => scores.set(sp, (scores.get(sp) || 0) + 1));
    neg.forEach(sp => scores.set(sp, (scores.get(sp) || 0) - 1));
  }

  return [...scores.entries()]
    .map(([name, score]) => ({ name, score }))
    .sort((a, b) => b.score - a.score || a.name.localeCompare(b.name));
}

// ── Navigation ───────────────────────────────────────────────────
// choice: 'A' = Yes (statement matches), 'B' = No, 'S' = Skip upperside.
function ksAnswer(choice) {
  const cp = ksState.couplets.find(c => c.id === ksState.current);
  if (!cp) return;

  ksState.history = ksState.history.filter(h => h.id !== cp.id);
  if (choice !== 'S') {
    ksState.history.push({ id: cp.id, choice });
  }

  let next;
  if (choice === 'S')      next = ksSkipNext(cp);
  else if (choice === 'B') next = cp.next_b;
  else                     next = cp.next_a;
  ksState.current = next || null;

  ksSave();
  ksRender();
}

function ksJumpTo(coupletId) {
  const idx = ksState.history.findIndex(h => h.id === coupletId);
  if (idx !== -1) {
    ksState.history = ksState.history.slice(0, idx);
    ksState.current = coupletId;
  } else {
    ksState.history = [];
    ksState.current = coupletId;
  }
  ksSave();
  ksRender();
}

function ksBack() {
  if (ksState.history.length > 0) {
    const last = ksState.history[ksState.history.length - 1];
    ksState.history.pop();
    ksState.current = last.id;
  } else {
    ksState.current = ksState.couplets[0]?.id || null;
  }
  ksSave();
  ksRender();
}

function ksReset() {
  ksState.history = [];
  ksState.current = ksState.couplets[0]?.id || null;
  ksSave();
  ksRender();
}

// ── Rendering ────────────────────────────────────────────────────
function ksRender() {
  const answered = ksState.history.filter(h => h.choice !== 'S').length;
  const countEl = document.getElementById('ks-answered-count');
  if (countEl) countEl.textContent = answered > 0 ? answered + ' answered' : '';

  ksRenderHistory();
  ksRenderCouplet();
  ksRenderCandidates();
}

function ksRenderHistory() {
  const el = document.getElementById('ks-history');
  if (!el) return;
  if (ksState.history.length === 0) { el.innerHTML = ''; return; }

  const items = ksState.history.map(h => {
    const cp = ksState.couplets.find(c => c.id === h.id);
    if (!cp) return '';
    const verdict = h.choice === 'S' ? 'Skip' : h.choice === 'A' ? 'Yes' : 'No';
    const label = 'Key ' + ksNumA(cp) + ': ' + verdict;
    return '<span class="ks-hist-item" onclick="ksJumpTo(\'' + ksEsc(h.id) + '\')" ' +
      'title="Back to Key ' + ksEsc(ksNumA(cp)) + '">' + ksEsc(label) + '</span>';
  }).filter(Boolean).join('<span class="ks-hist-sep">&#8250;</span>');

  el.innerHTML = '<div class="ks-hist">' + items +
    '<span class="ks-hist-sep">&#8250;</span><span class="ks-hist-current">now</span></div>';
}

function ksRenderCouplet() {
  const el = document.getElementById('ks-couplets');
  if (!el) return;

  // Reached a terminal lead — show the identification result.
  if (!ksState.current) {
    const last = ksState.history[ksState.history.length - 1];
    const cp = last ? ksState.couplets.find(c => c.id === last.id) : null;

    let side = [], text, leadNum;
    if (cp && last.choice === 'B') {
      side = cp.species_b || [];
      text = cp.b_text;
      leadNum = String(cp.leads || '').split('/')[1];
    } else if (cp) {
      side = cp.species_a || [];
      text = cp.a_text;
      leadNum = ksNumA(cp);
    }
    if (leadNum) leadNum = leadNum.replace(/[^\d]/g, '');

    let name = side[0];
    // Fall back to the top-scoring candidate if the branch has no single species.
    const scores = ksComputeScores();
    if (!name && scores[0]) name = scores[0].name;

    // A terminal lead can end on look-alikes the key cannot separate (e.g.
    // russelli / normani). Name them so the result isn't falsely precise.
    const others = side.slice(1);
    const ambiguous = others.length
      ? '<p class="ks-result-text"><strong>Not separable by this key from:</strong> ' +
          ksEsc(others.map(n => n.replace(/^Nacaduba /, 'N. ')).join(', ')) +
          ' — compare the candidates below.</p>'
      : '';

    const sp = name ? ksState.speciesMap.get(name) : null;
    const common = (sp && sp.common_name) ? '<p class="ks-result-common">' + ksEsc(sp.common_name) + '</p>' : '';
    const inat = name
      ? '<a class="ks-inat-link" href="' + ksEsc(ksInatUrl(sp, name)) + '" target="_blank" rel="noopener noreferrer">View all observations on iNaturalist &#8594;</a>'
      : '';

    el.innerHTML =
      '<div class="ks-result-card">' +
        '<p class="ks-result-label">&#9658; Identification' + (leadNum ? ' &middot; Key ' + ksEsc(leadNum) : '') + '</p>' +
        '<p class="ks-result-species"><em>' + ksEsc(name || 'Unknown') + '</em></p>' +
        common +
        (text ? '<p class="ks-result-text">' + ksEsc(text) + '</p>' : '') +
        ambiguous +
        inat +
        '<button class="ks-btn ks-btn-skip" onclick="ksBack()" style="margin-top:0.7rem;">&#8592; Go back</button>' +
      '</div>';
    return;
  }

  const cp = ksState.couplets.find(c => c.id === ksState.current);
  if (!cp) { el.innerHTML = ''; return; }

  const prev = ksState.history.find(h => h.id === cp.id);
  const selA = prev && prev.choice === 'A';
  const selB = prev && prev.choice === 'B';

  const hintHtml = cp.hint
    ? '<details class="ks-hint"><summary>Hint</summary><p>' + ksEsc(cp.hint) + '</p></details>'
    : '';

  // Skip is only meaningful when the upperside can't be assessed AND at least
  // one branch continues the key (both-terminal couplets get no Skip).
  const canSkip = cp.upperside && ksSkipNext(cp) !== null;
  const skipHtml = canSkip
    ? '<div class="ks-btn-row"><button class="ks-btn ks-btn-skip" onclick="ksAnswer(\'S\')">' +
        'Skip — cannot assess upperside / female</button></div>'
    : '';

  const questionHtml = cp.question
    ? ' <span class="ks-cp-question">' + ksEsc(cp.question) + '</span>'
    : '';

  el.innerHTML =
    '<div class="ks-cp">' +
      '<p class="ks-cp-label"><span class="ks-label-tag">Key ' + ksEsc(ksNumA(cp)) + '</span>' + questionHtml + '</p>' +
      '<p class="ks-cp-statement">' + ksEsc(cp.a_text) + '</p>' +
      hintHtml +
      '<div class="ks-btn-row ks-btn-row--yesno">' +
        '<button class="ks-btn ks-btn-yes' + (selA ? ' sel' : '') + '" onclick="ksAnswer(\'A\')">Yes</button>' +
        '<button class="ks-btn ks-btn-no' + (selB ? ' sel' : '') + '" onclick="ksAnswer(\'B\')">No</button>' +
      '</div>' +
      skipHtml +
    '</div>';
}

function ksRenderCandidates() {
  const el = document.getElementById('ks-candidates');
  if (!el) return;

  const scored = ksState.history.filter(h => h.choice !== 'S').length;
  if (scored === 0) {
    el.innerHTML = '<p class="ks-empty">Answer couplet questions above to rank candidates.</p>';
    return;
  }

  const scores = ksComputeScores();
  const maxScore = Math.max(...scores.map(s => s.score));
  const minScore = Math.min(...scores.map(s => s.score));
  const range = Math.max(maxScore - minScore, 1);
  const medals = ['🥇', '🥈', '🥉'];

  el.innerHTML = scores.slice(0, 10).map(function(s, i) {
    const sp = ksState.speciesMap.get(s.name) || { name: s.name };
    const pct = Math.round(((s.score - minScore) / range) * 100);
    const neg = s.score < 0;
    const isTop = s.score === maxScore;
    const medal = isTop && i < 3 ? medals[i] : '';
    const inatUrl = ksInatUrl(sp, s.name);
    const scoreStr = (s.score > 0 ? '+' : '') + s.score;
    return '<div class="ks-cand">' +
      '<div class="ks-cand-row">' +
        '<span class="ks-rank">' + (medal || (i + 1)) + '</span>' +
        '<span class="ks-cname">' +
          '<span class="ks-sci">' + ksEsc(sp.name) + '</span>' +
          (sp.common_name ? '<span class="ks-common">' + ksEsc(sp.common_name) + '</span>' : '') +
        '</span>' +
        '<span class="ks-bar-wrap">' +
          '<span class="ks-bar-bg"><span class="ks-bar' + (neg ? ' neg' : '') + '" style="width:' + pct + '%"></span></span>' +
          '<span class="ks-score-num' + (neg ? ' neg' : '') + '">' + ksEsc(scoreStr) + '</span>' +
        '</span>' +
        '<a class="ks-inat-icon" href="' + ksEsc(inatUrl) + '" target="_blank" rel="noopener noreferrer" title="View on iNaturalist">🔗</a>' +
      '</div>' +
    '</div>';
  }).join('');
}

// ── Init ─────────────────────────────────────────────────────────
async function ksInit() {
  const loadingEl = document.getElementById('loading');
  const appEl = document.getElementById('ks-app');

  try {
    const [keyData, spData] = await Promise.all([
      fetch('data/id_key.json').then(r => { if (!r.ok) throw new Error('id_key'); return r.json(); }),
      fetch('data/species.json').then(r => { if (!r.ok) throw new Error('species'); return r.json(); })
    ]);

    ksState.couplets = keyData.couplets || [];

    (spData.species || []).forEach(sp => ksState.speciesMap.set(sp.name, sp));

    // Ensure every species mentioned in couplets has a map entry
    ksState.couplets.forEach(cp => {
      [...(cp.species_a || []), ...(cp.species_b || [])].forEach(name => {
        if (!ksState.speciesMap.has(name)) ksState.speciesMap.set(name, { name });
      });
    });

    ksLoad();

    // Validate restored state
    if (ksState.current && !ksState.couplets.find(c => c.id === ksState.current)) {
      ksState.current = ksState.couplets[0]?.id || null;
      ksState.history = [];
    }
    if (!ksState.current && ksState.history.length === 0) {
      ksState.current = ksState.couplets[0]?.id || null;
    }

    if (loadingEl) loadingEl.style.display = 'none';
    if (appEl) appEl.style.display = '';

    ksRender();
  } catch (err) {
    if (loadingEl) loadingEl.innerHTML = '<p style="padding:2rem;text-align:center;color:#c00;">Failed to load key data.</p>';
  }
}

document.addEventListener('DOMContentLoaded', function() {
  const resetBtn = document.getElementById('ks-reset');
  if (resetBtn) {
    resetBtn.addEventListener('click', function() {
      if (ksState.history.length === 0 || confirm('Reset the key and start from the beginning?')) {
        ksReset();
      }
    });
  }
  ksInit();
});
