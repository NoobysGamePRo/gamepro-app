"""Check test specs against the scripts they test, and draft checkpoints from a script's log lines.

usage:
  python tools/testing/spec_check.py                 check every spec in tests/specs
  python tools/testing/spec_check.py <spec.yaml>...  check some
  python tools/testing/spec_check.py --draft-checkpoints
        rewrite the checkpoints of DRAFT specs from their script's log lines
        (confirmed specs, draft: false, are never touched)

The format is described in docs/testing/spec-format.md. Exit code 1 if any spec has an error.
Test tool only: main.py doesn't import it and it isn't in the exe.
"""
import argparse
import ast
import glob
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
SPECS_DIR = os.path.join(APP, 'tests', 'specs')
SCRIPTS_DIR = os.path.join(os.path.dirname(APP), 'gamepro-scripts')
sys.path.insert(0, HERE)

from gamepro_test import DEFAULT_TARGET_LOG, parse_when  # noqa: E402  same parsing the runner uses

KINDS = {'soft-reset hunt', 'wild encounters', 'breeding / hatching', 'item / BP farm', 'online trade'}
HUNTS = {'soft-reset hunt', 'wild encounters', 'breeding / hatching'}
REQUIRED = ['script', 'name', 'game', 'console', 'draft', 'kind', 'start_state', 'cycles',
            'timeout_minutes', 'success']
KNOWN = set(REQUIRED) | {
    'cycle_log', 'target_log', 'max_static_seconds', 'static_threshold', 'camera_lost_seconds',
    'stop_grace_seconds', 'max_frames_per_checkpoint', 'settings', 'directions', 'calibration',
    'interactive', 'checkpoints', 'detection_proof', 'notes',
}
CHECKPOINT_KEYS = {'name', 'when', 'expect', 'reference_image', 'per_cycle'}
OVERVIEW_MINUTES = 10


# ── Reading a script's log lines ───────────────────────────────────────────────

def log_texts(src):
    """Every log('...') call in the script as (line, text), f-string fields as '{}'.
    Lines that build their message another way (log(msg)) are skipped."""
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and n.args:
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')
            if name != 'log':
                continue
            t = _text(n.args[0])
            if t is not None and t.strip():
                out.append((n.lineno, t.strip()))
    return sorted(out)


def _text(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _text(node.left), _text(node.right)
        return None if a is None or b is None else a + b
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):    # 'x %s' % y
        a = _text(node.left)
        return None if a is None else re.sub(r'%[-\d.]*[sdfr]', '{}', a)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'format':
        a = _text(node.func.value)
        return None if a is None else re.sub(r'\{[^{}]*\}', '{}', a)
    return None


def sample(text):
    """A log text with its '{}' fields filled in the way a real run would."""
    return text.replace('{}', '12')


def lines_matching(regex, texts):
    r = re.compile(regex)
    return [(ln, t) for ln, t in texts if r.search(sample(t))]


def regex_for(text):
    """A regex matching one log line: the literal text up to the second field, fields as \\S+."""
    parts = text.split('{}')
    keep = parts[:2] if len(parts) > 2 else parts
    rx = r'\S+'.join(re.escape(p) for p in keep).replace(r'\ ', ' ')
    return rx.strip()


SKIP = re.compile(r'(?i)shiny!|\*\*\*|DETECTED|calibrat|before shiny|so far|total|no frame|stopped|started')
BASELINE = re.compile(r'(?i)^(\S+ )?baseline|baseline —')      # the calibration line, not a check


def checked_screen_log(texts):
    """The log line written right after the script's detection check, so a frame grabbed on it shows
    the screen that was checked. Returns (regex, why) or (None, why)."""
    cands = [(ln, t) for ln, t in texts if not SKIP.search(t) and not BASELINE.search(t)]
    for ln, t in cands:                                   # per-check colour line: "SR #{}: R:{} G:{} B:{}"
        if re.search(r'R:\s*\{\}', t):
            return regex_for(t), 'logged with the colour values of each check'
    for ln, t in cands:                                   # "No shiny. Soft reset #{}...", "Horde #{}: not shiny"
        if re.search(r'(?i)no shiny|not shiny', t):
            return regex_for(t), 'the not-shiny line, logged straight after the check'
    for ln, t in cands:                                   # FRLG Script Builder: "Watching... {} px match"
        if re.search(r'(?i)px match', t):
            return regex_for(t), 'logged while the check is watching the screen'
    for ln, t in cands:                                   # timing checks: "text appeared in {} ms"
        if re.search(r'in \{\} ?ms', t):
            return regex_for(t), 'the timing check result'
    return None, 'no log line marks the check - only the overview frames'


