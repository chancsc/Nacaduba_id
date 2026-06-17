#!/usr/bin/env python3
"""
audit_paths.py — Audit canonical paths used by both the ID Key and Feature Scoring.

CHECKS
------
1. Canonical path quality (score 0–99 / ≥100)
   score 0  — clean direct path (ideal)
   score 1+ — has CD or escape-hatch step
   ≥100     — contradiction (tailed/tailless mismatch)

2. CD coverage gaps
   For every CD choice on a species' canonical path, verify that the species
   is still reachable from the CD destination.  If not, following the CD
   bypass abandons the species entirely — a silent dead end.

3. Orphaned nodes
   Nodes present in tree.json but unreachable from the start node.
   These are dead code and often indicate a routing error.

BACKGROUND
----------
The app has two identification tools that both derive from data/tree.json:

  1. ID Key (index.html / js/app.js)
     A step-by-step decision tree. For each result species it displays:
       • Direct path   — the canonical route with the fewest ambiguities
       • CD path       — an alternative route using "Cannot determine" answers
                         when a feature is hidden in the photo

  2. Feature Scoring (checklist.html / js/checklist.js)
     A parallel scoring mode. Each species is pre-mapped to a set of
     observable features (question → expected answer) derived from its
     canonical path. When the user marks features in their photo the species
     whose features match best rises to the top.

Both tools select the same "canonical path" using the same scoring algorithm.
This script checks that every species' canonical path is clean and consistent.

SCORING ALGORITHM
-----------------
Each path through the tree receives a penalty score:

  +1  per "Cannot determine" answer
  +1  per escape-hatch answer (camdeo-group bypass)
  +100 contradiction — tailed/tailless mismatch

The lowest-score path is canonical.  Paths scoring ≥100 are excluded.

PATH QUALITY FLAGS
------------------
  [CD]           canonical path contains a Cannot-determine answer.
                 Expected only for unresolved species groups.
  [ESC]          canonical path uses the camdeo escape-hatch choice.
                 Indicates a tree routing bug.
  [TAILED-CONTR] path branch contradicts the result note.
                 Indicates a structural tree error.
  [FEAT]         result node has manual feature overrides (intentional).

ESCAPE-HATCH LABELS
-------------------
The camdeo escape-hatch is a special "none of these" choice in q_camdeo_sub.
The ESCAPE_HATCHES list below covers all known label variants.

USAGE
-----
    python scripts/audit_paths.py              # uses data/tree.json
    python scripts/audit_paths.py path/to/tree.json

Exit code: 0 = all checks pass, 1 = issues found.

EXPECTED BASELINE (June 2026)
------------------------------
  score 0 (clean)       : 108 / 114
  score 1–99 (CD/ESC)   :   6 / 114  — all unresolved species groups
  score ≥100 (error)    :   0 / 114
  CD coverage gaps      :   0
  Orphaned nodes        :   1  — q_amph_s1 (superseded by split choice)
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
TREE_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT / 'data' / 'tree.json'

# All known label prefixes for the camdeo escape-hatch choice in q_camdeo_sub.
# Add new entries here whenever q_camdeo_sub's last choice is reworded.
ESCAPE_HATCHES = [
    'None of the camdeo features present',
    'HW spot 6 appears midway between spot 5 and the end-cell bar',
]

# Node IDs that are intentionally unreachable (superseded dead code).
# Listed here so the orphan check doesn't report them as unexpected.
KNOWN_ORPHANS = {
    'q_amph_s1',        # tail-presence question, replaced by explicit tailed/tailless
                        # choices in q_amphimuta_sub
    'g_camdeo_camdeo',  # superseded by g_camdeo_general; identical next pointer
}


def is_escape(choice: str) -> bool:
    return any(choice.startswith(eh) for eh in ESCAPE_HATCHES) if choice else False


# ── Tree traversal ────────────────────────────────────────────────────────────

def build_all_paths(nodes: dict, start: str) -> dict:
    """
    DFS from `start`; return {species_name: [path, ...]} where each path is a
    list of step dicts:
        {'node_id': str, 'question': str, 'choice': str}  — question nodes
        {'node_id': str, 'group':    str}                 — group nodes

    node_id is included so callers can look up the originating node for each
    step (e.g. to find its CD choices for coverage checks).
    """
    result_paths: dict[str, list] = {}

    def dfs(node_id: str, path: list, visited: frozenset):
        if node_id in visited:
            return
        node = nodes.get(node_id)
        if not node:
            return
        visited = visited | {node_id}

        ntype = node['type']

        if ntype == 'result':
            name = node.get('name', '')
            if name:
                result_paths.setdefault(name, []).append(list(path))
            return

        if ntype == 'question':
            for c in node.get('choices') or []:
                if c.get('next'):
                    dfs(c['next'],
                        path + [{'node_id': node_id,
                                 'question': node['question'],
                                 'choice':   c['label']}],
                        visited)
            return

        if ntype == 'group':
            if node.get('next'):
                dfs(node['next'],
                    path + [{'node_id': node_id,
                              'group':   node.get('group_name', '')}],
                    visited)
            elif node.get('member_results'):
                for rid in node['member_results']:
                    rn = nodes.get(rid)
                    if rn and rn.get('name'):
                        result_paths.setdefault(rn['name'], []).append(
                            list(path) + [{'node_id': node_id,
                                           'group':   node.get('group_name', '')}])

    dfs(start, [], frozenset())
    return result_paths


def collect_result_nodes(nodes: dict) -> dict:
    return {n['name']: n
            for n in nodes.values()
            if n.get('type') == 'result' and n.get('name')}


def visited_node_ids(nodes: dict, start: str) -> set:
    """Return all node IDs reachable from `start` (any type)."""
    seen = set()

    def dfs(node_id):
        if node_id in seen:
            return
        seen.add(node_id)
        node = nodes.get(node_id)
        if not node:
            return
        if node['type'] == 'question':
            for c in node.get('choices') or []:
                if c.get('next'):
                    dfs(c['next'])
        elif node['type'] == 'group':
            if node.get('next'):
                dfs(node['next'])
            for rid in node.get('member_results') or []:
                dfs(rid)

    dfs(start)
    return seen


# ── Reachability map ──────────────────────────────────────────────────────────

def build_reachable_species(nodes: dict) -> dict:
    """
    For every node ID, compute the frozenset of species names reachable from it
    via any path through the tree (ignoring per-branch visited constraints, so
    this is a global reachability map).

    Used by check_cd_coverage to ask: "if the user chooses CD here, is species
    S still reachable in the subtree that follows?"
    """
    cache: dict[str, frozenset] = {}

    def reachable(node_id: str) -> frozenset:
        if node_id in cache:
            return cache[node_id]
        # Sentinel to break potential cycles before computing.
        cache[node_id] = frozenset()
        node = nodes.get(node_id)
        if not node:
            return frozenset()

        result: frozenset = frozenset()
        ntype = node['type']

        if ntype == 'result':
            name = node.get('name', '')
            result = frozenset([name]) if name else frozenset()
            # Group-level "unresolved" results carry covers_results — a list of
            # individual result node IDs that this result represents.  Follow them
            # so the CD-coverage check recognises the species as reachable.
            for rid in node.get('covers_results') or []:
                covered = nodes.get(rid)
                if covered and covered.get('name'):
                    result = result | frozenset([covered['name']])

        elif ntype == 'question':
            for c in node.get('choices') or []:
                if c.get('next'):
                    result = result | reachable(c['next'])

        elif ntype == 'group':
            if node.get('next'):
                result = result | reachable(node['next'])
            for rid in node.get('member_results') or []:
                rn = nodes.get(rid)
                if rn and rn.get('name'):
                    result = result | frozenset([rn['name']])

        cache[node_id] = result
        return result

    for nid in nodes:
        reachable(nid)

    return cache


# ── Path scoring ──────────────────────────────────────────────────────────────

def path_score(path: list, note: str) -> int:
    """
    Score a candidate path.  Lower is better; ≥100 means the path is invalid.
    Mirrors skipCount in js/app.js and pathScore in js/checklist.js.
    Keep all three in sync.
    """
    lc = (note or '').lower()
    result_is_tailed     = lc.startswith('tailed')
    result_is_not_tailed = lc.startswith('tailless')

    score = sum(1 for s in path if s.get('choice', '').startswith('Cannot determine'))
    score += sum(1 for s in path if is_escape(s.get('choice', '')))

    first_choice  = path[0].get('choice', '') if path else ''
    starts_tailed = first_choice == 'Yes — hindwing is tailed'
    starts_notail = first_choice == 'No — hindwing is tailless'

    if starts_tailed and any(re.search(r'tailless', s.get('choice', ''), re.I)
                              for s in path):
        score += 100
    if starts_notail and result_is_tailed:
        score += 100
    if starts_tailed and result_is_not_tailed:
        score += 100

    return score


def pick_canonical(paths: list, note: str) -> list:
    """Select the best (lowest-score, non-contradicting) canonical path."""
    scored = sorted(((path_score(p, note), i, p) for i, p in enumerate(paths)),
                    key=lambda x: x[0])
    best = next((p for s, _, p in scored if s < 100), None)
    return best if best is not None else (scored[0][2] if scored else [])


# ── Path inspection helpers ───────────────────────────────────────────────────

def path_flags(path: list, note: str) -> list[str]:
    flags = []
    if any(s.get('choice', '').startswith('Cannot determine') for s in path):
        flags.append('CD')
    if any(is_escape(s.get('choice', '')) for s in path):
        flags.append('ESC')
    lc    = (note or '').lower()
    first = path[0].get('choice', '') if path else ''
    if first == 'Yes — hindwing is tailed'  and lc.startswith('tailless'):
        flags.append('TAILED-CONTR')
    if first == 'No — hindwing is tailless' and lc.startswith('tailed'):
        flags.append('TAILED-CONTR')
    return flags


def cd_questions(path: list) -> list[str]:
    return [s['question'][:80] for s in path
            if s.get('choice', '').startswith('Cannot determine')]


def escape_questions(path: list) -> list[str]:
    return [s['question'][:80] for s in path if is_escape(s.get('choice', ''))]


# ── CD coverage gap check ─────────────────────────────────────────────────────

def check_cd_coverage(nodes: dict,
                      all_paths: dict,
                      result_map: dict,
                      reachable_map: dict) -> list[tuple]:
    """
    For every species, walk its canonical path and find every question node
    that carries a "Cannot determine" choice.  Then verify that the species
    is still reachable from the CD destination.

    Returns a list of (species_name, node_id, question_text, cd_dest_id) tuples
    for each gap found.

    Example gap (fixed June 2026):
        q_amph_s4  CD → q_amph_s6 (q_amph_s7 subtree)
        A. major was only reachable via q_amph_s4 "Yes" → q_amph_broad.
        Following CD led to a subtree that did not include A. major.
    """
    gaps = []

    for name in sorted(all_paths):
        paths    = all_paths[name]
        note     = result_map.get(name, {}).get('note', '')
        canon    = pick_canonical(paths, note)

        for step in canon:
            node_id = step.get('node_id')
            if not node_id:
                continue
            node = nodes.get(node_id)
            if not node or node['type'] != 'question':
                continue

            # Check every CD choice on this node.
            for c in node.get('choices') or []:
                label = c.get('label', '')
                if not label.startswith('Cannot determine'):
                    continue
                cd_dest = c.get('next')
                if not cd_dest:
                    continue

                reachable_via_cd = reachable_map.get(cd_dest, frozenset())
                if name not in reachable_via_cd:
                    gaps.append((name,
                                 node_id,
                                 node['question'],
                                 cd_dest))

    return gaps


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    with open(TREE_PATH) as f:
        tree = json.load(f)

    nodes      = tree['nodes']
    start      = tree['start']
    all_paths  = build_all_paths(nodes, start)
    result_map = collect_result_nodes(nodes)
    reachable  = build_reachable_species(nodes)

    # ── Check 1: Canonical path quality ──────────────────────────────────────
    by_score: dict[int, list] = defaultdict(list)
    total = 0

    for name in sorted(all_paths):
        paths    = all_paths[name]
        rnode    = result_map.get(name, {})
        note     = rnode.get('note', '')
        has_feat = bool(rnode.get('features'))
        total   += 1

        canon = pick_canonical(paths, note)
        score = path_score(canon, note)
        flags = path_flags(canon, note)
        if has_feat:
            flags.append('FEAT')

        by_score[min(score, 100)].append((name, score, len(canon), flags, canon))

    clean  = len(by_score.get(0, []))
    impure = sum(len(v) for k, v in by_score.items() if 0 < k < 100)
    contra = len(by_score.get(100, []))

    # ── Check 2: CD coverage gaps ─────────────────────────────────────────────
    cd_gaps = check_cd_coverage(nodes, all_paths, result_map, reachable)

    # ── Check 3: Orphaned nodes ───────────────────────────────────────────────
    visited     = visited_node_ids(nodes, start)
    all_ids     = set(nodes.keys())
    orphans     = sorted(all_ids - visited - KNOWN_ORPHANS)
    known_found = sorted(all_ids - visited) # include known ones in count

    # ── Report ────────────────────────────────────────────────────────────────
    any_issue = impure or contra or cd_gaps or orphans

    print(f'Canonical path audit — {total} species\n')
    print(f'  [1] Path quality')
    print(f'      score 0  (clean)        : {clean} / {total}')
    print(f'      score 1–99 (CD/ESC)     : {impure} / {total}')
    print(f'      score ≥100 (error)      : {contra} / {total}')
    print(f'  [2] CD coverage gaps        : {len(cd_gaps)}')
    print(f'  [3] Orphaned nodes          : {len(orphans)} unexpected'
          f'  ({len(known_found) - len(orphans)} known)')
    print()

    if not any_issue:
        print('✓ All checks pass.')
        return 0

    # Detail: imperfect paths
    impure_entries = [x for k, v in by_score.items() if 1 <= k < 100 for x in v]
    if impure_entries:
        print('── [1] Imperfect paths (score 1–99) ─────────────────────────────────────\n')
        for name, score, length, flags, canon in sorted(impure_entries):
            tag = ' '.join(f'[{f}]' for f in flags)
            print(f'  {name}  {tag}  (score={score}, len={length})')
            for q in cd_questions(canon):
                print(f'    CD:  {q}')
            for q in escape_questions(canon):
                print(f'    ESC: {q}')
        print()

    if contra:
        print('── [1] Contradictions (score ≥100) ──────────────────────────────────────\n')
        for name, score, length, flags, canon in by_score[100]:
            tag   = ' '.join(f'[{f}]' for f in flags)
            first = canon[0].get('choice', '') if canon else '—'
            print(f'  {name}  {tag}')
            print(f'    starts with: {first[:80]}')
        print()

    if cd_gaps:
        print('── [2] CD coverage gaps ─────────────────────────────────────────────────\n')
        print('  A species is unreachable if the user selects Cannot Determine at the')
        print('  node shown.  The CD choice routes to a subtree that does not contain')
        print('  that species.\n')
        for name, node_id, question, cd_dest in cd_gaps:
            print(f'  {name}')
            print(f'    node : {node_id}')
            print(f'    Q    : {question[:80]}')
            print(f'    CD → : {cd_dest}  (species not reachable from here)')
        print()

    if orphans:
        print('── [3] Unexpected orphaned nodes ────────────────────────────────────────\n')
        print('  These nodes exist in tree.json but are unreachable from the start.\n')
        for nid in orphans:
            node = nodes[nid]
            label = node.get('question') or node.get('group_name') or node.get('name') or ''
            print(f'  {nid}  ({node["type"]})  {label[:70]}')
        print()

    return 1


if __name__ == '__main__':
    sys.exit(main())
