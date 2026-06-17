// path-utils.js — canonical-path algorithm shared by app.js and checklist.js
//
// Both the ID-key direct path display and the Feature Scoring page must agree on
// which path is "canonical" for each species.  Keeping a single copy here ensures
// a change to the scoring logic is automatically reflected in both.

const ESCAPE_HATCHES = [
  'None of the camdeo features present',
  'HW spot 6 appears midway between spot 5 and the end-cell bar',
];

function isEscapeHatch(choice) {
  return choice && ESCAPE_HATCHES.some(eh => choice.startsWith(eh));
}

// Score a path — lower is better (canonical = lowest score).
// +1 per "Cannot determine" step, +1 per escape-hatch step.
// +100 when the path starts on the wrong tailed/tailless branch.
function pathScore(path, note) {
  const lc = (note || '').toLowerCase();
  const resultIsTailed    = /^tailed/.test(lc);
  const resultIsNotTailed = /^tailless/.test(lc);
  let score = path.filter(s => s.choice && s.choice.startsWith('Cannot determine')).length;
  score    += path.filter(s => isEscapeHatch(s.choice)).length;
  if (path.length > 0) {
    const startsTailed    = path[0].choice === 'Yes — hindwing is tailed';
    const startsNotTailed = path[0].choice === 'No — hindwing is tailless';
    if (startsTailed    && path.some(s => s.choice && /tailless/i.test(s.choice))) score += 100;
    if (startsNotTailed && resultIsTailed)    score += 100;
    if (startsTailed    && resultIsNotTailed) score += 100;
  }
  return score;
}

// Returns 1 if any definite answer in the path contradicts the result's explicit
// features; 0 otherwise.  Used as a tiebreaker so the biologically correct path
// beats a DFS-order artefact when two choices lead to the same next node.
function pathIsInconsistent(path, resultFeatures) {
  for (const step of path) {
    if (!step.question || !step.choice) continue;
    if (step.choice.startsWith('Cannot determine')) continue;
    const expected = resultFeatures[step.question];
    if (expected && !expected.startsWith('Cannot determine') && step.choice !== expected) return 1;
  }
  return 0;
}

// Override path display answers with explicit result-node features.
function pathApplyFeatures(path, resultFeatures) {
  if (!resultFeatures || Object.keys(resultFeatures).length === 0) return path;
  return path.map(step =>
    (step.question &&
     resultFeatures[step.question] &&
     !resultFeatures[step.question].startsWith('Cannot determine'))
      ? { ...step, choice: resultFeatures[step.question] }
      : step
  );
}

// Position of the first CD step in a path (used to prefer deeper CD paths).
function pathCdDepth(path) {
  return path.findIndex(s => s.choice && s.choice.startsWith('Cannot determine'));
}

// Select the canonical path: lowest (score, inconsistency, length).
function pickCanonicalPath(paths, note, resultFeatures) {
  if (!paths || paths.length === 0) return null;
  const rf = resultFeatures || {};
  const sorted = [...paths].sort((a, b) =>
    pathScore(a, note) - pathScore(b, note) ||
    pathIsInconsistent(a, rf) - pathIsInconsistent(b, rf) ||
    a.length - b.length
  );
  return sorted.find(p => pathScore(p, note) < 100) || sorted[0];
}

// Select the fallback (CD) path for display alongside the direct path.
// Picks the path with one more CD step than canonical, preferring the one
// whose CD step occurs deepest (= a designed bypass rather than an early skip).
function pickFallbackPath(paths, note, resultFeatures) {
  if (!paths || paths.length === 0) return null;
  const rf = resultFeatures || {};
  const canonical = pickCanonicalPath(paths, note, rf);
  if (!canonical) return null;
  const canonicalScore = pathScore(canonical, note);
  const pool = paths.filter(p =>
    pathScore(p, note) < 100 &&
    pathScore(p, note) > canonicalScore &&
    !p.some(s => isEscapeHatch(s.choice))
  );
  pool.sort((a, b) =>
    pathScore(a, note)          - pathScore(b, note)          ||
    pathIsInconsistent(a, rf)   - pathIsInconsistent(b, rf)   ||
    pathCdDepth(b)              - pathCdDepth(a)              ||
    a.length                    - b.length
  );
  const fallback = pool[0] || null;
  if (!fallback) return null;
  if (JSON.stringify(fallback) === JSON.stringify(canonical)) return null;
  return fallback;
}