def guess_cycle_log(texts):
    """For a spec with no cycle_log: a numbered not-shiny line, if the script has exactly one."""
    hits = [t for _, t in texts if re.search(r'(?i)no shiny|not shiny', t) and '{}' in t
            and not SKIP.search(t)]
    if len(hits) == 1:
        rx = regex_for(hits[0])
        if len(lines_matching(rx, texts)) == 1:
            return rx
    return None


FINISHED = re.compile(r'(?i)no shiny|not shiny|complete|done\b')


def one_cycle_log(texts, cycle_log):
    """If cycle_log matches several log lines (start, retry, result...), a regex for just one of them:
    the line that ends a normal cycle ('not shiny', 'complete'), else the first. None if no change."""
    target = re.compile(DEFAULT_TARGET_LOG)
    hits = [(ln, t) for ln, t in lines_matching(cycle_log, texts)
            if not target.search(sample(t)) and not re.search(r'\*\*\*|DETECTED', t)]
    if len(hits) == len(lines_matching(cycle_log, texts)) <= 1:
        return None
    pick = next(((ln, t) for ln, t in hits if FINISHED.search(t)), hits[0] if hits else None)
    if pick is None:
        return None
    rx = regex_for(pick[1])
    return rx if len(lines_matching(rx, texts)) == 1 else None


def draft_checkpoints(spec, src):
    """Checkpoints for a draft spec: the checked screen (one per cycle) plus an overview."""
    texts = log_texts(src)
    kind = spec.get('kind')
    rx, why = checked_screen_log(texts) if kind in HUNTS else (None, '')
    expect = {
        'soft-reset hunt': 'the target Pokemon on screen, where the script checks its colour',
        'wild encounters': 'a wild Pokemon on the battle screen, where the script checks its colour',
        'breeding / hatching': 'the hatchling (or egg) the script is checking',
    }.get(kind, 'the screen the script is checking')
    cps = []
    if rx:
        cps.append({'name': 'checked screen', 'when': {'log': rx}, 'per_cycle': 1, 'expect': expect,
                    '_why': why})
    elif kind not in HUNTS and spec.get('cycle_log'):
        rx, why = spec['cycle_log'], 'the end of each cycle'
        cps.append({'name': 'cycle end', 'when': {'log': rx}, 'per_cycle': 1, '_why': why,
                    'expect': {'item / BP farm': 'back at the start position, the reward counter higher',
                               'online trade': 'the trade finished and the game back at the trade menu'}
                    .get(kind, 'the game back where the cycle started')})
    cps.append({'name': 'overview', 'when': {'every_minutes': OVERVIEW_MINUTES},
                'expect': 'the game running the script\'s loop, not stuck on a menu, error or HOME screen',
                '_why': '' if rx else why})
    return cps


def checkpoints_yaml(cps):
    q = lambda s: yaml.safe_dump(s, allow_unicode=True, default_style='"', width=1000).strip()
    out = ['checkpoints:']
    for c in cps:
        out.append(f'  - name: {q(c["name"])}')
        w = c['when']
        if 'log' in w:
            out.append(f'    when: {{log: {q(w["log"])}}}' + (f'   # {c["_why"]}' if c.get('_why') else ''))
        else:
            out.append(f'    when: {{every_minutes: {w["every_minutes"]}}}' + (f'   # {c["_why"]}' if c.get('_why') else ''))
        if c.get('per_cycle'):
            out.append(f'    per_cycle: {c["per_cycle"]}')
        out.append(f'    expect: {q(c["expect"])}')
    return '\n'.join(out) + '\n'


def replace_block(text, key, new_block):
    """Replace a top-level 'key:' block (the key line plus its indented lines) in a YAML text."""
    lines = text.splitlines(keepends=True)
    out, i, done = [], 0, False
    while i < len(lines):
        if not done and re.match(rf'{key}\s*:', lines[i]):
            i += 1
            while i < len(lines) and (lines[i].startswith((' ', '\t')) or not lines[i].strip()):
                i += 1
            out.append(new_block)
            done = True
            continue
        out.append(lines[i])
        i += 1
    return ''.join(out) if done else None


