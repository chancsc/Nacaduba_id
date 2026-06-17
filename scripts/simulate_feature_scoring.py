#!/usr/bin/env python3
"""
Simulate the Feature Scoring page (checklist.js) for A. horsfieldi's CD path.
Mirrors JS logic exactly: buildTreePaths, pathScore, isInconsistent, initData,
scoreAll, getDisplayQuestions.

Usage: python scripts/simulate_feature_scoring.py
"""

import json
import sys
from collections import defaultdict

TREE_PATH = 'data/tree.json'
SPECIES_PATH = 'data/species.json'

ESCAPE_HATCHES = [
    'None of the camdeo features present',
    'HW spot 6 appears midway between spot 5 and the end-cell bar',
]

def is_escape_hatch(c):
    return c and any(c.startswith(eh) for eh in ESCAPE_HATCHES)

def path_score(p, note):
    lc = (note or '').lower()
    result_is_tailed     = lc.startswith('tailed')
    result_is_not_tailed = lc.startswith('tailless')
    score = sum(1 for s in p if s.get('choice','').startswith('Cannot determine'))
    score += sum(1 for s in p if is_escape_hatch(s.get('choice','')))
    if p:
        starts_tailed     = p[0].get('choice','') == 'Yes — hindwing is tailed'
        starts_not_tailed = p[0].get('choice','') == 'No — hindwing is tailless'
        if starts_tailed and any('tailless' in s.get('choice','').lower() for s in p):
            score += 100
        if starts_not_tailed and result_is_tailed:
            score += 100
        if starts_tailed and result_is_not_tailed:
            score += 100
    return score

def build_tree_paths(td):
    nodes = td['nodes']
    result_map = defaultdict(list)
    def dfs(node_id, path, vis):
        if node_id in vis:
            return
        node = nodes.get(node_id)
        if not node:
            return
        v2 = set(vis); v2.add(node_id)
        ntype = node.get('type')
        if ntype == 'result':
            name = node.get('name', '')
            if name:
                result_map[name].append(list(path))
            return
        if ntype == 'question':
            for c in (node.get('choices') or []):
                if c.get('next'):
                    dfs(c['next'], path + [{'question': node['question'], 'choice': c['label']}], v2)
            return
        if ntype == 'group' and node.get('next'):
            dfs(node['next'], path + [{'group': node.get('group_name','')}], v2)
    dfs(td['start'], [], set())
    return dict(result_map)

def build_question_numbers(td):
    nodes = td['nodes']
    numbers = {}
    n = [0]
    seen = set()
    def dfs(node_id):
        if node_id in seen:
            return
        node = nodes.get(node_id)
        if not node:
            return
        seen.add(node_id)
        if node.get('type') == 'question':
            q = node['question']
            if q not in numbers:
                n[0] += 1
                numbers[q] = n[0]
            for c in (node.get('choices') or []):
                if c.get('next'):
                    dfs(c['next'])
        elif node.get('type') == 'group' and node.get('next'):
            dfs(node['next'])
    dfs(td['start'])
    return numbers

def init_data(tree_data, species_data):
    """Returns (feature_matrix, question_meta, question_coverage, question_numbers, result_notes, tree_nodes)"""
    nodes = tree_data['nodes']
    paths_map = build_tree_paths(tree_data)

    q_meta = {}   # question_text -> {choices: [], hint: ''}
    q_cov  = {}   # question_text -> int
    result_notes = {}

    for node in nodes.values():
        if node.get('type') == 'question':
            q = node['question']
            all_choices = [c['label'] for c in (node.get('choices') or [])]
            if q not in q_meta:
                q_meta[q] = {'choices': all_choices, 'hint': node.get('hint','') }
            else:
                for l in all_choices:
                    if l not in q_meta[q]['choices']:
                        q_meta[q]['choices'].append(l)
        if node.get('type') == 'result' and node.get('name'):
            result_notes[node['name']] = node.get('note','')

    result_features_map = {}
    for node in nodes.values():
        if node.get('type') == 'result' and node.get('name') and node.get('features'):
            result_features_map[node['name']] = node['features']

    matrix = {}  # name -> {question -> choice}

    for name, paths in paths_map.items():
        note = result_notes.get(name, '')
        rf   = result_features_map.get(name, {})

        def is_inconsistent(p):
            for step in p:
                if not step.get('question') or not step.get('choice'):
                    continue
                if step['choice'].startswith('Cannot determine'):
                    continue
                expected = rf.get(step['question'])
                if expected and not expected.startswith('Cannot determine') and step['choice'] != expected:
                    return 1
            return 0

        scored = sorted(
            [(path_score(p, note), is_inconsistent(p), len(p), p) for p in paths],
            key=lambda x: (x[0], x[1], x[2])
        )
        best = next((x for x in scored if x[0] < 100), scored[0]) if scored else None
        canonical = best[3] if best else []

        features = {}
        cov_seen = set()
        for step in canonical:
            q = step.get('question')
            c = step.get('choice','')
            if q and c and not c.startswith('Cannot determine'):
                features[q] = c
                if q not in cov_seen:
                    cov_seen.add(q)
                    q_cov[q] = q_cov.get(q, 0) + 1

        # Merge explicit result features
        for q, c in rf.items():
            if c.startswith('Cannot determine'):
                features.pop(q, None)
            else:
                if q not in features:
                    q_cov[q] = q_cov.get(q, 0) + 1
                features[q] = c

        matrix[name] = features

    q_numbers = build_question_numbers(tree_data)
    return matrix, q_meta, q_cov, q_numbers, result_notes, nodes