// ── Simulation CD path ────────────────────────────────────────────────────────

// Returns true if the question is about upperside features or spaces 1–3 on the
// underside — features that a field observer using only a photo may not be able
// to assess reliably.
// Pass the node's choices array (optional) to also catch questions whose text
// doesn't say "upperside" but whose CD choice label does (e.g. border-width Qs).
function isSimCdQuestion(question, choices) {
  const q = (question || '').toLowerCase();
  if (q.includes('upperside') || q.includes('upper side') || /\bspace [123][ab]?\b/.test(q))
    return true;
  if (Array.isArray(choices)) {
    return choices.some(c =>
      c.label && c.label.startsWith('Cannot determine') && /upperside/i.test(c.label));
  }
  return false;
}

// Walk the tree from root to resultName, choosing the "Cannot determine" answer
// for any question where isSimCdQuestion() is true (if a CD choice exists),
// and the canonical answer for all other questions.
// Returns the path array, or null if the target cannot be reached this way.
function buildSimulationCdPath(treeData, canonicalPath, resultName) {
  if (!treeData || !canonicalPath || !resultName) return null;
  const nodes = treeData.nodes;

  const canonicalAnswers = new Map();
  for (const step of canonicalPath) {
    if (step.question && step.choice) canonicalAnswers.set(step.question, step.choice);
  }

  function walk(nodeId, path, visited) {
    if (visited.has(nodeId)) return null;
    const node = nodes[nodeId];
    if (!node) return null;
    const vis2 = new Set(visited); vis2.add(nodeId);

    if (node.type === 'result') {
      return (node.name || '') === resultName ? path : null;
    }
    if (node.type === 'group') {
      const step = { group: node.group_name };
      if (node.next) return walk(node.next, [...path, step], vis2);
      if (node.member_results) {
        for (const rid of node.member_results) {
          const rn = nodes[rid];
          if (rn && rn.name === resultName) return [...path, step];
        }
      }
      return null;
    }
    if (node.type === 'question') {
      const choices = node.choices || [];
      if (isSimCdQuestion(node.question, choices)) {
        const cdChoice = choices.find(c => c.label && c.label.startsWith('Cannot determine'));
        if (cdChoice && cdChoice.next) {
          const found = walk(cdChoice.next, [...path, { question: node.question, choice: cdChoice.label }], vis2);
          if (found) return found;
        }
        // CD choice not available or didn't reach target — no sim-CD path.
        return null;
      }
      // Try canonical answer first; fall back to DFS for questions reached only via a CD detour.
      const realAnswer = canonicalAnswers.get(node.question);
      if (realAnswer) {
        const c = choices.find(ch => ch.label === realAnswer);
        if (c && c.next) {
          const found = walk(c.next, [...path, { question: node.question, choice: realAnswer }], vis2);
          if (found) return found;
        }
      }
      for (const c of choices) {
        if (!c.next) continue;
        if (c.label && c.label.startsWith('Cannot determine')) continue;
        if (realAnswer && c.label === realAnswer) continue;
        const found = walk(c.next, [...path, { question: node.question, choice: c.label }], vis2);
        if (found) return found;
      }
      return null;
    }
    return null;
  }

  const result = walk(treeData.start, [], new Set());
  if (!result) return null;
  if (JSON.stringify(result) === JSON.stringify(canonicalPath)) return null;
  return result;
}

// ── Tree traversal ────────────────────────────────────────────────────────────

