const state = {
  tree: null,
  species: null,       // Map<taxon_id (number), species object>
  speciesIndex: [],    // [{name, common_name, note, taxon_photos, inat_url}] sorted A-Z
  questionNumbers: null, // Map<questionText, number> — stable Q-numbers by DFS order
  simCdPaths: null,    // Map<resultName, [{question, choice}]> — pre-computed by Python
  currentNodeId: null,
  history: []          // [{ nodeId, choiceLabel }, ...]
};

const ESTIMATED_MAX_DEPTH = 8;

// ===== Init =====

async function init() {
  const loadingEl = document.getElementById('loading');
  const appEl = document.getElementById('app');

  try {
    const [treeRes, speciesRes, simCdRes] = await Promise.all([
      fetch('data/tree.json', { cache: 'no-cache' }),
      fetch('data/species.json', { cache: 'no-cache' }),
      fetch('data/sim_cd_paths.json', { cache: 'no-cache' })
    ]);

    if (!treeRes.ok || !speciesRes.ok) throw new Error('Failed to load data files');

    const [treeData, speciesData] = await Promise.all([
      treeRes.json(),
      speciesRes.json()
    ]);

    state.tree = treeData;
    state.species = new Map(speciesData.species.map(s => [s.id, s]));
    state.speciesIndex = buildSpeciesIndex(treeData, speciesData);
    state.questionNumbers = buildQuestionNumbers(treeData);
    state.simCdPaths = simCdRes.ok ? new Map(Object.entries(await simCdRes.json())) : null;
    state.currentNodeId = treeData.start;
    state.history = [];

    loadingEl.style.display = 'none';
    appEl.style.display = 'block';
    document.getElementById('app-topbar').style.display = '';
    render();
    initMenuListeners();
  } catch (err) {
    loadingEl.style.display = 'none';
    appEl.style.display = 'block';
    appEl.innerHTML = renderErrorCard('Could not load identification data. Please refresh the page.');
  }
}

// buildTreePaths and buildQuestionNumbers live in path-utils.js

// Build a flat sorted array of all result nodes enriched with species photo/url data
function buildSpeciesIndex(treeData, speciesData) {
  const seen = new Set();
  const index = [];
  const pathsMap = buildTreePaths(treeData);

  for (const node of Object.values(treeData.nodes)) {
    if (node.type !== 'result') continue;
    const name = node.name || '';
    if (!name || seen.has(name)) continue;
    seen.add(name);

    // Match species entry by first two words (genus + species)
    const sp2 = name.split(' ').slice(0, 2).join(' ');
    let spData = null;
    for (const s of speciesData.species) {
      if (s.name.split(' ').slice(0, 2).join(' ') === sp2) { spData = s; break; }
    }

    // For tailless species, sort so tailless-branch paths come first.
    // buildPathDisplay then picks the lowest-score path, which will be the tailless one.
    let paths = pathsMap.get(name) || [];
    if ((node.note || '').includes('Tailless')) {
      paths = [...paths].sort((a, b) => {
        const aOk = a.length > 0 && (a[0].choice || '').toLowerCase().includes('tailless');
        const bOk = b.length > 0 && (b[0].choice || '').toLowerCase().includes('tailless');
        return (bOk ? 1 : 0) - (aOk ? 1 : 0);
      });
    }

    index.push({
      name,
      common_name: node.common_name || (spData && spData.common_name) || '',
      note: node.note || '',
      taxon_photos: (spData && spData.taxon_photos) || [],
      inat_url: (spData && spData.inat_url)
        || `https://www.inaturalist.org/search?q=${encodeURIComponent(sp2)}`,
      paths,
      resultFeatures: node.features || {}
    });
  }

  return index.sort((a, b) => a.name.localeCompare(b.name));
}

// ===== Main render =====