def score_all(answers, feature_matrix):
    if not answers:
        return [{'name': n, 'score': 0, 'max': 0} for n in feature_matrix]
    results = []
    for name, features in feature_matrix.items():
        score, max_ = 0, 0
        for q, ans in answers.items():
            if ans.startswith('Cannot determine'):
                continue
            max_ += 2
            if q in features:
                score += 2 if features[q] == ans else -1
        results.append({'name': name, 'score': score, 'max': max_})
    results.sort(key=lambda x: (-(x['score']/x['max']) if x['max'] > 0 else 0, x['name']))
    return results

def get_display_questions(answers, scores, feature_matrix, q_meta, q_cov, tree_nodes, question_order_ref):
    """
    Returns (visible_questions, question_order).
    question_order_ref: list to use/update (mutable, pass [] first time).
    """
    # Build topNames
    if not answers or all(s['score'] == 0 for s in scores):
        top_names = list(feature_matrix.keys())
    else:
        top_score = scores[0]
        top_pct = top_score['score'] / top_score['max'] if top_score['max'] > 0 else 0
        top_names = [s['name'] for s in scores if (s['score']/s['max'] if s['max'] > 0 else 0) >= top_pct]

    # Build diversity map and filtered coverage
    diversity = {}  # question -> set of choices
    filtered_cov = {}
    for name in top_names:
        for q, c in feature_matrix.get(name, {}).items():
            if q not in diversity:
                diversity[q] = set()
            diversity[q].add(c)
            filtered_cov[q] = filtered_cov.get(q, 0) + 1

    touched = set(answers.keys())

    # top-1 features
    top1_features = set()
    if answers and scores:
        top1_features = set(feature_matrix.get(scores[0]['name'], {}).keys())

    # CD followups
    cd_followups = set()
    if tree_nodes:
        for q, choice in answers.items():
            if not choice.startswith('Cannot determine'):
                continue
            for node in tree_nodes.values():
                if node.get('type') != 'question' or node.get('question') != q:
                    continue
                cd_choice = next((c for c in (node.get('choices') or []) if c['label'] == choice), None)
                if not cd_choice or not cd_choice.get('next'):
                    continue
                follow = tree_nodes.get(cd_choice['next'])
                if follow and follow.get('type') == 'question':
                    cd_followups.add(follow['question'])
                break

    # Candidate pool
    all_q = [q for q, choices in diversity.items()
             if q in touched or len(choices) >= 2 or q in top1_features or q in cd_followups]
    all_q_set = set(all_q)

    def new_q_sort_key(q):
        is_upper = 'upperside' in q.lower()
        return (1 if is_upper else 0, -(filtered_cov.get(q, 0)))

    if not question_order_ref:
        question_order_ref[:] = sorted(all_q, key=new_q_sort_key)
    else:
        question_order_ref[:] = [q for q in question_order_ref if q in touched or q in all_q_set]
        existing = set(question_order_ref)
        # CD-followup: move/insert right after deepest answered CD question
        if cd_followups:
            cd_positions = [question_order_ref.index(aq) for aq, ac in answers.items()
                            if ac.startswith('Cannot determine') and aq in question_order_ref]
            if cd_positions:
                insert_at = max(cd_positions) + 1
                for q in list(cd_followups):
                    if q not in all_q_set:
                        continue
                    cur_idx = question_order_ref.index(q) if q in question_order_ref else -1
                    if cur_idx == -1:
                        question_order_ref.insert(insert_at, q)
                        existing.add(q)
                    elif cur_idx > insert_at:
                        question_order_ref.pop(cur_idx)
                        question_order_ref.insert(insert_at, q)
        new_qs = sorted([q for q in all_q if q not in existing], key=new_q_sort_key)
        question_order_ref.extend(new_qs)

    return list(question_order_ref), cd_followups

