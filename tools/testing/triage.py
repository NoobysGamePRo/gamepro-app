"""Phase 1 static triage of gamepro-scripts/Beta: reads every script, never runs one.

usage: python tools/testing/triage.py [--scripts PATH] [--build-dir PATH] [--specs]

Writes:
  docs/testing/triage-report.csv   one row per Beta script: status, reasons, what a headless run needs
  tests/specs/<folder>/<script>.yaml   draft spec for each script that doesn't have one yet (--specs).
                                       Existing specs are never overwritten.

Status:
  needs-fix  a check that means the script will crash or misbehave (unknown controller call, import
             not in the exe, undefined name, ...)
  unclear    nothing broken found, but something needs a person to decide (no stop check in a loop,
             blind timing, ...)
  likely-ok  no findings above "info"

Judgement that a tool can't make (start state, what the screen should show, manual overrides of the
status) lives in docs/testing/triage-notes.csv and the spec files, and is merged into the report.

Needs pyflakes (dev only): pip install pyflakes
"""
import argparse
import ast
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

FOLDER_GAME = {
    'gen_2_vc': ('Gold / Silver / Crystal (Virtual Console)', '3DS'),
    'gen_3_frlg': ('FireRed / LeafGreen (NSO GBA)', 'Switch'),
    'gen_4_hgss_dpp': ('HGSS / DPPt (DS)', '3DS (DS mode)'),
    'gen_5_bw_b2w2': ('Black / White / B2W2 (DS)', '3DS (DS mode)'),
    'gen_6_oras_xy': ('X / Y / ORAS', '3DS'),
    'gen_7_sun_moon': ('Sun / Moon / USUM', '3DS'),
    'gen_8_bdsp': ('Brilliant Diamond / Shining Pearl', 'Switch'),
    'gen_8_sword_shield': ('Sword / Shield', 'Switch'),
    'gen_9_scarlet_violet': ('Scarlet / Violet', 'Switch'),
    'legends_za': ('Legends: Z-A', 'Switch / Switch 2'),
    'lets_go': ("Let's Go Pikachu / Eevee", 'Switch'),
}

# Names a script uses for the controller object
CTRL_NAMES = {'controller', 'ctrl', 'c', 'gp', 'pad'}
CTRL_ATTRS = {'c', 'ctrl', 'controller', '_c', '_ctrl', 'gp', 'pad'}
SEVERITY = {'error': 3, 'warn': 2, 'info': 1}


# ── What the app provides ──────────────────────────────────────────────────────

def controller_api():
    """Public methods of GameProController, read from core/controller.py."""
    tree = ast.parse(open(os.path.join(APP, 'core', 'controller.py'), encoding='utf-8').read())
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and n.name == 'GameProController':
            return {f.name for f in n.body if isinstance(f, ast.FunctionDef) and not f.name.startswith('_')}
    raise SystemExit('GameProController not found')


def exe_modules(build_dir):
    """Top-level modules frozen into GamePRo.exe, from a PyInstaller work dir. None if not built."""
    if not build_dir or not os.path.isdir(build_dir):
        return None
    mods = set(sys.builtin_module_names)      # sys, time, ... are compiled into python3xx.dll
    for toc in ('PYZ-00.toc', 'Analysis-00.toc'):
        p = os.path.join(build_dir, toc)
        if os.path.exists(p):
            for m in re.finditer(r"\(\s*'([A-Za-z_][\w.]*)'", open(p, encoding='utf-8').read()):
                mods.add(m.group(1).split('.')[0])
    bl = os.path.join(build_dir, 'base_library.zip')
    if os.path.exists(bl):
        import zipfile
        for n in zipfile.ZipFile(bl).namelist():
            mods.add(n.split('/')[0].split('.')[0])
    return mods or None


