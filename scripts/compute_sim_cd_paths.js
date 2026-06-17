#!/usr/bin/env node
'use strict';
/**
 * Compute Simulation CD paths for all species via Feature Scoring simulation.
 *
 * For each species, simulates the Feature Scoring flow answering:
 *   • "Cannot determine" for any question about upperside features or spaces 1–3
 *   • The canonical answer for all other questions
 *
 * Outputs data/sim_cd_paths.json — a dict keyed by result name, each value a list
 * of {question, choice} steps in the order Feature Scoring would present them.
 *
 * Shares scoreAllPure and getDisplayQuestionsPure directly from js/path-utils.js,
 * so the simulation is guaranteed to mirror the live browser behaviour exactly.
 *
 * Usage: node scripts/compute_sim_cd_paths.js
 */

const fs   = require('fs');
const path = require('path');

const {
  isSimCdQuestion,
  scoreAllPure,
  getDisplayQuestionsPure,
  buildTreePaths,
  buildQuestionNumbers,
  pickCanonicalPath,
} = require('../js/path-utils.js');

const TREE_PATH   = path.join(__dirname, '../data/tree.json');
const OUTPUT_PATH = path.join(__dirname, '../data/sim_cd_paths.json');

// ── Feature matrix ────────────────────────────────────────────────────────────

function buildFeatureMatrix(treeData, pathsMap) {
  const nodes = treeData.nodes;
  const qMeta = new Map();
  const qCov  = new Map();
  const resultNotes    = new Map();
  const resultFeatures = new Map();

  for (const node of Object.values(nodes)) {
    if (node.type === 'question') {
      const choices = (node.choices || []).map(c => c.label);
      if (!qMeta.has(node.question)) {
        qMeta.set(node.question, { choices, hint: node.hint || '' });
      } else {
        const ex = qMeta.get(node.question);
        for (const l of choices) if (!ex.choices.includes(l)) ex.choices.push(l);
      }
    }
    if (node.type === 'result' && node.name) {
      resultNotes.set(node.name, node.note || '');
      if (node.features) resultFeatures.set(node.name, node.features);
    }
  }

  const matrix = new Map();
  for (const [name, paths] of pathsMap) {
    const note = resultNotes.get(name) || '';
    const rf   = resultFeatures.get(name) || {};
    const canonical = pickCanonicalPath(paths, note, rf) || [];

    const features = new Map();
    const covSeen  = new Set();
    for (const step of canonical) {
      const { question: q, choice: c } = step;
      if (q && c && !c.startsWith('Cannot determine') && !step.group) {
        features.set(q, c);
        if (!covSeen.has(q)) { covSeen.add(q); qCov.set(q, (qCov.get(q) || 0) + 1); }
      }
    }
    // Apply explicit features override from result node
    for (const [q, c] of Object.entries(rf)) {
      if (c.startsWith('Cannot determine')) {
        features.delete(q);
      } else {
        if (!features.has(q)) qCov.set(q, (qCov.get(q) || 0) + 1);
        features.set(q, c);
      }
    }
    matrix.set(name, features);
  }

  return { matrix, qMeta, qCov, resultNotes };
}

// ── Sim-CD path computation ───────────────────────────────────────────────────

function getCdLabel(nodes, questionText) {
  for (const node of Object.values(nodes)) {
    if (node.type === 'question' && node.question === questionText) {
      const c = (node.choices || []).find(c => c.label && c.label.startsWith('Cannot determine'));
      if (c) return c.label;
    }
  }
  return null;
}