function render() {
  const appEl = document.getElementById('app');
  const node = state.tree.nodes[state.currentNodeId];

  if (!node) {
    appEl.innerHTML = renderErrorCard(`Unknown node: "${state.currentNodeId}". Please restart.`);
    return;
  }

  const breadcrumbHTML = buildBreadcrumb();
  const progressHTML = buildProgressBar();

  let bodyHTML;
  if (node.type === 'question') {
    bodyHTML = renderQuestion(node);
  } else if (node.type === 'result') {
    bodyHTML = renderResult(node);
  } else if (node.type === 'group') {
    bodyHTML = renderGroup(node);
  } else {
    bodyHTML = renderErrorCard(`Unknown node type: "${node.type}"`);
  }

  appEl.innerHTML = `
    ${breadcrumbHTML}
    ${progressHTML}
    ${bodyHTML}
  `;

  // Attach event listeners after render
  const backBtn = appEl.querySelector('.back-btn');
  if (backBtn) backBtn.addEventListener('click', handleBack);

  const restartBtn = appEl.querySelector('.btn-restart');
  if (restartBtn) restartBtn.addEventListener('click', restart);

  const choicesEl = appEl.querySelector('.choices');
  if (choicesEl) {
    choicesEl.addEventListener('click', e => {
      const btn = e.target.closest('.choice-btn');
      if (btn) handleChoice(btn.dataset.label, btn.dataset.next);
    });
  }

  const speciesIdBtn = appEl.querySelector('.btn-species-id');
  if (speciesIdBtn) {
    speciesIdBtn.addEventListener('click', () => {
      handleChoice('Identify to species', speciesIdBtn.dataset.next);
    });
  }
}

// ===== Node renderers =====

function renderQuestion(node) {
  const hintHTML = node.hint
    ? `<p class="question-hint">${renderHint(node.hint)}</p>`
    : '';

  const guideLinkHTML = node.guide_link
    ? `<a href="${escapeAttr(node.guide_link)}" class="question-guide-link" target="_blank" rel="noopener">&#128247; Visual guide →</a>`
    : '';

  const choicesHTML = node.choices
    .map(c => `<button class="choice-btn" data-label="${escapeAttr(c.label)}" data-next="${escapeAttr(c.next)}">${escapeHtml(c.label)}</button>`)
    .join('');

  const qNum = state.questionNumbers && state.questionNumbers.has(node.question)
    ? `<span class="path-qnum">Q${state.questionNumbers.get(node.question)}</span> `
    : '';

  return `
    <div class="card">
      ${buildBackButton()}
      <h2 class="question-text">${qNum}${escapeHtml(node.question)}</h2>
      ${hintHTML}
      ${guideLinkHTML}
      <div class="choices">${choicesHTML}</div>
    </div>
  `;
}

function renderResult(node) {
  const species = node.taxon_id ? state.species.get(node.taxon_id) : null;

  const commonName = (species && species.common_name) || node.common_name
    || (species && species.name) || node.name || 'Unknown Species';
  const sciName = (species && species.name) || node.name || '';
  const inatUrl = (species && species.inat_url)
    || (node.taxon_id ? `https://www.inaturalist.org/taxa/${node.taxon_id}` : null)
    || `https://www.inaturalist.org/search?q=${encodeURIComponent(sciName || 'Nacaduba')}`;

  const noteHTML = node.note
    ? `<div class="id-note">${escapeHtml(node.note)}</div>`
    : '';

  const galleryHTML = buildPhotoGallery(species);

  return `
    <div class="card card--result">
      ${buildBackButton()}
      <span class="result-badge">Identification</span>
      <h2 class="species-common">${escapeHtml(commonName)}</h2>
      ${sciName ? `<p class="species-name">${escapeHtml(sciName)}</p>` : ''}
      ${noteHTML}
      ${galleryHTML}
      <div class="action-row">
        <a class="btn-inat" href="${escapeAttr(inatUrl)}" target="_blank" rel="noopener noreferrer">
          ${iconExternal()} View on iNaturalist
        </a>
        <button class="btn-restart">
          ${iconRestart()} Start Over
        </button>
      </div>
    </div>
  `;
}