def load_like_app(path):
    """Import the script as gui/app.py does. Returns the BaseScript subclass, None if there is none,
    or the error text."""
    import importlib.util
    if APP not in sys.path:
        sys.path.insert(0, APP)
    from scripts.base_script import BaseScript
    try:
        spec = importlib.util.spec_from_file_location('_gp_script', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except BaseException as e:            # SystemExit too: nothing may stop the triage run
        return f'{type(e).__name__}: {e}'
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if isinstance(obj, type) and issubclass(obj, BaseScript) and obj is not BaseScript:
            return obj
    return None


# ── Small AST helpers ──────────────────────────────────────────────────────────

def is_ctrl(node):
    if isinstance(node, ast.Name):
        return node.id in CTRL_NAMES
    return (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
            and node.value.id == 'self' and node.attr in CTRL_ATTRS)


def call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        base = f.value
        b = base.id if isinstance(base, ast.Name) else (base.attr if isinstance(base, ast.Attribute) else '?')
        return f'{b}.{f.attr}'
    if isinstance(f, ast.Name):
        return f.id
    return '?'


def const_value(node, consts):
    """Number for a literal, a class constant (self.X / Cls.X / X), or a simple expression of those."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.Attribute) and node.attr in consts:
        return consts[node.attr]
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.BinOp):
        a, b = const_value(node.left, consts), const_value(node.right, consts)
        if a is not None and b is not None:
            try:
                return {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b, ast.Div: a / b if b else None}.get(type(node.op))
            except Exception:
                return None
    return None


def names_in(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
    return out


def loop_exits(loop):
    """True if a while-loop can end: a condition that isn't constant True, or break/return/raise inside
    (not counting those in nested functions), or a stop check."""
    if not (isinstance(loop.test, ast.Constant) and loop.test.value is True):
        return True
    for n in ast.walk(loop):
        if isinstance(n, (ast.Break, ast.Return, ast.Raise)):
            return True
    return False


def has_stop_check(node):
    src_names = names_in(node)
    if src_names & {'stop_event', 'stop', '_stop', 'is_set'}:
        return True
    for n in ast.walk(node):            # self.wait(...) returns False on Stop
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('wait', '_wait', 'sleep_s', '_sleep'):
            return True
    return False


# ── One script ─────────────────────────────────────────────────────────────────

def triage(path, rel, api, exe_mods, repo):
    src = open(path, encoding='utf-8', errors='replace').read()
    findings = []                         # (severity, code, text)

    def add(sev, code, text):
        findings.append((sev, code, text))

    info = {'calibration': [], 'interactive': [], 'settings': [], 'cycle_logs': [], 'stop_logs': [],
            'saved_calibration': '', 'name': '', 'doc': '', 'class': ''}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        add('error', 'syntax', f'syntax error line {e.lineno}: {e.msg}')
        return findings, info
    info['doc'] = (ast.get_docstring(tree) or '').strip()

    # Script class
    cls = None
    for n in tree.body:
        if isinstance(n, ast.ClassDef) and any(isinstance(b, ast.Name) and b.id == 'BaseScript' for b in n.bases):
            cls = n
            break
    consts = {}
    if cls is None:
        add('error', 'no-class', 'no BaseScript subclass: the app will not list it')
    else:
        info['class'] = cls.name
        for b in cls.body:
            if isinstance(b, ast.Assign) and len(b.targets) == 1 and isinstance(b.targets[0], ast.Name):
                k = b.targets[0].id
                try:
                    v = ast.literal_eval(b.value)
                except Exception:
                    v = const_value(b.value, consts)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    consts[k] = float(v)
                if k == 'NAME':
                    info['name'] = v if isinstance(v, str) else ''
                if k == 'SETTINGS':
                    info['settings'] = v if isinstance(v, list) else None      # None: read from the loaded class
                    if not isinstance(v, list):
                        add('warn', 'settings', 'SETTINGS could not be read (not a plain list)')
                    else:
                        for s in v:
                            if not isinstance(s, dict) or s.get('type') not in ('section', 'choice', 'bool', 'int', 'multi', 'text'):
                                add('error', 'settings', f'SETTINGS entry the dialog does not understand: {s!r:.60}')
                            elif s['type'] != 'section' and 'key' not in s:
                                add('error', 'settings', f'SETTINGS entry without a key: {s.get("label")!r}')
        run = [f for f in cls.body if isinstance(f, ast.FunctionDef) and f.name == 'run']
        if not run:
            add('error', 'no-run', 'class has no run() method')
        else:
            args = [a.arg for a in run[0].args.args]
            need = ['self', 'controller', 'frame_grabber', 'stop_event', 'log', 'request_calibration']
            if args[:6] != need and not run[0].args.kwarg:
                add('error', 'run-signature', f'run() arguments {args} - the app passes them by keyword: {need[1:]}')
        if not info['name']:
            add('warn', 'no-name', 'no NAME: listed as "Unnamed Script"')

    # Load it the way the app does (gui/app.py _load_script): runs module-level code only
    loaded = load_like_app(path)
    if isinstance(loaded, str):
        add('error', 'load', f'the app cannot load it: {loaded}')
    elif loaded is not None and info['settings'] is None:
        info['settings'] = list(getattr(loaded, 'SETTINGS', None) or [])
        findings[:] = [f for f in findings if f[1] != 'settings']
    if info['settings'] is None:
        info['settings'] = []

    # pyflakes: undefined names, unused imports, redefinitions
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
        out, err = io.StringIO(), io.StringIO()
        check(src, rel, Reporter(out, err))
        for line in out.getvalue().splitlines():
            msg = line.split(':', 3)[-1].strip()
            ln = line.split(':')[1] if ':' in line else '?'
            if 'undefined name' in msg:
                add('error', 'undefined-name', f'line {ln}: {msg}')
            elif 'unable to detect undefined names' in msg:
                pass
            elif 'imported but unused' in msg or 'is assigned to but never used' in msg or 'redefinition of unused' in msg:
                pass                     # harmless
            elif 'f-string is missing placeholders' in msg:
                pass
            else:
                add('info', 'pyflakes', f'line {ln}: {msg}')
    except ImportError:
        add('info', 'pyflakes', 'pyflakes not installed: undefined names not checked')

    # Imports available in the exe
    for n in ast.walk(tree):
        mods = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            mods = [n.module]
        for m in mods:
            top = m.split('.')[0]
            if top == 'scripts':
                parts = m.split('.')[1:]
                cand = os.path.join(repo, *parts)
                if parts and not (os.path.exists(cand + '.py') or os.path.isdir(cand)):
                    add('error', 'import', f'line {n.lineno}: imports {m}, which is not in gamepro-scripts')
            elif os.path.exists(os.path.join(os.path.dirname(path), top)) or os.path.exists(os.path.join(os.path.dirname(path), top + '.py')):
                if not tracked_ok(repo, os.path.join(os.path.dirname(path), top)):
                    add('error', 'import', f'line {n.lineno}: imports {m}, a local folder that is not in git (customers will not have it)')
            elif exe_mods is not None and top not in exe_mods:
                add('error', 'import', f'line {n.lineno}: imports {m}, which is not frozen into GamePRo.exe (works from source only)')
            elif exe_mods is None and top not in sys.stdlib_module_names and top not in ('cv2', 'numpy', 'PIL', 'serial'):
                add('warn', 'import', f'line {n.lineno}: imports {m} (third-party; check it is in the exe)')

    # Controller calls
    used = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and is_ctrl(n.func.value):
            meth = n.func.attr
            used.add(meth)
            if meth.startswith('_'):
                add('warn', 'private-call', f'line {n.lineno}: calls controller.{meth}() (private; bypasses the API)')
            elif meth not in api:
                add('error', 'unknown-call', f'line {n.lineno}: controller.{meth}() does not exist (crashes here)')
    if any(m.startswith('hold_') for m in used) and 'release_all' not in used:
        add('warn', 'hold-no-release', 'holds a direction but never calls release_all()')
    if 'wait_for_direction' in used:
        info['interactive'].append('waits for a D-pad press in Manual Controls (wait_for_direction)')

    # Calibration
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and call_name(n).endswith('request_calibration'):
            prompt = n.args[0].value if n.args and isinstance(n.args[0], ast.Constant) else '<expr>'
            mode = 'rect'
            for a in n.args[1:2]:
                if isinstance(a, ast.Constant):
                    mode = a.value
            for k in n.keywords:
                if k.arg == 'mode' and isinstance(k.value, ast.Constant):
                    mode = k.value.value
            info['calibration'].append({'mode': mode, 'prompt': prompt, 'line': n.lineno})
    m = re.search(r"""['"]([\w ./-]*calibration[\w ./-]*)['"]""", src)
    if '_cal_path' in src or re.search(r'calibration.{0,40}\.json', src):
        info['saved_calibration'] = 'yes'
        if 'Beta' in src and re.search(r"['\"]Beta['\"]", src):
            add('warn', 'cal-path-beta', 'calibration path hard-codes "Beta": moving to Release loses the saved calibration')

    # Loops, sleeps, frames
    funcs = [f for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for f in funcs:
        frame_vars = set()
        for n in ast.walk(f):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and call_name(n.value).endswith('get_latest_frame'):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        frame_vars.add(t.id)
        if frame_vars:
            checked = False
            for n in ast.walk(f):
                if isinstance(n, ast.Compare) and any(isinstance(o, (ast.Is, ast.IsNot)) for o in n.ops):
                    if names_in(n) & frame_vars:
                        checked = True
                if isinstance(n, (ast.If, ast.While, ast.IfExp)) and isinstance(n.test, (ast.Name, ast.UnaryOp)):
                    if names_in(n.test) & frame_vars:
                        checked = True
                if isinstance(n, ast.BoolOp) and names_in(n) & frame_vars:
                    checked = True
            if not checked:
                add('warn', 'frame-none', f'{f.name}(): uses get_latest_frame() without checking for None (crashes if the camera drops a frame)')
        for n in ast.walk(f):
            if isinstance(n, ast.While):
                if not loop_exits(n) and not has_stop_check(n):
                    add('error', 'endless-loop', f'line {n.lineno} in {f.name}(): while True with no break/return and no stop check (Stop can never end it)')
                elif not has_stop_check(n) and isinstance(n.test, ast.Constant):
                    add('warn', 'loop-no-stop', f'line {n.lineno} in {f.name}(): while True without a stop check (Stop waits until the loop ends by itself)')
            if isinstance(n, ast.Call) and call_name(n) in ('time.sleep', 'sleep'):
                v = const_value(n.args[0], consts) if n.args else None
                if v is not None and v >= 30:
                    add('warn', 'sleep-units', f'line {n.lineno}: time.sleep({v:g}) - milliseconds by mistake?')
                elif v is not None and v >= 2:
                    add('warn', 'long-sleep', f'line {n.lineno}: time.sleep({v:g}) ignores Stop for {v:g}s (use self.wait)')
                arg = n.args[0] if n.args else None
                if arg is not None and re.search(r'_MS$|_MILLIS', ast.unparse(arg)) and '/' not in ast.unparse(arg):
                    add('error', 'sleep-units', f'line {n.lineno}: time.sleep({ast.unparse(arg)}) - a millisecond constant used as seconds')
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'wait' and n.args:
                a = n.args[0]
                v = const_value(a, consts)
                if v is not None and v >= 300 and not re.search(r'TIMEOUT|MAX|LIMIT', ast.unparse(a)):
                    add('warn', 'wait-units', f'line {n.lineno}: wait({ast.unparse(a)} = {v:g}s) - milliseconds by mistake?')
                if re.search(r'_MS\b', ast.unparse(a)) and '/' not in ast.unparse(a):
                    add('error', 'wait-units', f'line {n.lineno}: wait({ast.unparse(a)}) - a millisecond constant used as seconds')
            # stop_event.wait() with no timeout = park until the user presses Stop
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'wait'
                    and not n.args and not n.keywords and names_in(n.func.value) & {'stop_event', 'stop'}):
                info['interactive'].append(f'line {n.lineno}: parks until Stop is pressed (stop_event.wait())')

    # Old-system leftovers
    if re.search(r'\bSleep\(|\bdelay\(|digitalWrite|Serial\.(write|print)', src):
        add('error', 'old-syntax', 'C++/Arduino calls left in the code')
    if re.search(r'["\']COM\d+["\']', src):
        add('warn', 'com-port', 'hard-coded COM port')
    if re.search(r'cv2\.(imshow|waitKey|namedWindow)|\binput\(', src):
        add('error', 'blocking-ui', 'cv2.imshow / input() - blocks or crashes in the windowed exe')
    for m in re.finditer(r'#\s*(TODO|FIXME|XXX)\b(.*)', src):
        add('info', 'todo', f'{m.group(1)}{m.group(2)[:70]}')

    # Detection method
    uses_cam = bool(re.search(r'get_latest_frame|request_calibration\(', src))
    uses_ldr = 'read_light_value' in src
    info['detection'] = ('camera' if uses_cam else '') + ('+light' if uses_ldr else '') or 'blind timing'
    if not uses_cam and not uses_ldr:
        add('warn', 'blind', 'no camera or light sensor: it cannot notice if the game drifts out of step')
    if re.search(r'[Pp]ress Stop (now|within)|window\)', src):
        info['interactive'].append('asks the user to press Stop within a time window (baseline check)')

    # Log lines: guess the cycle counter and the success/stop lines
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'log' and n.args:
            a = n.args[0]
            if isinstance(a, ast.JoinedStr):
                text = ''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in a.values)
            elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                text = a.value
            else:
                continue
            if re.search(r'shiny|SHINY|found|Found|perfect|caught|hatched', text) and re.search(r'!|\*\*\*|STOP|stop', text):
                info['stop_logs'].append(text.strip()[:90])
            elif re.search(r'(\w[\w ]{0,25}?)\s*#?\{\}', text):
                info['cycle_logs'].append(text.strip()[:90])
    return findings, info


