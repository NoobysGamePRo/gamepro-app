"""Compare each Beta script with the C++ program it was ported from (Phase 1, backlog only).

usage: python tools/testing/port_compare.py [--cpp DIR] [--scripts PATH] [--out CSV]

For both versions it flattens the main routine into the sequence of Arduino commands it sends
(button chars, as in core/controller.py) and notes how it detects things (light sensor / camera).
Simple counted loops are unrolled and helper functions inlined, both branches of an if are kept.
The similarity of the two button sequences (0-1) is a quick way to find ports that don't do what the
original did; anything low gets read by a person.  Output: docs/testing/port-compare.csv
"""
import argparse
import ast
import csv
import difflib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(os.path.dirname(HERE))
DEFAULT_CPP = os.path.join(os.path.expanduser('~'), 'Dropbox', 'GamePRo Software', 'Windows', 'Source Code')

PY_CHARS = {
    'press_a': 'a', 'press_b': 'b', 'press_x': 'x', 'press_y': 'y',
    'press_up': 'e', 'press_down': 'd', 'press_left': 's', 'press_right': 'f',
    'hold_up': '8', 'hold_down': '2', 'hold_left': '4', 'hold_right': '6',
    'release_all': '0', 'soft_reset': 'S', 'soft_reset_z': 'Z', 'wonder_trade': 'W',
    'press_plus': 'W', 'press_home': 'S', 'press_start': '?start',
}
MAX_UNROLL = 12

# Checked by hand where the name/header match is wrong.  '' = written new, no C++ original.
ORIGINAL = {
    'random_encounter': 'VC_Gen_2_Random_Encounter_Shiny.cpp',
    'dp_shiny_starter': 'Diamond_Pearl_Shiny_Starter.cpp',
    'horde_encounters': 'Horde_Encounters.cpp',
    'FRLG_Breeding': '', 'FRLG_Shiny_Lapras': '', 'FRLG_shiny_starter': '',
    'legends_za_bench': '',          # original is Legends_ZA_Bench.py, not C++
}


# ── C++ ────────────────────────────────────────────────────────────────────────

def strip_comments(src):
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    return re.sub(r'//[^\n]*', '', src)


