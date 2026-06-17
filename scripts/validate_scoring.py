#!/usr/bin/env python3
"""
validate_scoring.py — Feature-scoring simulation for all species.

For every species, checks three types of paths through the tree:

  CANONICAL  — the single shortest / lowest-penalty path (score 0)
  CONSISTENT — score-0 alternate paths where every definite answer matches
               the canonical feature matrix (no wrong answers, different route)
  CD-ALT     — score-1 paths that take exactly one "Cannot determine" step;
               the most important real-world alternate: user can't see a feature

Expectation: the species should rank #1 (or tied #1) on every checked path.
Unresolved group results (names containing "subgroup", "group —") are
expected to be beaten by their constituent species; failures there are noted
but not counted as bugs.

Exit code:
  0 — all individual species rank #1 on every checked path
  1 — one or more individual species fail

Usage:
  python3 scripts/validate_scoring.py            # failures + summary
  python3 scripts/validate_scoring.py --verbose  # all species
  python3 scripts/validate_scoring.py --species "Nacaduba major major"
  python3 scripts/validate_scoring.py --cd-depth 2   # check 2-CD paths too
"""

import json
import re
import sys
import argparse
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
TREE_FILE    = REPO_ROOT / 'data' / 'tree.json'
ESCAPE_HATCHES = [
    'None of the camdeo features present',
    'HW spot 6 appears midway between spot 5 and the end-cell bar',
]

# ── Tree path building (mirrors checklist.js buildTreePaths) ──────────────────

def build_tree_paths(tree: dict) -> dict[str, list]:
    nodes = tree['nodes']
    result_map: dict[str, list] = {}

    def dfs(nid: str, path: list, vis: frozenset):
        if nid in vis:
            return
        node = nodes.get(nid)
        if not node:
            return
        v2 = vis | {nid}
        ntype = node['type']
        if ntype == 'result':
            name = node.get('name', '')
            if name:
                result_map.setdefault(name, []).append(list(path))
            return
        if ntype == 'question':
            for c in node.get('choices', []):
                if c.get('next'):
                    dfs(c['next'],
                        path + [{'question': node['question'], 'choice': c['label']}],
                        v2)
            return
        if ntype == 'group' and node.get('next'):
            dfs(node['next'],
                path + [{'group': node.get('group_name', '')}],
                v2)

    dfs(tree['start'], [], frozenset())
    return result_map


# ── Path scoring (mirrors checklist.js pathScore) ────────────────────────────

def path_score(path: list, note: str = '') -> int:
    lc    = (note or '').lower()
    score = sum(1 for s in path if s.get('choice', '').startswith('Cannot determine'))
    score += sum(1 for s in path
                 if any(s.get('choice', '').startswith(eh) for eh in ESCAPE_HATCHES))
    first             = path[0].get('choice', '') if path else ''
    starts_tailed     = first == 'Yes — hindwing is tailed'
    starts_not_tailed = first == 'No — hindwing is tailless'
    if starts_not_tailed and lc.startswith('tailed'):   score += 100
    if starts_tailed     and lc.startswith('tailless'): score += 100
    if starts_tailed and any('tailless' in s.get('choice', '').lower() for s in path):
        score += 100
    return score


# ── Canonical path selection (mirrors path-utils.js pickCanonicalPath) ───────

def is_inconsistent(path: list, rf: dict) -> int:
    for step in path:
        q, c = step.get('question'), step.get('choice', '')
        if not q or not c or c.startswith('Cannot determine'):
            continue
        expected = rf.get(q)
        if expected and not expected.startswith('Cannot determine') and c != expected:
            return 1
    return 0


def pick_canonical_path(paths: list, note: str, rf: dict) -> list:
    if not paths:
        return []
    scored = sorted(
        ((path_score(p, note), is_inconsistent(p, rf), len(p), p) for p in paths),
        key=lambda x: (x[0], x[1], x[2])
    )
    best = next((p for s, _, __, p in scored if s < 100), scored[0][3] if scored else [])
    return best


# ── Feature matrix (mirrors checklist.js initData + path-utils.js) ───────────

def build_feature_matrix(tree: dict, paths_map: dict) -> dict[str, dict]:
    nodes = tree['nodes']
    result_features = {
        n['name']: n['features']
        for n in nodes.values()
        if n.get('type') == 'result' and n.get('name') and n.get('features')
    }
    result_notes = {
        n['name']: n.get('note', '')
        for n in nodes.values()
        if n.get('type') == 'result' and n.get('name')
    }
    matrix: dict[str, dict] = {}
    for name, paths in paths_map.items():
        note = result_notes.get(name, '')
        rf   = result_features.get(name, {})
        best = pick_canonical_path(paths, note, rf)
        features: dict[str, str] = {}
        for step in best:
            q, c = step.get('question'), step.get('choice')
            if q and c and not c.startswith('Cannot determine'):
                features[q] = c
        for q, c in rf.items():
            if c.startswith('Cannot determine'):
                features.pop(q, None)
            else:
                features[q] = c
        matrix[name] = features
    return matrix


