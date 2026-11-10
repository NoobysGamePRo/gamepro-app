"""Read-only inventory of gamepro-scripts/Beta -> docs/testing/inventory.csv (Phase 0).

usage: python tools/testing/inventory.py [out.csv] [path/to/gamepro-scripts]
"""
import ast, csv, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(APP, 'docs', 'testing', 'inventory.csv')
REPO = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(APP), 'gamepro-scripts')
CTRL = {'press_a', 'press_b', 'press_x', 'press_y', 'press_up', 'press_down', 'press_left', 'press_right',
        'hold_up', 'hold_down', 'hold_left', 'hold_right', 'release_all', 'soft_reset', 'soft_reset_z',
        'wonder_trade', 'read_calibration', 'write_calibration', 'read_light_value', 'is_open', 'close',
        'wait_for_direction', 'set_direction_input', 'fire'}
FOLDER_GAME = {
    'gen_2_vc': ('Gold / Silver / Crystal (Virtual Console)', '3DS'),
    'gen_3_frlg': ('FireRed / LeafGreen', 'Switch (NSO GBA)'),
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

def git_date(path):
    r = subprocess.run(['git', '-C', REPO, 'log', '-1', '--format=%cs', '--', path], capture_output=True, text=True)
    return r.stdout.strip() or 'untracked'

def cls_attrs(tree):
    for n in tree.body:
        if isinstance(n, ast.ClassDef):
            d = {}
            for b in n.body:
                if isinstance(b, ast.Assign) and len(b.targets) == 1 and isinstance(b.targets[0], ast.Name):
                    try:
                        d[b.targets[0].id] = ast.literal_eval(b.value)
                    except Exception:
                        d[b.targets[0].id] = '<expr>'
            if any(isinstance(base, ast.Name) and base.id == 'BaseScript' for base in n.bases):
                return n.name, d
    return None, {}

rows = []
for root, dirs, files in os.walk(os.path.join(REPO, 'Beta')):
    dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not d.startswith('_'))
    for fn in sorted(files):
        if not fn.endswith('.py') or fn == '__init__.py':
            continue
        p = os.path.join(root, fn)
        rel = os.path.relpath(p, REPO).replace(os.sep, '/')
        src = open(p, encoding='utf-8', errors='replace').read()
        folder = rel.split('/')[1]
        game, console = FOLDER_GAME.get(folder, ('?', '?'))
        flags = []
        try:
            tree = ast.parse(src)
            doc = (ast.get_docstring(tree) or '').strip()
            cls, attrs = cls_attrs(tree)
        except SyntaxError as e:
            doc, cls, attrs = '', None, {}
            flags.append(f'SYNTAX ERROR line {e.lineno}')
        mg = re.search(r'^Game:\s*(?:Pokemon\s+)?(.+)$', doc, re.M)
        if mg and len(mg.group(1)) < 70:
            game = mg.group(1).strip()
        if cls is None and not flags:
            flags.append('no BaseScript subclass (not shown in app)')
        m = re.search(r'[Pp]orted from\s+([\w .\-()]+?\.(?:cpp|ino|c))', src)
        if m: flags.append('ported from ' + m.group(1))
        n_sleep = len(re.findall(r'\btime\.sleep\(', src))
        n_wait = len(re.findall(r'\.wait\(', src))
        stop_checks = len(re.findall(r'stop_event\.is_set\(\)|self\.stop\.is_set\(\)', src))
        if n_sleep: flags.append(f'time.sleep x{n_sleep} (ignores Stop)')
        if re.search(r'while\s+True\s*:', src): flags.append('while True loop')
        if stop_checks == 0 and n_wait == 0: flags.append('never checks stop_event')
        used = sorted(set(re.findall(r'controller\.(\w+)\(', src)) | set(re.findall(r'self\.c\.(\w+)\(', src)))
        unknown = [u for u in used if u not in CTRL]
        if unknown: flags.append('unknown controller calls: ' + ' '.join(unknown))
        if 'wonder_trade' in used: flags.append("uses wonder_trade ('W' = + on Switch)")
        if re.search(r'\bSleep\(|delay\(|digitalWrite|Serial\.', src): flags.append('C++/Arduino syntax left in')
        if re.search(r'COM\d', src): flags.append('hard-coded COM port')
        if 'cv2.imshow' in src or 'input(' in src: flags.append('imshow/input (blocks GUI thread?)')
        if 'TODO' in src or 'FIXME' in src: flags.append('TODO/FIXME')
        if '_cal_path' in src or "calibration'" in src or 'calibration"' in src: flags.append('saves own calibration JSON')
        uses_cam = bool(re.search(r'get_latest_frame|frame_grabber\.|request_calibration\(', src))
        uses_ldr = 'read_light_value' in src
        if not uses_cam and not uses_ldr: flags.append('blind timing only (no camera / light sensor)')
        rows.append({
            'script': fn[:-3], 'path': rel, 'name': attrs.get('NAME', ''), 'game': game, 'console': console,
            'purpose': (attrs.get('DESCRIPTION') or doc.split('\n\n')[0].replace('\n', ' '))[:160],
            'lines': src.count('\n') + 1, 'last_modified': git_date(rel),
            'detection': ('camera' if uses_cam else '') + ('+light sensor' if uses_ldr else ''),
            'has_readme': os.path.exists(p[:-3] + '.md'), 'has_settings': 'SETTINGS' in attrs,
            'controller_calls': ' '.join(used), 'flags': '; '.join(flags),
        })
with open(OUT, 'w', newline='', encoding='utf-8') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
print(len(rows), 'scripts ->', OUT)
from collections import Counter
c = Counter()
for r in rows:
    for fl in r['flags'].split('; '):
        if fl: c[re.sub(r'[:x]\s*[\d.,\s]+.*$|from .*', '', fl).strip()] += 1
for k, v in c.most_common(): print(f'{v:4d}  {k}')
print(Counter(r['detection'] or 'none' for r in rows))
print(Counter(r['last_modified'][:7] for r in rows))