function renderGroup(node) {
  const featuresHTML = node.key_features && node.key_features.length
    ? `<ul class="key-features">${node.key_features.map(f => `<li>${escapeHtml(f)}</li>`).join('')}</ul>`
    : '';

  const continueOrPending = node.next
    ? `<button class="btn-species-id" data-next="${escapeAttr(node.next)}">
         Identify to species ${iconArrowRight()}
       </button>`
    : node.no_next_note
    ? `<div class="species-pending">${iconPending()} ${escapeHtml(node.no_next_note)}</div>`
    : `<div class="species-pending">
         ${iconPending()} Species-level keys for this group will be added in the next update.
       </div>`;

  return `
    <div class="card card--group">
      ${buildBackButton()}
      <span class="result-badge result-badge--group">Group Identified</span>
      <h2 class="species-common">${escapeHtml(node.group_name)}</h2>
      <p class="group-description">${escapeHtml(node.description)}</p>
      ${featuresHTML}
      ${continueOrPending}
      <div class="action-row">
        <button class="btn-restart">
          ${iconRestart()} Start Over
        </button>
      </div>
    </div>
  `;
}

// ===== Hamburger menu =====

function initMenuListeners() {
  const overlay  = document.getElementById('menu-overlay');
  const closeBtn = document.getElementById('menu-close');
  const menuBtn  = document.getElementById('menu-btn');

  // Open menu via topbar button
  if (menuBtn) menuBtn.addEventListener('click', openMenu);

  // Close on backdrop click or close button
  closeBtn.addEventListener('click', closeMenu);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeMenu(); });

  // Keyboard: Escape closes
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && overlay.classList.contains('open')) closeMenu();
  });

  // Nav item listeners
  document.getElementById('nav-idkey').addEventListener('click', closeMenu);
}