# ── Key-path / feature-matrix cross-check ────────────────────────────────────

def check_keypath_matrix_sync(paths_map: dict, matrix: dict, tree: dict) -> list[str]:
    """
    For every species, compare the key-path canonical answers (what buildPathDisplay
    would show) against the feature-matrix entries (what Feature Scoring uses).
    Returns a list of mismatch description strings; empty list = all in sync.
    """
    nodes = tree['nodes']
    result_features = {
        n['name']: n.get('features', {})
        for n in nodes.values()
        if n.get('type') == 'result' and n.get('name')
    }
    result_notes = {
        n['name']: n.get('note', '')
        for n in nodes.values()
        if n.get('type') == 'result' and n.get('name')
    }
    mismatches = []
    for name, paths in paths_map.items():
        note = result_notes.get(name, '')
        rf   = result_features.get(name, {})
        canonical = pick_canonical_path(paths, note, rf)
        # Apply features overrides (mirrors pathApplyFeatures)
        display_answers = {}
        for step in canonical:
            q, c = step.get('question'), step.get('choice', '')
            if not q or not c or c.startswith('Cannot determine'):
                continue
            override = rf.get(q, '')
            display_answers[q] = override if (override and not override.startswith('Cannot determine')) else c
        feat_row = matrix.get(name, {})
        for q, display_ans in display_answers.items():
            if q in feat_row and feat_row[q] != display_ans:
                mismatches.append(
                    f'  {name}\n'
                    f'    Q: {q[:80]}\n'
                    f'    key path : {display_ans[:70]}\n'
                    f'    feat matrix: {feat_row[q][:70]}'
                )
    return mismatches


# ── Scoring (mirrors checklist.js scoreAll) ───────────────────────────────────

def score_all(matrix: dict, answers: dict) -> list[tuple]:
    results = []
    for name, features in matrix.items():
        sc = mx = 0
        for q, ans in answers.items():
            if ans.startswith('Cannot determine'):
                continue
            mx += 2
            if q in features:
                sc += 2 if features[q] == ans else -1
        pct = sc / mx if mx > 0 else 0.0
        results.append((pct, sc, mx, name))
    results.sort(key=lambda x: (-x[0], x[3]))
    return results


# ── Path classification helpers ───────────────────────────────────────────────

def answers_key(path: list) -> frozenset:
    return frozenset(
        (s['question'], s['choice'])
        for s in path
        if s.get('question') and s.get('choice')
    )


def is_consistent(path: list, canonical_features: dict) -> bool:
    """
    True if every DEFINITE answer (non-CD) in path matches canonical_features
    for that question — or the question isn't in canonical_features at all.
    """
    for step in path:
        q = step.get('question')
        c = step.get('choice')
        if q and c and not c.startswith('Cannot determine'):
            if q in canonical_features and canonical_features[q] != c:
                return False
    return True


def get_paths_by_category(paths: list, note: str,
                          canonical_features: dict,
                          max_cd_depth: int = 1) -> dict[str, list]:
    """
    Classify paths into:
      canonical   — single best score-0 path (shortest)
      consistent  — other score-0 paths with no canonical contradictions
      cd_alt      — score 1..max_cd_depth paths that are otherwise consistent
    Each category is a deduplicated list of paths.
    """
    seen: set[frozenset] = set()
    categories: dict[str, list] = {'canonical': [], 'consistent': [], 'cd_alt': []}

    # Sort by (path_score, inconsistent_flag, length) so consistent paths come
    # first among ties — this ensures the canonical simulation path matches the
    # feature matrix even when two score-0 paths differ only in a question where
    # the result node's features override the path answer.
    def _inconsistent(p: list) -> int:
        return 0 if is_consistent(p, canonical_features) else 1

    scored = sorted(((path_score(p, note), _inconsistent(p), len(p), i, p)
                     for i, p in enumerate(paths)),
                    key=lambda x: (x[0], x[1], x[2]))

    canonical_set = False
    for sc, _inc, _ln, _i, p in scored:
        if sc >= 100:
            continue
        ak = answers_key(p)
        if ak in seen:
            continue
        seen.add(ak)

        consistent = is_consistent(p, canonical_features)
        if sc == 0:
            if not canonical_set:
                categories['canonical'].append(p)
                canonical_set = True
            elif consistent:
                categories['consistent'].append(p)
        elif 0 < sc <= max_cd_depth and consistent:
            categories['cd_alt'].append(p)

    return categories


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate(name: str, path: list, matrix: dict) -> dict:
    answers = {
        s['question']: s['choice']
        for s in path
        if s.get('question') and s.get('choice')
    }
    scores    = score_all(matrix, answers)
    top_pct   = scores[0][0] if scores else 0.0
    top_names = [s[3] for s in scores if s[0] >= top_pct]
    rank      = next((i + 1 for i, s in enumerate(scores) if s[3] == name), None)
    sc        = next((s[1] for s in scores if s[3] == name), 0)
    mx        = next((s[2] for s in scores if s[3] == name), 0)
    return {'rank': rank, 'score': sc, 'max': mx,
            'n_answers': len(answers), 'top_names': top_names}