def simulate_step(label, answers, feature_matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order_ref):
    scores = score_all(answers, feature_matrix)
    questions, cd_followups = get_display_questions(answers, scores, feature_matrix, q_meta, q_cov, tree_nodes, question_order_ref)

    # Count unanswered
    unanswered_seen = 0
    visible = []
    for q in questions:
        if q in answers:
            visible.append(q)
        else:
            unanswered_seen += 1
            if unanswered_seen <= 15:
                visible.append(q)

    # Q88 = q_tailed_corinda_apex question text
    q88_text = "Does the forewing apex appear distinctly pointed and the termen straight (not rounded)?"

    print(f"\n=== After: {label} ===")
    print(f"  Answers: {dict(list(answers.items())[-3:])}" if answers else "  Answers: (none)")
    top3 = scores[:3]
    print(f"  Top 3: {[(s['name'], s['score'], s['max']) for s in top3]}")
    print(f"  Total questions shown (visible): {len(visible)}")
    unanswered_visible = [q for q in visible if q not in answers]
    print(f"  Unanswered visible: {len(unanswered_visible)}")

    # Q88 info
    if q88_text in questions:
        pos = questions.index(q88_text)
        in_vis = q88_text in visible
        q_num = q_numbers.get(q88_text, '?')
        print(f"  Q{q_num} (q_tailed_corinda_apex) position in question_order: {pos} (0-indexed), visible: {in_vis}")
    else:
        print(f"  Q88 (q_tailed_corinda_apex): NOT in question pool")

    if cd_followups:
        followup_nums = [f"Q{q_numbers.get(q,'?')} \"{q[:60]}...\"" for q in cd_followups]
        print(f"  CD followups: {followup_nums}")

    # Show first 15 questions with numbers
    print(f"  First 15 visible (unanswered) questions:")
    shown = 0
    for q in visible:
        if q in answers:
            q_num = q_numbers.get(q, '?')
            print(f"    [answered] Q{q_num}: {q[:80]}")
        else:
            shown += 1
            if shown <= 15:
                q_num = q_numbers.get(q, '?')
                print(f"    [{shown:2d}] Q{q_num}: {q[:80]}")

    return scores, questions, visible