function computeSimCdPath(resultName, matrix, treeNodes, canonicalAnswers) {
  if (!canonicalAnswers || canonicalAnswers.size === 0) return null;

  // Build question → choices lookup so isSimCdQuestion can inspect CD choice labels
  const qChoicesMap = new Map();
  for (const node of Object.values(treeNodes)) {
    if (node.type === 'question' && !qChoicesMap.has(node.question))
      qChoicesMap.set(node.question, node.choices || []);
  }

  // Build sim-CD answers: replace sim-CD questions with their CD label
  const simAnswers = new Map();
  for (const [q, answer] of canonicalAnswers) {
    if (isSimCdQuestion(q, qChoicesMap.get(q))) {
      const cdLabel = getCdLabel(treeNodes, q);
      simAnswers.set(q, cdLabel || answer);
    } else {
      simAnswers.set(q, answer);
    }
  }

  // Augment simAnswers with inferred answers for CD-followup questions.
  // When a sim-CD question Q has canonical answer C → node X, and Q's CD branch
  // leads to followup question F whose non-CD choice also → X, add that choice
  // for F.  This handles cases like Q88[CD] → Q89 (FW apex check): non-corinda
  // species answer Q88 "No" → Q80, so when Q88 is answered CD, we can infer
  // Q89 "No" (also → Q80) and include it in the sim path.
  for (const node of Object.values(treeNodes)) {
    if (node.type !== 'question') continue;
    const qText = node.question;
    if (!simAnswers.has(qText)) continue;
    if (!simAnswers.get(qText).startsWith('Cannot determine')) continue;
    const canonicalAns = canonicalAnswers.get(qText);
    if (!canonicalAns || canonicalAns.startsWith('Cannot determine')) continue;
    const canonicalChoice = (node.choices || []).find(c => c.label === canonicalAns);
    if (!canonicalChoice || !canonicalChoice.next) continue;
    const canonicalNext = canonicalChoice.next;
    const cdChoice = (node.choices || []).find(c => c.label && c.label.startsWith('Cannot determine'));
    if (!cdChoice || !cdChoice.next) continue;
    const followNode = treeNodes[cdChoice.next];
    if (!followNode || followNode.type !== 'question') continue;
    const followQText = followNode.question;
    if (simAnswers.has(followQText)) continue;
    // Don't pre-fill answers for follow questions that are themselves sim-CD
    if (isSimCdQuestion(followQText, followNode.choices || [])) continue;
    for (const fc of (followNode.choices || [])) {
      if (fc.next === canonicalNext && !(fc.label && fc.label.startsWith('Cannot determine'))) {
        simAnswers.set(followQText, fc.label);
        break;
      }
    }
  }

  // Simulate Feature Scoring using the same functions as the browser
  const answers       = new Map();
  const questionOrder = [];           // mutable state for getDisplayQuestionsPure
  const simPath       = [];
  const simCdQs       = new Set([...simAnswers.entries()]
    .filter(([, a]) => a.startsWith('Cannot determine')).map(([q]) => q));

  for (let step = 0; step < 50; step++) {
    const scores = scoreAllPure(answers, matrix);
    getDisplayQuestionsPure(answers, scores, matrix, treeNodes, questionOrder);

    // Find the first unanswered question in the visible 15-cap window that
    // this species can answer — either from simAnswers or as a sim-CD question
    // that the user can't see (upperside / space 1–3).
    let nextQ = null, nextAns = null, seen = 0;
    for (const q of questionOrder) {
      if (answers.has(q)) continue;
      if (++seen > 15) break;
      if (simAnswers.has(q)) {
        nextQ = q; nextAns = simAnswers.get(q); break;
      }
      if (isSimCdQuestion(q, qChoicesMap.get(q))) {
        const cdLabel = getCdLabel(treeNodes, q);
        if (cdLabel) { nextQ = q; nextAns = cdLabel; simCdQs.add(q); break; }
      }
    }
    if (nextQ === null) break;

    answers.set(nextQ, nextAns);
    simPath.push({ question: nextQ, choice: nextAns });

    // Stop once species is uniquely #1 by at least 2 points and all sim-CD questions answered.
    // After the gap >= 2 threshold is met, also continue if the species hasn't reached its
    // maximum possible score yet AND there are still unanswered own-feature questions visible
    // in the window. This ensures that confirmatory features (e.g. Q38–Q41 for A. agaba) are
    // included without inflating paths for species already at max score.
    const newScores = scoreAllPure(answers, matrix);
    if (newScores.length > 0 && newScores[0].name === resultName &&
        (newScores.length < 2 || newScores[0].score >= newScores[1].score + 2)) {
      if ([...simCdQs].every(q => answers.has(q))) {
        const atMax = newScores[0].score >= newScores[0].max;
        if (atMax) break;
        // Refresh window so questions unlocked by the last answer are visible.
        getDisplayQuestionsPure(answers, newScores, matrix, treeNodes, questionOrder);
        const ownLeft = questionOrder
          .filter(q => !answers.has(q)).slice(0, 15)
          .filter(q => simAnswers.has(q)).length;
        if (ownLeft === 0) break;
      }
    }
  }

  if (simPath.length === 0) return null;

  // If identical to the direct canonical path, nothing to show
  const canonicalPath = simPath
    .filter(s => canonicalAnswers.has(s.question))
    .map(s => ({ question: s.question, choice: canonicalAnswers.get(s.question) }));
  if (JSON.stringify(simPath) === JSON.stringify(canonicalPath)) return null;

  return simPath;
}

// ── Main ──────────────────────────────────────────────────────────────────────

function main() {
  const treeData = JSON.parse(fs.readFileSync(TREE_PATH, 'utf8'));

  console.log('Building tree paths and feature matrix...');
  const pathsMap = buildTreePaths(treeData);
  const { matrix, qMeta, resultNotes } = buildFeatureMatrix(treeData, pathsMap);
  console.log(`  ${matrix.size} species, ${qMeta.size} questions`);

  const treeNodes = treeData.nodes;
  const qNumbers  = buildQuestionNumbers(treeData);

  const simCdPaths = {};
  let hasPath = 0;

  // Iterate in DFS encounter order (stable across runs)
  const seenNames = new Set();
  for (const node of Object.values(treeNodes)) {
    if (node.type !== 'result' || !node.name || seenNames.has(node.name)) continue;
    seenNames.add(node.name);

    const canonicalAnswers = matrix.get(node.name);
    if (!canonicalAnswers) continue;

    const p = computeSimCdPath(node.name, matrix, treeNodes, canonicalAnswers);
    if (p) { simCdPaths[node.name] = p; hasPath++; }
  }

  console.log(`\nSim-CD paths: ${hasPath} of ${seenNames.size} species\n`);

  // Sample printout
  const sample = 'Nacaduba major major';
  if (simCdPaths[sample]) {
    console.log(`=== Sample: ${sample} ===`);
    for (const s of simCdPaths[sample]) {
      const qn = qNumbers.get(s.question) || '?';
      const cd = s.choice.startsWith('Cannot determine') ? ' [CD]' : '';
      console.log(`  Q${qn}: ${s.question.slice(0, 65)}${cd}`);
      console.log(`       -> ${s.choice.slice(0, 70)}`);
    }
  }

  fs.writeFileSync(OUTPUT_PATH, JSON.stringify(simCdPaths, null, 2));
  console.log(`\nWrote ${Object.keys(simCdPaths).length} paths to ${OUTPUT_PATH}`);
}

main();