CYCLE_WORDS = r'(?i)reset|encounter|attempt|cycle|chain|egg|trade|battle|\brun\b|loop|hatch|catch|spin|round|trip'


def cycle_regex(lines):
    """Turn the most likely per-cycle log line ('No shiny. Soft reset #{}...') into a regex."""
    for t in lines:
        for m in re.finditer(r'([A-Za-z][A-Za-z ]{0,25}?)\s*(#?)\{\}', t):
            if re.search(CYCLE_WORDS, m.group(1)) and not re.search(r'(?i)before|total|baseline', t):
                return re.escape(m.group(1).strip()) + r'\s*' + re.escape(m.group(2)) + r'(\d+)'
    return ''


# ── Specs ──────────────────────────────────────────────────────────────────────

def q(s):
    return json.dumps(s, ensure_ascii=False)       # a JSON string is a valid YAML scalar


# What a pass means, by kind of script (docs/testing/triage-notes.csv 'kind').  Shiny hunts can't wait
# for a shiny, so they pass on clean cycles plus proof that the detection works (DECISIONS.md).
KINDS = {
    'sr': ('soft-reset hunt', 5,
           'Every cycle resets, reaches the encounter/gift and logs "not shiny" with the screen showing the '
           'expected Pokemon. Plus one proof that detection works: a shiny reference frame, or the check '
           'value moving clearly when the target is not on screen.',
           [('encounter', 'the target Pokemon on screen at the moment the shiny check runs'),
            ('after reset', 'the game back at the title / save point, ready for the next cycle')]),
    'wild': ('wild encounters', 10,
             'Every encounter is detected, judged not shiny and fled, then walking resumes in the same area. '
             'Different species must NOT be reported as shiny. Plus one proof that detection works.',
             [('encounter', 'a wild Pokemon on the battle screen when the check runs'),
              ('after flee', 'back in the overworld at the same spot')]),
    'egg': ('breeding / hatching', 3,
            'Each cycle collects or hatches the expected eggs and checks every hatchling; the party/boxes end '
            'in the state the script expects.',
            [('hatch', 'the hatch animation or the hatchling summary screen'),
             ('end', 'party / box as the script expects')]),
    'farm': ('item / BP farm', 5,
             'Each loop completes and the counter (money, balls, BP) changes as expected; the game ends where it '
             'started.',
             [('loop end', 'back at the start position with the expected reward')]),
    'trade': ('online trade', 3,
              'Each trade completes and the next one starts from the same screen.',
              [('trade done', 'trade complete screen, then back at the trade menu')]),
}