# ── Per-species validation ────────────────────────────────────────────────────

def validate_species(name: str, paths: list, matrix: dict,
                     note: str, max_cd_depth: int = 1,
                     max_per_cat: int = 5) -> dict:
    canon_features = matrix.get(name, {})
    cats = get_paths_by_category(paths, note, canon_features, max_cd_depth)

    results: dict[str, list] = {'canonical': [], 'consistent': [], 'cd_alt': []}
    failures: list[dict]     = []

    for cat in ('canonical', 'consistent', 'cd_alt'):
        for p in cats[cat][:max_per_cat]:
            sim = simulate(name, p, matrix)
            sim['category'] = cat
            results[cat].append(sim)
            if sim['rank'] is None or name not in sim['top_names']:
                failures.append(sim)

    return {'results': results, 'failures': failures,
            'n_consistent': len(cats['consistent']),
            'n_cd_alt': len(cats['cd_alt'])}


# ── Formatting ────────────────────────────────────────────────────────────────

def pct_str(sc: int, mx: int) -> str:
    return f'{round(sc / mx * 100):3d}%' if mx > 0 else ' n/a'


def is_group_result(name: str) -> bool:
    """Unresolved group results are expected to be beaten by individual species."""
    lc = name.lower()
    return ('subgroup —' in lc or 'group —' in lc or
            'unresolved' in lc or name.startswith('Muta ') or
            name.startswith('Alea ') or name.startswith('Camdeo ') or
            name.startswith('Epimuta ') or name.startswith('Ganesa '))


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--verbose', '-v', action='store_true')
    parser.add_argument('--species', '-s', metavar='NAME')
    parser.add_argument('--cd-depth', type=int, default=1,
                        help='Max CD steps in CD-alt paths (default 1)')
    parser.add_argument('--max-per-cat', type=int, default=5,
                        help='Max alternates checked per category (default 5)')
    parser.add_argument('--show-top', type=int, default=3)
    args = parser.parse_args()

    with open(TREE_FILE) as f:
        tree = json.load(f)

    print('Building paths…', flush=True)
    paths_map = build_tree_paths(tree)

    print('Building feature matrix…', flush=True)
    matrix = build_feature_matrix(tree, paths_map)

    # Cross-check: key-path display answers must match feature-matrix entries.
    # A mismatch means the ID key and Feature Scoring disagree on a species answer.
    sync_mismatches = check_keypath_matrix_sync(paths_map, matrix, tree)
    if sync_mismatches:
        print(f'\n{"━"*72}')
        print(f'KEY-PATH ↔ FEATURE-MATRIX SYNC FAILURES ({len(sync_mismatches)})\n')
        for m in sync_mismatches:
            print(m)
    else:
        print('Key-path ↔ feature-matrix sync: ✓ all in sync')

    result_notes: dict[str, str] = {
        n['name']: n.get('note', '')
        for n in tree['nodes'].values()
        if n.get('type') == 'result' and n.get('name')
    }

    species_list = sorted(paths_map.keys())
    if args.species:
        pat = args.species.lower()
        species_list = [s for s in species_list if pat in s.lower()]
        if not species_list:
            print(f'No species matching {args.species!r}', file=sys.stderr)
            return 1

    total      = len(species_list)
    individual = [s for s in species_list if not is_group_result(s)]
    groups     = [s for s in species_list if is_group_result(s)]

    # Counters — individual species only
    n_canon_ok  = 0
    n_alt_ok    = 0   # species where ALL checked alternates pass
    n_alt_total = 0   # species that have at least one alternate
    all_failures: list[tuple[str, dict]] = []

    W = 50
    print(f'\nValidating {total} entries ({len(individual)} species + {len(groups)} group results)…\n')
    print(f'  Path types checked: CANONICAL, CONSISTENT-ALT, CD-ALT (depth ≤ {args.cd_depth})\n')
    print(f'{"Species":<{W}}  {"Canon":6}  {"Alt":6}  Notes')
    print('─' * (W + 40))

    for name in species_list:
        note   = result_notes.get(name, '')
        paths  = paths_map.get(name, [])
        res    = validate_species(name, paths, matrix, note,
                                  max_cd_depth=args.cd_depth,
                                  max_per_cat=args.max_per_cat)

        canon_list   = res['results']['canonical']
        alt_list     = res['results']['consistent'] + res['results']['cd_alt']
        failures     = res['failures']
        is_grp       = is_group_result(name)

        canon_ok = bool(canon_list) and canon_list[0]['rank'] == 1
        alt_fails = [a for a in alt_list if name not in a.get('top_names', [])]
        alt_ok    = not alt_fails

        if not is_grp:
            if canon_ok:
                n_canon_ok += 1
            if alt_list:
                n_alt_total += 1
                if alt_ok:
                    n_alt_ok += 1

        species_ok = canon_ok and alt_ok
        if not species_ok:
            all_failures.append((name, res))

        if not species_ok or args.verbose:
            c = canon_list[0] if canon_list else None
            a = alt_fails[0]  if alt_fails  else (alt_list[0] if alt_list else None)
            canon_str = (f"#{c['rank']} {pct_str(c['score'], c['max'])}"
                         if c else 'none')
            alt_str   = (f"#{a['rank']} {pct_str(a['score'], a['max'])}"
                         if a else ' —   ')
            notes_parts = []
            if not canon_ok and c:
                beaten = [n for n in c['top_names'] if n != name][:args.show_top]
                notes_parts.append(f'beaten by {beaten}')
            if alt_fails:
                beaten = [n for n in alt_fails[0]['top_names'] if n != name][:args.show_top]
                cat    = alt_fails[0]['category']
                notes_parts.append(f'{cat} beaten by {beaten}')
            notes_str = '; '.join(notes_parts)
            grp_tag   = ' [group]' if is_grp else ''
            tick      = '✓' if species_ok else ('~' if is_grp else '✗')
            print(f'{tick} {name:<{W}}  {canon_str:6}  {alt_str:6}  {notes_str}{grp_tag}')

    # ── Detailed failure report ───────────────────────────────────────────────
    indiv_failures = [(n, r) for n, r in all_failures if not is_group_result(n)]
    group_failures = [(n, r) for n, r in all_failures if is_group_result(n)]

    if indiv_failures:
        print(f'\n{"━"*72}')
        print('INDIVIDUAL SPECIES FAILURES — detail\n')
        for name, res in indiv_failures:
            print(f'  {name}')
            for cat in ('canonical', 'consistent', 'cd_alt'):
                for entry in res['results'][cat]:
                    rank    = entry['rank']
                    in_top  = name in entry.get('top_names', [])
                    ok      = '✓' if in_top else '✗'
                    sc_s    = f"{entry['score']:+d}/{entry['max']}"
                    pct     = pct_str(entry['score'], entry['max'])
                    beaten  = [n for n in entry['top_names'] if n != name][:args.show_top]
                    suffix  = (f'  tied with {beaten}' if in_top and rank > 1 and beaten
                               else (f'  beaten by {beaten}' if not in_top else ''))
                    print(f'    {ok} [{cat:<10}] rank=#{rank}  {sc_s} ({pct}){suffix}')
            print()

    if group_failures and args.verbose:
        print(f'\n{"━"*72}')
        print('GROUP RESULT FAILURES (expected — groups are beaten by constituents)\n')
        for name, res in group_failures:
            print(f'  ~ {name}')

    # ── Summary ──────────────────────────────────────────────────────────────
    n_indiv      = len(individual)
    n_indiv_fail = sum(1 for n, _ in all_failures if not is_group_result(n))
    print(f'\n{"━"*72}')
    print('SUMMARY')
    print(f'  Individual species checked  : {n_indiv}')
    can_fail = n_indiv - n_canon_ok
    print(f'  Canonical path → rank #1    : {n_canon_ok} / {n_indiv}  '
          + ('✓' if can_fail == 0 else f'✗  {can_fail} fail'))
    alt_fail = n_alt_total - n_alt_ok
    print(f'  Species with checked alts   : {n_alt_total}')
    print(f'  Alt paths all → rank #1     : {n_alt_ok} / {n_alt_total}  '
          + ('✓' if alt_fail == 0 else f'✗  {alt_fail} fail'))
    print(f'  All paths clean             : {n_indiv - n_indiv_fail} / {n_indiv}  '
          + ('✓' if n_indiv_fail == 0 else f'✗  {n_indiv_fail} species need attention'))
    grp_fail = sum(1 for n, _ in all_failures if is_group_result(n))
    if grp_fail:
        print(f'  Group results w/ failures   : {grp_fail} (expected; not counted as bugs)')
    sync_ok = len(sync_mismatches) == 0
    print(f'  Key-path ↔ matrix sync      : '
          + ('✓' if sync_ok else f'✗  {len(sync_mismatches)} mismatch(es)'))

    return 0 if (n_indiv_fail == 0 and sync_ok) else 1


if __name__ == '__main__':
    sys.exit(main())