def main():
    with open(TREE_PATH) as f:
        tree_data = json.load(f)
    with open(SPECIES_PATH) as f:
        species_data = json.load(f)

    print("Loading data and building feature matrix...")
    matrix, q_meta, q_cov, q_numbers, result_notes, tree_nodes = init_data(tree_data, species_data)
    print(f"  {len(matrix)} species in matrix")
    print(f"  {len(q_meta)} unique questions")

    # Verify horsfieldi feature matrix
    horsfieldi_features = matrix.get('Nacaduba horsfieldi basiviridis', {})
    q87_text = "On the forewing underside, is the basal half of space 1b completely darkened?"
    q88_text = "Does the forewing apex appear distinctly pointed and the termen straight (not rounded)?"
    q11_text = "On the hindwing underside, is the postdiscal spot in space 6 positioned roughly midway between the postdiscal spot in space 5 and the end-cell bar?"

    print(f"\n=== Horsfieldi feature matrix (key questions) ===")
    print(f"  Q{q_numbers.get(q87_text,'?')} (basal 1b): {horsfieldi_features.get(q87_text, 'NOT FOUND')}")
    print(f"  Q{q_numbers.get(q88_text,'?')} (apex): {horsfieldi_features.get(q88_text, 'NOT FOUND')}")
    print(f"  Q{q_numbers.get(q11_text,'?')} (spot6 pos): {horsfieldi_features.get(q11_text, 'NOT FOUND')}")
    print(f"  Total features: {len(horsfieldi_features)}")

    # Check q11_text exactly
    print(f"\n=== Q11 exact key check ===")
    if q11_text in horsfieldi_features:
        print(f"  Found! value = {horsfieldi_features[q11_text][:80]}")
    else:
        # Find partial match
        for q in horsfieldi_features:
            if 'postdiscal spot in space 6' in q:
                print(f"  Close match: {q[:100]}")
                print(f"  Value: {horsfieldi_features[q][:80]}")

    # Find the exact text of Q87 in tree
    q87_node = next((n for n in tree_nodes.values()
                     if n.get('type') == 'question' and 'basal half' in n.get('question','') and 'space 1b' in n.get('question','')), None)
    if q87_node:
        q87_text_exact = q87_node['question']
        cd_choice = next((c for c in q87_node.get('choices',[]) if c['label'].startswith('Cannot determine')), None)
        print(f"\n=== Q87 node ===")
        print(f"  question: {q87_text_exact}")
        print(f"  CD choice: {cd_choice}")
    else:
        q87_text_exact = q87_text
        print("\n  WARNING: Q87 node not found by search!")

    # ───── Simulation ─────────────────────────────────────────────────────────
    print("\n\n" + "="*70)
    print("FEATURE SCORING SIMULATION — Horsfieldi CD path")
    print("="*70)

    answers = {}
    question_order = []  # mutable list, updated in-place

    # Step 0: initial
    simulate_step("initial (no answers)", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Get the Q1 answer text
    q1_text = "Does the hindwing have a tail?"
    q1_answer = "Yes — hindwing is tailed"
    answers[q1_text] = q1_answer
    simulate_step(f"Q{q_numbers.get(q1_text,'?')}={q1_answer[:40]}", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Q2: cell silver?
    q2_text = "Does the hindwing cell contain a silver or metallic spot?"
    q2_answer = next((c for c in q_meta.get(q2_text,{}).get('choices',[]) if 'no' in c.lower() or 'no silver' in c.lower() or 'absent' in c.lower()), None)
    if not q2_text in q_meta:
        # Find by partial match
        q2_text = next((q for q in q_meta if 'hindwing cell' in q and 'silver' in q), None)
    if q2_text:
        choices = q_meta[q2_text]['choices']
        q2_answer = next((c for c in choices if 'no' in c.lower() and ('silver' in c.lower() or 'metallic' in c.lower())), choices[-1])
        answers[q2_text] = q2_answer
        simulate_step(f"Q{q_numbers.get(q2_text,'?')}=no-silver", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Q5 (vein 3 tail?): No
    q5_text = "Is the tail short, white-tipped, and located at vein 3?"
    if q5_text in q_meta:
        choices = q_meta[q5_text]['choices']
        q5_answer = next((c for c in choices if c.startswith('No')), choices[-1])
        answers[q5_text] = q5_answer
        simulate_step(f"Q{q_numbers.get(q5_text,'?')}=usual-vein2", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Q6 (very long tail?): No
    q6_text = "Is the tail very long and thread-like — approximately 5 mm or longer?"
    if q6_text in q_meta:
        choices = q_meta[q6_text]['choices']
        q6_answer = next((c for c in choices if c.startswith('No')), choices[-1])
        answers[q6_text] = q6_answer
        simulate_step(f"Q{q_numbers.get(q6_text,'?')}=moderate-tail", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Q8 (very dark FW upperside?): No — look for the right question
    # The horsfieldi path: after q_tailed_long_tail → q_tailed_long_dark
    q_long_dark = next((n for n in tree_nodes.values()
                        if n.get('id') == 'q_tailed_long_dark'), None)
    if q_long_dark:
        ld_text = q_long_dark['question']
        if ld_text in q_meta:
            choices = q_meta[ld_text]['choices']
            # horsfieldi is NOT the dark/camdeo group — choose "No"
            ld_answer = next((c for c in choices if c.startswith('No')), choices[-1])
            answers[ld_text] = ld_answer
            simulate_step(f"Q{q_numbers.get(ld_text,'?')}=no-dark-fw", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Now trace the tree to horsfieldi to find remaining questions
    # Let's trace the actual path from the tree to r_horsfieldi_basiviridis
    horsfieldi_paths = build_tree_paths(tree_data).get('Nacaduba horsfieldi basiviridis', [])
    rf_h = tree_data['nodes'].get('r_horsfieldi_basiviridis', {}).get('features', {})
    note_h = tree_data['nodes'].get('r_horsfieldi_basiviridis', {}).get('note', '')

    # Find canonical path
    def is_inconsistent_h(p):
        for step in p:
            if not step.get('question') or not step.get('choice'):
                continue
            if step['choice'].startswith('Cannot determine'):
                continue
            expected = rf_h.get(step['question'])
            if expected and not expected.startswith('Cannot determine') and step['choice'] != expected:
                return 1
        return 0

    scored_h = sorted(
        [(path_score(p, note_h), is_inconsistent_h(p), len(p), p) for p in horsfieldi_paths],
        key=lambda x: (x[0], x[1], x[2])
    )

    print("\n\n=== Horsfieldi paths summary ===")
    for i, (sc, inc, ln, p) in enumerate(scored_h[:3]):
        print(f"  Path {i}: score={sc}, inconsistent={inc}, len={ln}")
        for step in p:
            if step.get('question'):
                qn = q_numbers.get(step['question'], '?')
                print(f"    Q{qn}: {step['choice'][:60]}")

    canonical_h = scored_h[0][3] if scored_h else []

    print("\n\n=== Following canonical horsfieldi path through Feature Scoring ===")
    print("  (showing only steps not yet answered)")

    # Clear and restart with fresh question_order
    answers = {}
    question_order = []

    for step in canonical_h:
        q = step.get('question')
        c = step.get('choice', '')
        if not q or not c:
            continue
        qn = q_numbers.get(q, '?')
        print(f"\n  → Answering Q{qn}: \"{c[:70]}\"")
        answers[q] = c

    # Now simulate with CD on Q87
    print("\n\n=== FINAL SIMULATION: horsfieldi CD path ===")
    print("  Re-running from scratch, answering canonical path + Q87=CD\n")

    answers = {}
    question_order = []

    for step in canonical_h:
        q = step.get('question')
        c = step.get('choice', '')
        if not q:
            continue
        # Stop when we reach Q87
        if q == q87_text_exact:
            break
        answers[q] = c

    # Answer Q87 as Cannot determine
    cd_choice_text = next((c['label'] for c in q87_node.get('choices',[]) if c['label'].startswith('Cannot determine')), None) if q87_node else None
    if not cd_choice_text:
        # fallback
        cd_choice_text = "Cannot determine — FW space 1b obscured by hindwing"

    print(f"  Questions answered so far ({len(answers)}):")
    for q, c in answers.items():
        qn = q_numbers.get(q, '?')
        print(f"    Q{qn}: {c[:60]}")

    # Re-run simulation step-by-step
    answers_seq = list(answers.items())
    answers = {}
    question_order = []

    simulate_step("initial", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    for q, c in answers_seq:
        answers[q] = c
        qn = q_numbers.get(q, '?')
        simulate_step(f"Q{qn}={c[:40]}", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order)

    # Now add Q87=CD
    answers[q87_text_exact] = cd_choice_text
    qn87 = q_numbers.get(q87_text_exact, '?')
    print(f"\n  >>> KEY STEP: answering Q{qn87} as CD: {cd_choice_text}")
    scores, questions, visible = simulate_step(
        f"Q{qn87}=CD", answers, matrix, q_meta, q_cov, q_numbers, tree_nodes, question_order
    )

    # Final check
    print(f"\n\n=== FINAL CHECK ===")
    q88_in_visible = q88_text in visible
    q88_in_pool = q88_text in questions
    q88_answered = q88_text in answers
    print(f"  Q88 in question pool: {q88_in_pool}")
    print(f"  Q88 visible (within 15-cap): {q88_in_visible}")
    print(f"  Q88 answered: {q88_answered}")

    # Also check Q11 for the non-CD path
    print(f"\n=== Q11 check in horsfieldi feature matrix ===")
    if q11_text in horsfieldi_features:
        print(f"  Feature: {horsfieldi_features[q11_text][:100]}")
    else:
        print(f"  Q11 NOT in horsfieldi features!")
        print(f"  Looking for partial match 'postdiscal spot'...")
        for q in horsfieldi_features:
            if 'postdiscal' in q.lower() or 'spot 6' in q.lower() or 'space 6' in q.lower():
                print(f"    {q[:100]}: {horsfieldi_features[q][:60]}")

    # Check if Q11 is in the canonical path
    print(f"\n=== Q11 in canonical horsfieldi path ===")
    for step in canonical_h:
        q = step.get('question','')
        if 'postdiscal spot in space 6' in q or 'spot 6' in q:
            qn = q_numbers.get(q, '?')
            print(f"  Q{qn}: {step.get('choice','')[:80]}")

if __name__ == '__main__':
    main()