def draft_spec(row, info, note):
    cal = info['calibration']
    kind = KINDS.get(note.get('kind', ''), ('?', 3, 'TODO: what a passing run means for this script',
                                             [('end', 'TODO: what the screen shows when the run stops')]))
    lines = [
        f'script: {q(row["path"])}',
        f'name: {q(row["name"])}',
        f'game: {q(row["game"])}',
        f'console: {q(row["console"])}',
        'draft: true',
        f'kind: {q(kind[0])}',
        f'start_state: {q(note.get("start_state") or "TODO: where the game must be before Run, and how to get there")}',
        f'cycles: {kind[1]}',
        'timeout_minutes: 30',
        f'cycle_log: {q(cycle_regex(info["cycle_logs"]))}',
        'max_static_seconds: 120',
    ]
    if info['settings']:
        lines.append('settings:')
        for s in info['settings']:
            if isinstance(s, dict) and s.get('key'):
                lines.append(f'  {s["key"]}: {json.dumps(s.get("default"))}')
    if cal:
        lines.append('calibration:            # answered from the rig profile in a headless run')
        for c in cal:
            lines.append(f'  - {{mode: {q(c["mode"])}, prompt: {q(c["prompt"])}, rig_key: "TODO"}}')
    if info['interactive']:
        lines.append('interactive:            # a headless run must answer these')
        for t in dict.fromkeys(info['interactive']):
            lines.append(f'  - {q(t)}')
    lines.append('checkpoints:')
    for name, expect in kind[3]:
        lines += [f'  - name: {q(name)}', '    when: "TODO: log line or minutes"', f'    expect: {q(expect)}']
    lines += [
        f'success: {q(kind[2])}',
        f'notes: {q(note.get("notes", ""))}',
    ]
    if info['stop_logs']:
        lines.append('# log lines that mean it found its target:')
        for t in info['stop_logs'][:4]:
            lines.append(f'#   {t}')
    return '\n'.join(lines) + '\n'