def match_brace(src, i):
    """Index just past the '}' matching the '{' at src[i]."""
    depth = 0
    for j in range(i, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return j + 1
    return len(src)


def cpp_functions(src):
    funcs = {}
    for m in re.finditer(r'\n[\w:<>\*& ]+?\b(\w+)\s*\([^;{)]*\)\s*\{', src):
        name = m.group(1)
        if name in ('if', 'while', 'for', 'switch'):
            continue
        start = m.end() - 1
        funcs[name] = src[start + 1:match_brace(src, start) - 1]
    return funcs


TOKEN = re.compile(
    r"outputChars\[0\]\s*=\s*'(.)'"                     # 1 command char
    r"|\bSleep\(\s*(\d+)\s*\)"                          # 2 wait ms
    r"|\bfor\s*\(\s*int\s+\w+\s*=\s*(\d+)\s*;\s*\w+\s*<=?\s*(\d+)\s*;[^)]*\)\s*\{"   # 3,4 counted loop
    r"|\b(\w+)\s*\(")                                    # 5 call


def cpp_seq(body, funcs, depth=0, stack=()):
    seq, i = [], 0
    while True:
        m = TOKEN.search(body, i)
        if not m:
            break
        if m.group(1):
            seq.append(('cmd', m.group(1)))
            i = m.end()
        elif m.group(2):
            seq.append(('wait', int(m.group(2))))
            i = m.end()
        elif m.group(3) is not None:
            start = m.end() - 1
            end = match_brace(body, start)
            n = int(m.group(4)) - int(m.group(3)) + ('<=' in m.group(0))
            inner = cpp_seq(body[start + 1:end - 1], funcs, depth, stack)
            if any(k == 'cmd' for k, _ in inner):
                seq += inner * max(0, min(n, MAX_UNROLL))
            else:
                seq += inner[:]                       # a polling loop: keep its detection once
            i = end
        else:
            name = m.group(5)
            i = m.end()
            if name in ('ReadFile',):
                seq.append(('detect', 'light'))
            elif name in funcs and name not in stack and depth < 4:
                seq += cpp_seq(funcs[name], funcs, depth + 1, stack + (name,))
            elif re.match(r'(imread|imshow|cvtColor|inRange|matchTemplate|countNonZero|mean|VideoCapture|capture|read)$', name):
                seq.append(('detect', 'camera'))
    return seq


def cpp_main(src):
    src = strip_comments(src)
    funcs = cpp_functions(src)
    body = funcs.get('main', src)
    # drop the serial port boilerplate before the first command
    first = body.find('outputChars[0]')
    return cpp_seq(body[max(0, first):], funcs)


# ── Python ─────────────────────────────────────────────────────────────────────

class PyFlattener:
    def __init__(self, tree):
        self.methods, self.consts, self.funcs = {}, {}, {}
        for n in tree.body:
            if isinstance(n, ast.FunctionDef):
                self.funcs[n.name] = n
            if isinstance(n, ast.ClassDef):
                for b in n.body:
                    if isinstance(b, ast.FunctionDef):
                        self.methods[b.name] = b
                    elif isinstance(b, ast.Assign) and isinstance(b.targets[0], ast.Name):
                        try:
                            v = ast.literal_eval(b.value)
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                self.consts[b.targets[0].id] = v
                        except Exception:
                            pass

    def num(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Attribute) and node.attr in self.consts:
            return self.consts[node.attr]
        if isinstance(node, ast.Name) and node.id in self.consts:
            return self.consts[node.id]
        if isinstance(node, ast.BinOp):
            a, b = self.num(node.left), self.num(node.right)
            if a is not None and b is not None:
                if isinstance(node.op, ast.Sub):
                    return a - b
                if isinstance(node.op, ast.Add):
                    return a + b
                if isinstance(node.op, ast.Mult):
                    return a * b
                if isinstance(node.op, ast.Div) and b:
                    return a / b
        return None

    def calls(self, node, stack):
        out = []
        for n in ast.walk(node) if not isinstance(node, ast.Call) else [node]:
            if isinstance(n, ast.Call):
                out += self.call(n, stack)
        return out

    def call(self, n, stack):
        f = n.func
        if isinstance(f, ast.Attribute):
            if f.attr in PY_CHARS and not (isinstance(f.value, ast.Name) and f.value.id == 'self'):
                return [('cmd', PY_CHARS[f.attr])]
            if f.attr == 'fire' and n.args and isinstance(n.args[0], ast.Constant):
                return [('cmd', n.args[0].value)]
            if f.attr == '_send' and n.args and isinstance(n.args[0], ast.Constant):
                return [('cmd', n.args[0].value)]
            if f.attr == 'read_light_value':
                return [('detect', 'light')]
            if f.attr in ('get_latest_frame', 'avg_rgb'):
                return [('detect', 'camera')]
            if f.attr in ('wait', 'sleep') and n.args:
                v = self.num(n.args[0])
                is_stop = isinstance(f.value, ast.Name) and f.value.id in ('stop_event', 'stop')
                return [('wait', int(v * 1000))] if v is not None and not is_stop else []
            if isinstance(f.value, ast.Name) and f.value.id == 'self' and f.attr in self.methods and f.attr not in stack and len(stack) < 5:
                return self.block(self.methods[f.attr].body, stack + (f.attr,))
        if isinstance(f, ast.Name) and f.id in self.funcs and f.id not in stack and len(stack) < 5:
            return self.block(self.funcs[f.id].body, stack + (f.id,))
        return []

    def block(self, stmts, stack):
        seq = []
        for s in stmts:
            if isinstance(s, ast.For):
                n = None
                it = s.iter
                if isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == 'range' and it.args:
                    args = [self.num(a) for a in it.args]
                    if all(a is not None for a in args):
                        n = int(args[0]) if len(args) == 1 else int(args[1] - args[0])
                body = self.block(s.body, stack)
                if n is not None and any(k == 'cmd' for k, _ in body):
                    seq += body * max(0, min(n, MAX_UNROLL))
                else:
                    seq += body
            elif isinstance(s, ast.While):
                seq += self.calls(s.test, stack) + self.block(s.body, stack)
            elif isinstance(s, ast.If):
                seq += self.calls(s.test, stack) + self.block(s.body, stack) + self.block(s.orelse, stack)
            elif isinstance(s, (ast.With, ast.Try)):
                seq += self.block(s.body, stack)
                for h in getattr(s, 'handlers', []):
                    seq += self.block(h.body, stack)
                seq += self.block(getattr(s, 'finalbody', []), stack)
            elif isinstance(s, (ast.FunctionDef, ast.ClassDef)):
                continue
            else:
                for n in ast.walk(s):
                    if isinstance(n, ast.Call):
                        # only the outermost calls; nested ones are visited by ast.walk anyway
                        seq += self.call(n, stack)
        return seq


def py_main(src):
    tree = ast.parse(src)
    fl = PyFlattener(tree)
    if 'run' not in fl.methods:
        return []
    return fl.block(fl.methods['run'].body, ('run',))


# ── Matching and report ────────────────────────────────────────────────────────

def norm(s):
    s = re.sub(r'_?\d+(\.\d+)*$', '', os.path.splitext(os.path.basename(s))[0])
    return re.sub(r'[^a-z0-9]', '', s.lower())


def find_original(src, py_name, cpp_files):
    by_norm = {norm(f): f for f in cpp_files}
    m = re.search(r'[Pp]orted from\s+([\w .\-()]+?\.cpp)', src)
    if m and norm(m.group(1)) in by_norm:
        return by_norm[norm(m.group(1))], 'header'
    if m:
        close = difflib.get_close_matches(norm(m.group(1)), list(by_norm), n=1, cutoff=0.6)
        if close:
            return by_norm[close[0]], 'header~'
    close = difflib.get_close_matches(norm(py_name), list(by_norm), n=1, cutoff=0.55)
    if close:
        return by_norm[close[0]], 'name~'
    return '', ''


def summary(seq):
    cmds = ''.join(v for k, v in seq if k == 'cmd' and len(v) == 1)
    det = sorted({v for k, v in seq if k == 'detect'})
    return cmds, '+'.join(det) or 'none'


def diff_text(a, b, limit=6):
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal':
            out.append(f'{tag} C++[{i1}:{i2}]={a[i1:i2][:20]!r} Py[{j1}:{j2}]={b[j1:j2][:20]!r}')
        if len(out) >= limit:
            out.append('...')
            break
    return '; '.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cpp', default=DEFAULT_CPP)
    ap.add_argument('--scripts', default=os.path.join(os.path.dirname(APP), 'gamepro-scripts'))
    ap.add_argument('--out', default=os.path.join(APP, 'docs', 'testing', 'port-compare.csv'))
    a = ap.parse_args()
    cpp_files = [f for f in os.listdir(a.cpp) if f.endswith('.cpp')]
    rows, used = [], set()
    for root, dirs, files in os.walk(os.path.join(a.scripts, 'Beta')):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and not d.startswith(('_', '.')))
        for fn in sorted(files):
            if not fn.endswith('.py') or fn == '__init__.py':
                continue
            p = os.path.join(root, fn)
            src = open(p, encoding='utf-8', errors='replace').read()
            if fn[:-3] in ORIGINAL:
                orig, how = ORIGINAL[fn[:-3]], 'manual' if ORIGINAL[fn[:-3]] else 'new'
            else:
                orig, how = find_original(src, fn, cpp_files)
            py = py_main(src)
            pc, pd = summary(py)
            row = {'script': fn[:-3], 'original': orig, 'matched_by': how, 'similarity': '',
                   'cpp_detection': '', 'py_detection': pd, 'cpp_cmds': '', 'py_cmds': pc[:120], 'differences': ''}
            if orig:
                used.add(orig)
                cs = cpp_main(open(os.path.join(a.cpp, orig), encoding='utf-8', errors='replace').read())
                cc, cd = summary(cs)
                row.update(similarity=f'{difflib.SequenceMatcher(None, cc, pc, autojunk=False).ratio():.2f}',
                           cpp_detection=cd, cpp_cmds=cc[:120], differences=diff_text(cc, pc))
            rows.append(row)
    with open(a.out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(len(rows), 'scripts ->', a.out)
    print('C++ programs with no Python port:', ', '.join(sorted(set(cpp_files) - used)))


if __name__ == '__main__':
    main()