// DFS from tree root; returns Map<resultName, Array<path>>.
// Each path is an array of {question, choice} steps (with {group} milestones).
function buildTreePaths(treeData) {
  const nodes = treeData.nodes;
  const pathsMap = new Map();

  function dfs(nodeId, path, visited) {
    if (visited.has(nodeId)) return;
    const node = nodes[nodeId];
    if (!node) return;
    const vis2 = new Set(visited);
    vis2.add(nodeId);

    if (node.type === 'result') {
      const name = node.name || '';
      if (name) {
        if (!pathsMap.has(name)) pathsMap.set(name, []);
        pathsMap.get(name).push([...path]);
      }
      return;
    }
    if (node.type === 'question') {
      for (const c of (node.choices || []))
        if (c.next) dfs(c.next, [...path, { question: node.question, choice: c.label }], vis2);
      return;
    }
    if (node.type === 'group') {
      const step = { group: node.group_name };
      if (node.next) {
        dfs(node.next, [...path, step], vis2);
      } else if (node.member_results && node.member_results.length) {
        for (const resultId of node.member_results) {
          const rNode = nodes[resultId];
          if (rNode && rNode.name) {
            if (!pathsMap.has(rNode.name)) pathsMap.set(rNode.name, []);
            pathsMap.get(rNode.name).push([...path, step]);
          }
        }
      }
    }
  }

  dfs(treeData.start, [], new Set());
  return pathsMap;
}

// ── Feature Scoring pure functions ───────────────────────────────────────────
// These take all state as explicit parameters so they can be called identically
// from the browser (checklist.js wraps them with cs.* args) and from Node.js
// (compute_sim_cd_paths.js passes local variables directly).  One implementation,
// zero drift between the simulation script and the live app.

// Score all species given current answers and the feature matrix.
// Returns a sorted array of { name, score, max } (highest match% first).
function scoreAllPure(answers, featureMatrix) {
  if (answers.size === 0)
    return [...featureMatrix.keys()].map(n => ({ name: n, score: 0, max: 0 }));
  return [...featureMatrix.entries()].map(([name, features]) => {
    let score = 0, max = 0;
    for (const [q, ans] of answers) {
      if (ans.startsWith('Cannot determine')) continue;
      max += 2;
      if (features.has(q)) score += features.get(q) === ans ? 2 : -1;
    }
    return { name, score, max };
  }).sort((a, b) => {
    const pctA = a.max > 0 ? a.score / a.max : 0;
    const pctB = b.max > 0 ? b.score / b.max : 0;
    return pctB - pctA || a.name.localeCompare(b.name);
  });
}