# ── Main ───────────────────────────────────────────────────────────────────────

def git_date(repo, rel):
    r = subprocess.run(['git', '-C', repo, 'log', '-1', '--format=%cs', '--', rel], capture_output=True, text=True)
    return r.stdout.strip() or 'untracked'


def tracked_ok(repo, path):
    rel = os.path.relpath(path, repo).replace(os.sep, '/')
    r = subprocess.run(['git', '-C', repo, 'ls-files', '--', rel], capture_output=True, text=True)
    return bool(r.stdout.strip())


def tracked(repo):
    r = subprocess.run(['git', '-C', repo, 'ls-files'], capture_output=True, text=True)
    return set(r.stdout.split('\n'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scripts', default=os.path.join(os.path.dirname(APP), 'gamepro-scripts'))
    ap.add_argument('--build-dir', default=os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Temp', 'gamepro_work', 'gamepro'),
                    help='PyInstaller work dir, to check imports against what the exe really contains')
    ap.add_argument('--out', default=os.path.join(APP, 'docs', 'testing', 'triage-report.csv'))
    ap.add_argument('--notes', default=os.path.join(APP, 'docs', 'testing', 'triage-notes.csv'))
    ap.add_argument('--specs', action='store_true', help='write draft specs for scripts that have none')
    a = ap.parse_args()

    api = controller_api()
    exe_mods = exe_modules(a.build_dir)
    if exe_mods is None:
        print('note: no PyInstaller build found - imports checked against stdlib only')
    in_git = tracked(a.scripts)
    notes, ports = {}, {}
    if os.path.exists(a.notes):
        with open(a.notes, encoding='utf-8') as f:
            notes = {r['script']: r for r in csv.DictReader(f)}
    pc = os.path.join(os.path.dirname(a.out), 'port-compare.csv')       # from port_compare.py
    if os.path.exists(pc):
        with open(pc, encoding='utf-8') as f:
            ports = {r['script']: r for r in csv.DictReader(f)}

    rows = []
    for root, dirs, files in os.walk(os.path.join(a.scripts, 'Beta')):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not d.startswith(('_', '.')))
        for fn in sorted(files):
            if not fn.endswith('.py') or fn == '__init__.py':
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, a.scripts).replace(os.sep, '/')
            if rel not in in_git:
                continue                  # local-only file, never published
            folder = rel.split('/')[1]
            game, console = FOLDER_GAME.get(folder, ('?', '?'))
            findings, info = triage(p, rel, api, exe_mods, a.scripts)
            mg = re.search(r'^Game:\s*(?:Pokemon\s+|Pokémon\s+)?(.+)$', info['doc'], re.M)
            if mg and len(mg.group(1)) < 70:
                game = mg.group(1).strip()
            worst = max((SEVERITY[s] for s, _, _ in findings), default=0)
            status = 'needs-fix' if worst == 3 else 'unclear' if worst == 2 else 'likely-ok'
            reasons = [f'[{s}] {t}' for s, _, t in sorted(findings, key=lambda x: -SEVERITY[x[0]]) if s != 'info']
            note = notes.get(fn[:-3], {})
            spec_rel = f'tests/specs/{folder}/{fn[:-3]}.yaml'
            port = ports.get(fn[:-3], {})
            row = {
                'script': fn[:-3], 'path': rel, 'name': info['name'], 'game': game, 'console': console,
                'status': note.get('status') or status, 'kind': note.get('kind', ''),
                'review_notes': note.get('notes', ''),
                'auto_status': status, 'reasons': ' | '.join(dict.fromkeys(reasons)),
                'original': port.get('original', ''), 'port_similarity': port.get('similarity', ''),
                'detection_original': port.get('cpp_detection', ''),
                'detection': info.get('detection', ''),
                'calibration': '; '.join(f'{c["mode"]}: {c["prompt"]}' for c in info['calibration']),
                'saves_calibration': info['saved_calibration'],
                'interactive': ' | '.join(dict.fromkeys(info['interactive'])),
                'cycle_log': cycle_regex(info['cycle_logs']),
                'start_state': note.get('start_state', ''),
                'spec': spec_rel, 'last_modified': git_date(a.scripts, rel),
            }
            rows.append(row)
            if a.specs:
                sp = os.path.join(APP, *spec_rel.split('/'))
                if not os.path.exists(sp):
                    os.makedirs(os.path.dirname(sp), exist_ok=True)
                    with open(sp, 'w', encoding='utf-8', newline='\n') as f:
                        f.write(draft_spec(row, info, note))

    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter
    print(len(rows), 'Beta scripts ->', a.out)
    print('status:', dict(Counter(r['status'] for r in rows)))
    print('auto  :', dict(Counter(r['auto_status'] for r in rows)))


if __name__ == '__main__':
    main()