function openMenu() {
  const overlay = document.getElementById('menu-overlay');
  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeMenu() {
  document.getElementById('menu-overlay').classList.remove('open');
  document.body.style.overflow = '';
}

function renderSearchList(query) {
  const q = (query || '').trim().toLowerCase();
  const countEl  = document.getElementById('search-count');
  const resultsEl = document.getElementById('search-results');

  const matches = q
    ? state.speciesIndex.filter(s =>
        s.name.toLowerCase().includes(q) ||
        (s.common_name && s.common_name.toLowerCase().includes(q)) ||
        (s.note && s.note.toLowerCase().includes(q)))
    : state.speciesIndex;

  countEl.textContent = q
    ? `${matches.length} match${matches.length !== 1 ? 'es' : ''}`
    : `${state.speciesIndex.length} species`;

  if (matches.length === 0) {
    resultsEl.innerHTML = `<p class="search-empty">No species found for "${escapeHtml(query)}"</p>`;
    return;
  }

  resultsEl.innerHTML = matches.map(s => `
    <button class="search-item" role="listitem" data-name="${escapeAttr(s.name)}">
      <span class="search-item-sci">${escapeHtml(s.name)}</span>
      ${s.common_name ? `<span class="search-item-common">${escapeHtml(s.common_name)}</span>` : ''}
    </button>
  `).join('');
}

function showSpeciesDetail(sp) {
  document.getElementById('search-pane').style.display = 'none';
  document.getElementById('back-to-search').style.display = '';

  const detailEl = document.getElementById('species-detail');
  detailEl.style.display = 'block';
  detailEl.scrollTop = 0;

  const noteHTML = sp.note
    ? `<div class="id-note">${escapeHtml(sp.note)}</div>`
    : '';

  const galleryHTML = buildPhotoGallery(sp);
  const pathHTML = buildPathDisplay(sp.paths, sp.note, sp.resultFeatures, sp.name);

  detailEl.innerHTML = `
    <span class="result-badge">Species Info</span>
    <h2 class="species-common">${escapeHtml(sp.common_name || sp.name)}</h2>
    ${sp.common_name ? `<p class="species-name">${escapeHtml(sp.name)}</p>` : ''}
    ${noteHTML}
    ${pathHTML}
    ${galleryHTML}
    <a class="btn-inat" href="${escapeAttr(sp.inat_url)}" target="_blank" rel="noopener noreferrer">
      ${iconExternal()} View on iNaturalist
    </a>
  `;
}

function buildPathDisplay(paths, note, resultFeatures, resultName) {
  if (!paths || paths.length === 0) return '';

  const rf = resultFeatures || {};
  const canonical = pickCanonicalPath(paths, note, rf);
  if (!canonical) return '';
  const simCd = (() => {
    if (!resultName) return null;
    if (state.simCdPaths && state.simCdPaths.has(resultName)) {
      return state.simCdPaths.get(resultName);
    }
    return state.tree
      ? buildSimulationCdPath(state.tree, pathApplyFeatures(canonical, rf), resultName)
      : null;
  })();

  const renderSteps = (path, applyRf) => (applyRf ? pathApplyFeatures(path, rf) : path).map(step => {
    if (step.group) {
      return `<li class="path-step path-step--group"><span class="path-group">● ${escapeHtml(step.group)}</span></li>`;
    }
    const isCd = step.choice && step.choice.startsWith('Cannot determine');
    const qn = state.questionNumbers && state.questionNumbers.has(step.question)
      ? `<span class="path-qnum">Q${state.questionNumbers.get(step.question)}</span> `
      : '';
    return `
      <li class="path-step${isCd ? ' path-step--skip' : ''}">
        <span class="path-q">${qn}${escapeHtml(step.question)}</span>
        <span class="path-a">↳ ${escapeHtml(step.choice)}</span>
      </li>`;
  }).join('');

  let html = `
    <details class="path-details">
      <summary class="path-summary">Direct path — ${canonical.length} step${canonical.length !== 1 ? 's' : ''}</summary>
      <div class="path-content">
        <ol class="path-steps">${renderSteps(canonical, true)}</ol>
      </div>
    </details>
  `;

  if (simCd) {
    html += `
      <details class="path-details path-details--simcd">
        <summary class="path-summary">Simulation CD path — ${simCd.length} step${simCd.length !== 1 ? 's' : ''}</summary>
        <div class="path-content">
          <p class="path-skip-note">Path when upperside and FW underside space 1–3 features are unavailable (all answered "Cannot determine").</p>
          <ol class="path-steps">${renderSteps(simCd, false)}</ol>
        </div>
      </details>
    `;
  }

  return html;
}

// ===== Photo gallery =====

function buildPhotoGallery(species) {
  if (!species || !species.taxon_photos || species.taxon_photos.length === 0) {
    return `
      <div class="photo-gallery">
        <div class="photo-placeholder">
          ${iconButterfly()}
          <span>No photo available</span>
        </div>
      </div>
    `;
  }

  const photos = species.taxon_photos.slice(0, 5);
  const items = photos.map(p => `
    <div class="photo-item">
      <img src="${escapeAttr(p.url)}" alt="${escapeAttr(species.name)}" loading="lazy">
      <span class="photo-attr">${escapeHtml(p.attribution)}</span>
    </div>
  `).join('');

  return `<div class="photo-gallery">${items}</div>`;
}

// ===== Navigation helpers =====

function buildBackButton() {
  const disabled = state.history.length === 0 ? ' disabled' : '';
  return `
    <button class="back-btn"${disabled}>
      ${iconBack()} Back
    </button>
  `;
}

function buildBreadcrumb() {
  if (state.history.length === 0) {
    return `<div class="breadcrumb-wrap"><div class="breadcrumb"><span class="crumb-start">Start</span></div></div>`;
  }

  const crumbs = state.history.map(h =>
    `<span class="sep">›</span><span class="crumb" title="${escapeAttr(h.choiceLabel)}">${escapeHtml(h.choiceLabel)}</span>`
  ).join('');

  return `
    <div class="breadcrumb-wrap">
      <div class="breadcrumb">
        <span class="crumb-start">Start</span>
        ${crumbs}
      </div>
    </div>
  `;
}

function buildProgressBar() {
  const pct = Math.min((state.history.length / ESTIMATED_MAX_DEPTH) * 100, 100);
  return `
    <div class="progress-bar-track">
      <div class="progress-bar-fill" style="width: ${pct}%"></div>
    </div>
  `;
}

function renderErrorCard(message) {
  return `
    <div class="card card--error">
      <div class="error-icon">⚠️</div>
      <h2>Something went wrong</h2>
      <p>${escapeHtml(message)}</p>
      <button class="btn-restart" onclick="restart()">
        ${iconRestart()} Start Over
      </button>
    </div>
  `;
}

function handleChoice(label, nextNodeId) {
  state.history.push({ nodeId: state.currentNodeId, choiceLabel: label });
  state.currentNodeId = nextNodeId;
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function handleBack() {
  if (state.history.length === 0) return;
  const prev = state.history.pop();
  state.currentNodeId = prev.nodeId;
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function restart() {
  state.history = [];
  state.currentNodeId = state.tree.start;
  render();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ===== Helpers =====

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Like escapeHtml but converts [text](https://...) to clickable links.
// Escapes plain-text segments individually so URLs are never pre-escaped.
function renderHint(str) {
  if (!str) return '';
  const re = /\[([^\]]+)\]\((https:\/\/[^)]+)\)/g;
  const out = [];
  let last = 0, m;
  while ((m = re.exec(str)) !== null) {
    out.push(escapeHtml(str.slice(last, m.index)));
    out.push(`<a href="${escapeAttr(m[2])}" target="_blank" rel="noopener">${escapeHtml(m[1])}</a>`);
    last = m.index + m[0].length;
  }
  out.push(escapeHtml(str.slice(last)));
  return out.join('');
}

function escapeAttr(str) {
  if (!str) return '';
  return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ===== SVG icons =====

function iconMenu() {
  return `<svg width="22" height="22" viewBox="0 0 22 22" fill="none" aria-hidden="true">
    <path d="M3 6h16M3 11h16M3 16h16" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  </svg>`;
}

function iconBack() {
  return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M10 3L5 8l5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function iconExternal() {
  return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M7 3H3a1 1 0 00-1 1v9a1 1 0 001 1h9a1 1 0 001-1V9" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
    <path d="M10 2h4v4M14 2L8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function iconRestart() {
  return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M2 8a6 6 0 106-6H5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>
    <path d="M2 4v4h4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function iconArrowRight() {
  return `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true" style="vertical-align:-2px">
    <path d="M3 8h10M9 4l4 4-4 4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function iconPending() {
  return `<svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true" style="vertical-align:-2px">
    <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 5v3.5l2 1.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function iconButterfly() {
  return `<svg width="40" height="40" viewBox="0 0 40 40" fill="none" aria-hidden="true">
    <ellipse cx="11" cy="16" rx="8" ry="6" fill="#d4e2d0" opacity="0.8"/>
    <ellipse cx="29" cy="16" rx="8" ry="6" fill="#d4e2d0" opacity="0.8"/>
    <ellipse cx="12" cy="26" rx="6" ry="5" fill="#d4e2d0" opacity="0.6"/>
    <ellipse cx="28" cy="26" rx="6" ry="5" fill="#d4e2d0" opacity="0.6"/>
    <line x1="20" y1="10" x2="20" y2="32" stroke="#6b7c6b" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M18 11 Q20 9 22 11" stroke="#6b7c6b" stroke-width="1.2" stroke-linecap="round" fill="none"/>
  </svg>`;
}

// ===== Species page =====

async function initSpeciesPage() {
  const loadingEl = document.getElementById('loading');
  const appEl = document.getElementById('species-app');
  try {
    const [treeRes, speciesRes, simCdRes] = await Promise.all([
      fetch('data/tree.json', { cache: 'no-cache' }),
      fetch('data/species.json', { cache: 'no-cache' }),
      fetch('data/sim_cd_paths.json', { cache: 'no-cache' })
    ]);
    if (!treeRes.ok || !speciesRes.ok) throw new Error('Failed to load data');
    const [treeData, speciesData] = await Promise.all([treeRes.json(), speciesRes.json()]);
    state.tree = treeData;
    state.speciesIndex = buildSpeciesIndex(treeData, speciesData);
    state.questionNumbers = buildQuestionNumbers(treeData);
    state.simCdPaths = simCdRes.ok ? new Map(Object.entries(await simCdRes.json())) : null;
    loadingEl.style.display = 'none';
    appEl.style.display = '';

    // Wire up search
    const input = document.getElementById('search-input');
    const results = document.getElementById('search-results');
    const backBtn = document.getElementById('back-to-results');
    let dt;
    input.addEventListener('input', e => {
      clearTimeout(dt);
      dt = setTimeout(() => {
        sessionStorage.setItem('sp-search', e.target.value);
        renderSearchList(e.target.value);
      }, 120);
    });
    // Restore previous search query from this session
    const savedQuery = sessionStorage.getItem('sp-search') || '';
    if (savedQuery) {
      input.value = savedQuery;
      renderSearchList(savedQuery);
    }
    results.addEventListener('click', e => {
      const btn = e.target.closest('.search-item');
      if (!btn) return;
      const sp = state.speciesIndex.find(s => s.name === btn.dataset.name);
      if (sp) {
        results.style.display = 'none';
        document.getElementById('search-count').style.display = 'none';
        input.style.display = 'none';
        if (backBtn) backBtn.style.display = '';
        showSpeciesDetailInline(sp);
      }
    });
    if (backBtn) backBtn.addEventListener('click', () => {
      document.getElementById('species-detail').style.display = 'none';
      if (appEl) appEl.classList.remove('species-view');
      const spPage = appEl && appEl.querySelector('.sp-page');
      if (spPage) spPage.style.maxWidth = '';
      results.style.display = '';
      document.getElementById('search-count').style.display = '';
      input.style.display = '';
      backBtn.style.display = 'none';
    });

    // Wire up menu
    const overlay = document.getElementById('menu-overlay');
    const closeBtn = document.getElementById('menu-close');
    const menuBtn = document.getElementById('menu-btn');
    if (menuBtn) menuBtn.addEventListener('click', () => { overlay.classList.add('open'); document.body.style.overflow = 'hidden'; });
    if (closeBtn) closeBtn.addEventListener('click', () => { overlay.classList.remove('open'); document.body.style.overflow = ''; });
    overlay.addEventListener('click', e => { if (e.target === overlay) { overlay.classList.remove('open'); document.body.style.overflow = ''; } });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && overlay.classList.contains('open')) { overlay.classList.remove('open'); document.body.style.overflow = ''; } });
    const navSpecies = document.getElementById('nav-species');
    if (navSpecies) navSpecies.addEventListener('click', () => { overlay.classList.remove('open'); document.body.style.overflow = ''; });

    renderSearchList('');
  } catch (err) {
    loadingEl.style.display = 'none';
    if (appEl) { appEl.style.display = ''; appEl.innerHTML = '<p style="padding:2rem">Could not load species data.</p>'; }
  }
}

function showSpeciesDetailInline(sp) {
  const appEl = document.getElementById('species-app');
  if (appEl) appEl.classList.add('species-view');
  const spPage = appEl && appEl.querySelector('.sp-page');
  if (spPage) spPage.style.maxWidth = 'none';
  const detailEl = document.getElementById('species-detail');
  detailEl.style.display = 'block';
  detailEl.scrollTop = 0;
  const noteHTML = sp.note ? `<div class="id-note">${escapeHtml(sp.note)}</div>` : '';
  detailEl.innerHTML = `
    <span class="result-badge">Species Info</span>
    <h2 class="species-common">${escapeHtml(sp.common_name || sp.name)}</h2>
    ${sp.common_name ? `<p class="species-name">${escapeHtml(sp.name)}</p>` : ''}
    ${noteHTML}
    ${buildPathDisplay(sp.paths, sp.note, sp.resultFeatures, sp.name)}
    <a class="btn-inat" href="${escapeAttr(sp.inat_url)}" target="_blank" rel="noopener noreferrer">
      ${iconExternal()} View on iNaturalist
    </a>
  `;
}

document.addEventListener('DOMContentLoaded', () => {
  if (document.getElementById('species-app')) initSpeciesPage();
  else init();
});