// Compute the ordered list of questions to display given current state.
//
// questionOrder is a MUTABLE array that carries state across renders:
//   • pass an empty array [] for the first render (function fills it)
//   • pass the same array on subsequent renders (function updates in-place)
//
// Returns the cdFollowups Set (useful for callers that need it, e.g. the sim).
function getDisplayQuestionsPure(answers, scores, featureMatrix, treeNodes, questionOrder) {
  // ── top candidate pool ──
  let topNames;
  if (answers.size === 0 || scores.every(s => s.score === 0)) {
    topNames = [...featureMatrix.keys()];
  } else {
    const topPct = scores[0].max > 0 ? scores[0].score / scores[0].max : 0;
    topNames = scores
      .filter(s => (s.max > 0 ? s.score / s.max : 0) >= topPct)
      .map(s => s.name);
  }

  // ── diversity & filtered coverage ──
  const diversity   = new Map();
  const filteredCov = new Map();
  for (const name of topNames) {
    for (const [q, c] of (featureMatrix.get(name) || new Map())) {
      if (!diversity.has(q)) diversity.set(q, new Set());
      diversity.get(q).add(c);
      filteredCov.set(q, (filteredCov.get(q) || 0) + 1);
    }
  }

  const touched       = q => answers.has(q);
  const top1Features  = (answers.size > 0 && scores.length > 0)
    ? (featureMatrix.get(scores[0].name) || new Map()) : new Map();

  // ── CD-followup insertion ──
  // When a question was answered CD, surface the tree's next question on that
  // branch so the user can score an alternative character.  Only insert when
  // all nodes sharing the question text agree on the same CD destination
  // (ambiguous multi-node cases must not inject a spurious followup).
  const cdFollowups = new Set();
  if (treeNodes) {
    for (const [q, choice] of answers) {
      if (!choice.startsWith('Cannot determine')) continue;
      const candidates = new Set();
      for (const node of Object.values(treeNodes)) {
        if (node.type !== 'question' || node.question !== q) continue;
        const cdChoice = node.choices.find(c => c.label === choice);
        if (!cdChoice || !cdChoice.next) continue;
        const follow = treeNodes[cdChoice.next];
        if (follow && follow.type === 'question') candidates.add(follow.question);
      }
      if (candidates.size === 1) cdFollowups.add([...candidates][0]);
    }
  }

  // ── candidate question pool ──
  const allQ = [...diversity.entries()]
    .filter(([q, choices]) => touched(q) || choices.size >= 2 || top1Features.has(q) || cdFollowups.has(q))
    .map(([q]) => q);
  const allQSet = new Set(allQ);

  // Sort helper: underside morphology before upperside, then by filtered coverage
  const newQSort = (a, b) => {
    const aUpper = /upperside/i.test(a);
    const bUpper = /upperside/i.test(b);
    if (aUpper !== bUpper) return aUpper ? 1 : -1;
    return (filteredCov.get(b) || 0) - (filteredCov.get(a) || 0);
  };

  if (questionOrder.length === 0) {
    // First render: establish stable initial order
    questionOrder.push(...allQ.slice().sort(newQSort));
  } else {
    // Keep existing order; remove unanswered questions that are no longer relevant
    const filtered = questionOrder.filter(q => touched(q) || allQSet.has(q));
    questionOrder.length = 0;
    questionOrder.push(...filtered);
    const existing = new Set(questionOrder);

    // Move/insert CD-followup questions right after the deepest answered CD question
    if (cdFollowups.size > 0) {
      const cdPositions = [...answers.entries()]
        .filter(([, ac]) => ac.startsWith('Cannot determine'))
        .map(([aq]) => questionOrder.indexOf(aq))
        .filter(i => i !== -1);
      if (cdPositions.length > 0) {
        const insertAt = Math.max(...cdPositions) + 1;
        for (const q of cdFollowups) {
          if (!allQSet.has(q)) continue;
          const curIdx = questionOrder.indexOf(q);
          if (curIdx === -1) {
            questionOrder.splice(insertAt, 0, q);
            existing.add(q);
          } else if (curIdx > insertAt) {
            questionOrder.splice(curIdx, 1);
            questionOrder.splice(insertAt, 0, q);
          }
        }
      }
    }
    // Append newly relevant questions at the end
    const newQs = allQ.filter(q => !existing.has(q)).sort(newQSort);
    if (newQs.length) questionOrder.push(...newQs);
  }

  return cdFollowups;
}

// Assigns a stable Q-number to each unique question text in DFS encounter order.
function buildQuestionNumbers(treeData) {
  const nodes = treeData.nodes;
  const numbers = new Map();
  let n = 0;
  const seen = new Set();

  function dfs(id) {
    if (seen.has(id)) return;
    const node = nodes[id];
    if (!node) return;
    seen.add(id);
    if (node.type === 'question') {
      if (!numbers.has(node.question)) numbers.set(node.question, ++n);
      for (const c of (node.choices || [])) if (c.next) dfs(c.next);
    } else if (node.type === 'group') {
      if (node.next) dfs(node.next);
    }
  }

  dfs(treeData.start);
  return numbers;
}

// ── Node.js export ────────────────────────────────────────────────────────────
// Ignored by browsers (module is undefined); picked up by compute_sim_cd_paths.js.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    isSimCdQuestion,
    scoreAllPure,
    getDisplayQuestionsPure,
    buildTreePaths,
    buildQuestionNumbers,
    pickCanonicalPath,
    pickFallbackPath,
  };
}