# ── Checking a spec ────────────────────────────────────────────────────────────

def read_text(path):
    with open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def load_spec(path):
    with open(path, encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def check(spec, scripts_dir=SCRIPTS_DIR, spec_path=''):
    """Problems with one spec as (level, text); level is 'error' or 'warn'."""
    out = []
    err = lambda t: out.append(('error', t))
    warn = lambda t: out.append(('warn', t))
    draft = spec.get('draft', True) is not False

    for k in REQUIRED:
        if spec.get(k) in (None, ''):
            err(f'missing `{k}`')
    for k in spec:
        if k not in KNOWN and not k.startswith('_'):
            warn(f'unknown field `{k}` (typo?)')
    if spec.get('kind') and spec['kind'] not in KINDS:
        warn(f'kind `{spec["kind"]}` is not one of: {", ".join(sorted(KINDS))}')
    for k in ('cycles', 'timeout_minutes'):
        v = spec.get(k)
        if v is not None and (not isinstance(v, (int, float)) or v <= 0):
            err(f'`{k}` must be a positive number')
    if isinstance(spec.get('cycles'), int) and spec['cycles'] > 1000:
        err('`cycles` is over the runner\'s ceiling of 1000')

    src, texts = None, []
    script = spec.get('script')
    if script:
        p = os.path.join(scripts_dir, script)
        if not os.path.exists(p):
            err(f'script not found: {script}')
        else:
            src = read_text(p)
            try:
                texts = log_texts(src)
            except SyntaxError as e:
                err(f'script has a syntax error (line {e.lineno})')

    regexes = {}
    for k in ('cycle_log', 'target_log'):
        if spec.get(k):
            try:
                regexes[k] = re.compile(spec[k])
            except re.error as e:
                err(f'`{k}` is not a valid regex: {e}')

    if texts:
        if 'cycle_log' in regexes:
            hits = lines_matching(spec['cycle_log'], texts)
            if not hits:
                err('`cycle_log` matches none of the script\'s log lines - cycles would never be counted')
            elif len(hits) > 1:
                err('`cycle_log` matches more than one log line, so cycles may be counted twice: '
                    + ' / '.join(f'line {ln} "{t[:50]}"' for ln, t in hits))
        elif spec.get('kind') != 'breeding / hatching' or not draft:
            warn('no `cycle_log`: the run can only end on the timeout')
        target = regexes.get('target_log') or re.compile(DEFAULT_TARGET_LOG)
        finds = [(ln, t) for ln, t in texts
                 if re.search(r'\*\*\*|DETECTED|(?i:shiny).*!|Press .*Stop when done', t)
                 and not re.search(r'(?i)no shiny|not shiny|before shiny', t)]
        if finds and not any(target.search(sample(t)) for _, t in finds):
            err('the target pattern matches none of the lines that announce a find, e.g. '
                f'line {finds[0][0]} "{finds[0][1][:50]}" - a find would not stop the run')
        if 'cycle_log' in regexes:
            both = [t for _, t in lines_matching(spec['cycle_log'], texts) if target.search(sample(t))]
            if both:
                err(f'a cycle line also matches the target pattern: "{both[0][:50]}"')

    cps = spec.get('checkpoints') or []
    if not cps:
        (warn if draft else err)('no `checkpoints`: the run saves only start/final frames')
    names = set()
    for i, cp in enumerate(cps):
        label = f'checkpoint {cp.get("name") or i + 1}'
        if cp.get('name') in names:
            err(f'{label}: duplicate name')
        names.add(cp.get('name'))
        for k in cp:
            if k not in CHECKPOINT_KEYS:
                warn(f'{label}: unknown field `{k}`')
        if not cp.get('expect') or 'TODO' in str(cp.get('expect')):
            (warn if draft else err)(f'{label}: no `expect` text for the judge')
        try:
            w = parse_when(cp.get('when'))
        except re.error as e:
            err(f'{label}: `when` regex is invalid: {e}')
            continue
        if w is None:
            (warn if draft else err)(f'{label}: `when` must be {{log: <regex>}} or {{every_minutes: N}}')
        elif 'log' in w and texts and not lines_matching(w['log'].pattern, texts):
            err(f'{label}: `when` log regex matches none of the script\'s log lines')
        ref = cp.get('reference_image')
        if ref and not _exists_ref(ref, scripts_dir, spec_path):
            err(f'{label}: reference_image not found: {ref}')

    proof = spec.get('detection_proof') or {}
    if proof.get('reference_image') and not _exists_ref(proof['reference_image'], scripts_dir, spec_path):
        err(f'detection_proof reference_image not found: {proof["reference_image"]}')
    if not draft:
        if spec.get('kind') in HUNTS and not proof:
            err('confirmed hunt with no `detection_proof` (how we know a shiny would be caught)')
        todo = [k for k, v in spec.items() if 'TODO' in str(v)]
        if todo:
            err('confirmed spec still has TODO in: ' + ', '.join(todo))
    return out


def _exists_ref(ref, scripts_dir, spec_path):
    for base in (scripts_dir, APP, os.path.dirname(spec_path or SPECS_DIR)):
        if os.path.exists(os.path.join(base, ref)):
            return True
    return False


def all_specs():
    return sorted(glob.glob(os.path.join(SPECS_DIR, '**', '*.yaml'), recursive=True))


def main():
    ap = argparse.ArgumentParser(description='Check GamePRo test specs.')
    ap.add_argument('specs', nargs='*', help='spec files (default: all in tests/specs)')
    ap.add_argument('--scripts', default=SCRIPTS_DIR, help='gamepro-scripts checkout')
    ap.add_argument('--draft-checkpoints', action='store_true',
                    help='rewrite checkpoints of draft specs from their script\'s log lines')
    ap.add_argument('--quiet', action='store_true', help='only list specs with errors')
    a = ap.parse_args()
    paths = a.specs or all_specs()

    if a.draft_checkpoints:
        n = 0
        for p in paths:
            spec = load_spec(p)
            if spec.get('draft') is False:
                continue
            src_path = os.path.join(a.scripts, spec.get('script', ''))
            if not os.path.exists(src_path):
                print('skip (script not found):', p)
                continue
            src = read_text(src_path)
            with open(p, encoding='utf-8', newline='') as f:
                raw = f.read()
            eol = '\r\n' if '\r\n' in raw else '\n'
            text = raw.replace('\r\n', '\n')
            # rig_key TODO = the runner's default key (corners: auto/default, rects: <script>.<n>)
            new = text.replace(', rig_key: "TODO"}', '}').replace(
                '# answered from the rig profile in a headless run',
                '# corners: detected automatically; rects: answered from the rig profile')
            rx = None if spec.get('cycle_log') else guess_cycle_log(log_texts(src))
            if rx:
                spec['cycle_log'] = rx
                q = yaml.safe_dump(rx, default_style='"', width=1000).strip()
                new = re.sub(r'(?m)^cycle_log:.*$', lambda m: f'cycle_log: {q}', new, count=1)
            if spec.get('cycle_log'):
                rx = one_cycle_log(log_texts(src), spec['cycle_log'])
                if rx:
                    spec['cycle_log'] = rx
                    q = yaml.safe_dump(rx, default_style='"', width=1000).strip()
                    new = re.sub(r'(?m)^cycle_log:.*$', lambda m: f'cycle_log: {q}', new, count=1)
            new = replace_block(new, 'checkpoints', checkpoints_yaml(draft_checkpoints(spec, src)))
            if new and new != text:
                with open(p, 'w', encoding='utf-8', newline='') as f:     # keep the file's line endings
                    f.write(new.replace('\n', eol))
                n += 1
        print(f'checkpoints (and any double-counting cycle_log) redrafted in {n} spec(s)')

    errors = 0
    for p in paths:
        rel = os.path.relpath(p, APP).replace('\\', '/')
        try:
            spec = load_spec(p)
        except yaml.YAMLError as e:
            print(f'ERROR {rel}: not valid YAML: {e}')
            errors += 1
            continue
        probs = check(spec, a.scripts, p)
        bad = [t for lv, t in probs if lv == 'error']
        errors += bool(bad)
        state = 'draft' if spec.get('draft', True) is not False else 'confirmed'
        if bad or not a.quiet:
            print(f'{"FAIL" if bad else "ok  "} {rel} ({state})')
            for lv, t in probs:
                if lv == 'error' or not a.quiet:
                    print(f'       {lv}: {t}')
    print(f'\n{len(paths)} spec(s), {errors} with errors')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
