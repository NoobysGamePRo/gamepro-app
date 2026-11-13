"""
GamePRo headless test mode — run one script against a spec and record what happened.

    python tools/testing/gamepro_test.py --script Beta/gen_2_vc/crystal_shiny_eevee \
        --rig switch1 --cycles 3 --out test_runs

The script is loaded and run by core/runner.py, the same code the app's Run button uses.
Only the things a person would do in the GUI are replaced:
  - request_calibration() is answered from the rig profile (tests/rigs/<rig>.yaml);
  - SETTINGS come from the spec's `settings:` instead of the settings dialog;
  - controller.wait_for_direction() is answered from the spec's `directions:` list.

Safety (all enforced here, whatever the script does):
  - hard timeout, and a cycle limit counted from the spec's cycle_log regex;
  - abort if the camera stops delivering frames, or the picture stops changing for longer
    than the spec's max_static_seconds;
  - on every exit path: stop the script, then release_all() and close the port.

Developer/test-PC only: not imported by main.py, not bundled in the exe.
See tools/testing/README.md.
"""

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

import cv2
import numpy as np
import yaml

APP_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.camera import FrameGrabber                                   # noqa: E402
from core.controller import GameProController                          # noqa: E402
from core.runner import (default_scripts_dir, load_script_class,      # noqa: E402
                         run_script, corners_to_warp)
from core.screen_detect import detect_screen_live                      # noqa: E402
import rig as rigmod                                                   # noqa: E402

SPECS_DIR = os.path.join(APP_DIR, 'tests', 'specs')

# Lines that mean the script found what it was hunting (spec `target_log` overrides).
DEFAULT_TARGET_LOG = (r'(?i)(press stop when done|catch your shiny|\*\*\*.*shiny'
                      r'|shiny (found|detected)|found a shiny)')
DEFAULTS = {
    'timeout_minutes': 30,
    'max_cycles': 1000,          # hard ceiling whatever --cycles says
    'max_static_seconds': 120,   # picture unchanged this long -> abort
    'static_threshold': 1.5,     # mean abs grey difference that counts as "changed"
    'camera_lost_seconds': 10,   # no new frame this long -> abort
    'stop_grace_seconds': 20,    # time the script gets to return after stop
    'max_frames_per_checkpoint': 20,
}


# ── Spec ──────────────────────────────────────────────────────────────────────

def parse_when(when):
    """Checkpoint `when` -> {'log': regex, 'delay': s} or {'every': s}, or None if not usable."""
    if isinstance(when, dict):
        if 'log' in when:
            return {'log': re.compile(when['log']), 'delay': float(when.get('delay_seconds', 0))}
        if 'every_minutes' in when:
            return {'every': float(when['every_minutes']) * 60}
        return None
    if isinstance(when, str):
        m = re.match(r'\s*log\s*:\s*(.+)$', when)
        if m:
            return {'log': re.compile(m.group(1).strip()), 'delay': 0.0}
        m = re.match(r'\s*every\s+([\d.]+)\s*min', when)
        if m:
            return {'every': float(m.group(1)) * 60}
    return None


def find_spec(script_rel: str):
    stem = os.path.splitext(os.path.basename(script_rel))[0]
    hits = glob.glob(os.path.join(SPECS_DIR, '**', stem + '.yaml'), recursive=True)
    return hits[0] if len(hits) == 1 else None


def resolve_script(script: str, scripts_dir: str) -> str:
    """Accept 'Beta/gen_2_vc/crystal_shiny_eevee(.py)', a bare stem, or an absolute path."""
    if os.path.isabs(script) and os.path.isfile(script):
        return script
    cand = os.path.join(scripts_dir, script)
    for p in (cand, cand + '.py'):
        if os.path.isfile(p):
            return p
    stem = os.path.splitext(os.path.basename(script))[0]
    hits = [p for p in glob.glob(os.path.join(scripts_dir, '**', stem + '.py'), recursive=True)
            if '__pycache__' not in p]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f'Script not found or ambiguous: {script} ({len(hits)} matches)')


def prepare_sandbox(scripts_dir: str, sandbox: str, fresh_calibration: bool = False) -> str:
    """
    Copy the scripts tree into a per-rig sandbox and run from there. Scripts save calibration
    and other data next to themselves (e.g. Beta/calibration/<name>.json); this keeps that out
    of the real scripts folder while still carrying it from one run to the next on this rig.
    """
    if fresh_calibration:
        for d in glob.glob(os.path.join(sandbox, '*', 'calibration')):
            shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(scripts_dir, sandbox, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', '__pycache__', 'calibration', 'stuck_*.png'))
    return sandbox


def sha256(path: str) -> str:
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def git_rev(path: str) -> str:
    try:
        rev = subprocess.run(['git', '-C', path, 'rev-parse', '--short', 'HEAD'],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(['git', '-C', path, 'status', '--porcelain', '--untracked-files=no'],
                               capture_output=True, text=True, timeout=10).stdout.strip()
        return rev + ('-dirty' if dirty else '') if rev else ''
    except Exception:
        return ''


# ── One run ───────────────────────────────────────────────────────────────────

class HeadlessRun:
    """Runs one script through core.runner.run_script with the safety rules above."""

    def __init__(self, script_path, spec, rig, out_dir, controller, grabber,
                 cycles=None, timeout_minutes=None, calibrate=False, settings=None,
                 scripts_dir=None, source_dir=None, auto_corners=True, echo=True):
        self.script_path = script_path
        self.spec = spec or {}
        self.rig = rig or {'corners': {}, 'rects': {}}
        self.out_dir = out_dir
        self.controller = controller
        self.grabber = grabber
        self.calibrate = calibrate
        self.auto_corners = auto_corners
        self.echo = echo
        self.scripts_dir = scripts_dir
        self.source_dir = source_dir or scripts_dir

        lim = dict(DEFAULTS)
        for k in DEFAULTS:
            if self.spec.get(k) is not None:
                lim[k] = self.spec[k]
        if timeout_minutes is not None:
            lim['timeout_minutes'] = timeout_minutes
        self.lim = lim
        want = cycles if cycles is not None else self.spec.get('cycles')
        self.cycles_target = min(int(want), lim['max_cycles']) if want else lim['max_cycles']

        self.cycle_re = re.compile(self.spec['cycle_log']) if self.spec.get('cycle_log') else None
        self.target_re = re.compile(self.spec.get('target_log') or DEFAULT_TARGET_LOG)
        self.settings_override = dict(self.spec.get('settings') or {})
        self.settings_override.update(settings or {})
        self.directions = list(self.spec.get('directions') or [])
        self.cal_specs = list(self.spec.get('calibration') or [])

        self.checkpoints, self.warnings = [], []
        for cp in self.spec.get('checkpoints') or []:
            w = parse_when(cp.get('when'))
            if w is None:
                self.warnings.append(f"checkpoint '{cp.get('name')}' skipped: "
                                     f"'when' is not 'log: <regex>' or 'every N minutes'")
            else:
                self.checkpoints.append({'name': cp.get('name', 'checkpoint'), **w,
                                         'taken': 0, 'next': w.get('every')})

        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.t0 = None
        self.status = None
        self.reason = ''
        self.errors = []
        self.frames = []
        self.cycle_times = []
        self.target_line = None
        self.rect_count = 0
        self.warp = None
        self.cal_used = []
        self.paused_watchdog = False
        self.log_file = None

    # -- helpers -------------------------------------------------------------

    def now(self):
        return time.time() - self.t0

    def finish(self, status, reason=''):
        """First caller wins; asks the script to stop."""
        with self.lock:
            if self.status is None:
                self.status, self.reason = status, reason
                self.log(f'[test] stopping: {status}{" - " + reason if reason else ""}', internal=True)
        self.stop_event.set()

    def save_frame(self, name, line=None, frame=None):
        if frame is None and self.grabber is not None:
            frame = self.grabber.get_latest_frame()
        if frame is None:
            return None
        n = len(self.frames) + 1
        safe = re.sub(r'[^A-Za-z0-9_-]+', '_', name)[:40]
        rel = os.path.join('frames', f'{n:03d}_{safe}.png')
        cv2.imwrite(os.path.join(self.out_dir, rel), frame)
        self.frames.append({'name': name, 't': round(self.now(), 2),
                            'cycle': len(self.cycle_times), 'path': rel.replace('\\', '/'),
                            'log_line': line})
        return rel

    def log(self, msg, internal=False):
        """The log callable the script gets (plus the runner's own lines)."""
        t = self.now() if self.t0 else 0.0
        line = f'{t:9.2f}  {msg}'
        if self.log_file:
            self.log_file.write(line + '\n')
            self.log_file.flush()
        if self.echo:
            print(line, flush=True)
        if internal:
            return
        if self.cycle_re and self.cycle_re.search(msg):
            self.cycle_times.append(round(t, 2))
            if len(self.cycle_times) >= self.cycles_target:
                self.finish('completed', f'{len(self.cycle_times)} cycles')
        for cp in self.checkpoints:
            if 'log' in cp and cp['log'].search(msg) and cp['taken'] < self.lim['max_frames_per_checkpoint']:
                cp['taken'] += 1
                if cp['delay'] > 0:
                    threading.Timer(cp['delay'], self.save_frame, (cp['name'], msg)).start()
                else:
                    self.save_frame(cp['name'], msg)
        if self.target_line is None and self.target_re.search(msg):
            self.target_line = msg
            self.save_frame('target', msg)
            threading.Timer(2.0, self._target_settled, (msg,)).start()

    def _target_settled(self, msg):
        self.save_frame('target_settled', msg)
        self.finish('target-found', msg)

    # -- what the GUI user would do ---------------------------------------------

    def request_calibration(self, prompt, mode='rect'):
        if self.stop_event.is_set():
            return None if mode == 'corners' else (0, 0, 1, 1)
        stem = os.path.splitext(os.path.basename(self.script_path))[0]
        entry = next((c for c in self.cal_specs if c.get('prompt') == prompt and c.get('mode', 'rect') == mode), None) \
            or next((c for c in self.cal_specs if c.get('mode', 'rect') == mode), None)
        if entry in self.cal_specs:
            self.cal_specs.remove(entry)
        key = (entry or {}).get('rig_key')
        if mode == 'corners':
            if not key or key == 'TODO':
                key = 'default'
            table = 'corners'
        else:
            self.rect_count += 1
            if not key or key == 'TODO':
                key = f'{stem}.{self.rect_count}'
            table = 'rects'
        value, source = None, 'rig'
        if mode == 'corners' and self.auto_corners:
            # Same detection the app offers for one-click confirm; the rig value is the backup
            value = detect_screen_live(self.grabber, stop_event=self.stop_event)
            source = 'auto'
            if value is None:
                self.log('[test] screen not detected automatically - using the rig corners', internal=True)
        if value is None:
            value, source = self.rig[table].get(key), 'rig'
        if value is None and self.calibrate:
            source = 'manual'
            self.paused_watchdog = True
            try:
                value = (rigmod.capture_corners(self.grabber, prompt) if mode == 'corners'
                         else rigmod.capture_rect(self.grabber, self.warp, prompt))
            finally:
                self.paused_watchdog = False
            if value is not None and '_path' in self.rig:
                self.rig[table][key] = value
                rigmod.save_rig(self.rig)
                self.log(f'[test] saved {table}.{key} = {value} to {os.path.basename(self.rig["_path"])}', internal=True)
        self.cal_used.append({'mode': mode, 'prompt': prompt, 'rig_key': f'{table}.{key}',
                              'source': source, 'value': value})
        if value is None:
            self.save_frame('needs_calibration', prompt)
            self.finish('needs-calibration',
                        f'rig has no {table}.{key} for "{prompt}" - rerun with --calibrate')
            return None if mode == 'corners' else (0, 0, 1, 1)
        self.log(f'[test] calibration "{prompt}" answered from '
                 f'{"automatic screen detection" if source == "auto" else table + "." + key}', internal=True)
        if mode == 'corners':
            self.warp = corners_to_warp([tuple(p) for p in value])
            return self.warp
        return tuple(int(v) for v in value)

    def _answer_direction(self):
        if not getattr(self.controller, '_waiting_for_direction', False):
            return
        if self.directions:
            d = self.directions.pop(0)
            self.log(f'[test] answering direction prompt: {d}', internal=True)
            self.controller.set_direction_input(d)
        else:
            self.save_frame('needs_input')
            self.finish('needs-input', 'script asked for a D-pad direction; add `directions:` to the spec')

    # -- watchdog ------------------------------------------------------------

    def _watch(self):
        last_new = last_change = time.time()
        prev_bytes, ref = None, None
        while not self.stop_event.is_set():
            time.sleep(0.5)
            now = time.time()
            if self.now() > self.lim['timeout_minutes'] * 60:
                self.save_frame('timeout')
                self.finish('timeout', f'hard timeout {self.lim["timeout_minutes"]} min')
                break
            self._answer_direction()
            for cp in self.checkpoints:
                if cp.get('every') and self.now() >= cp['next'] and cp['taken'] < self.lim['max_frames_per_checkpoint']:
                    cp['taken'] += 1
                    cp['next'] += cp['every']
                    self.save_frame(cp['name'])
            if self.paused_watchdog:
                last_new = last_change = now
                continue
            frame = self.grabber.get_latest_frame()
            if frame is None:
                if now - last_new > self.lim['camera_lost_seconds']:
                    self.finish('camera-lost', f'no camera frame for {self.lim["camera_lost_seconds"]} s')
                    break
                continue
            small = cv2.cvtColor(cv2.resize(frame, (160, 120), interpolation=cv2.INTER_AREA),
                                 cv2.COLOR_BGR2GRAY)
            b = small.tobytes()
            if b != prev_bytes:            # sensor noise means a live camera never repeats exactly
                last_new, prev_bytes = now, b
            elif now - last_new > self.lim['camera_lost_seconds']:
                self.save_frame('camera_lost')
                self.finish('camera-lost', f'camera frame identical for {self.lim["camera_lost_seconds"]} s')
                break
            if ref is None or float(np.mean(cv2.absdiff(small, ref))) > self.lim['static_threshold']:
                ref, last_change = small, now
            elif now - last_change > self.lim['max_static_seconds']:
                self.save_frame('camera_frozen')
                self.finish('camera-frozen', f'picture unchanged for {self.lim["max_static_seconds"]} s')
                break

    # -- main ----------------------------------------------------------------

    def execute(self) -> dict:
        os.makedirs(os.path.join(self.out_dir, 'frames'), exist_ok=True)
        self.log_file = open(os.path.join(self.out_dir, 'log.txt'), 'w', encoding='utf-8')
        self.t0 = time.time()
        started = dt.datetime.now().isoformat(timespec='seconds')
        script_name, thread, crash, graced = None, None, {}, False
        for w in self.warnings:
            self.log(f'[test] warning: {w}', internal=True)
        try:
            # Camera must be live before any button is pressed
            deadline = time.time() + self.lim['camera_lost_seconds']
            while self.grabber.get_latest_frame() is None and time.time() < deadline:
                time.sleep(0.1)
            if self.grabber.get_latest_frame() is None:
                self.finish('camera-lost', 'no camera frame before start')
                return self._write(started, script_name)

            cls = load_script_class(self.script_path, lambda m: self.log(m, internal=True))
            if cls is None:
                self.finish('error', 'script failed to load')
                self.errors.append('load failed (see log.txt)')
                return self._write(started, script_name)
            script = cls()
            script_name = cls.NAME
            if getattr(cls, 'SETTINGS', None):
                from gui.settings_dialog import defaults
                values = defaults(cls.SETTINGS)
                unknown = [k for k in self.settings_override if k not in values]
                if unknown:
                    self.warnings.append(f'unknown settings ignored: {unknown}')
                values.update({k: v for k, v in self.settings_override.items() if k in values})
                script.settings = values
            self.settings_used = getattr(script, 'settings', None)

            self.save_frame('start')
            self.log(f'Starting: {script.NAME}', internal=True)

            def target():
                crash['exc'] = run_script(script, controller=self.controller,
                                          frame_grabber=self.grabber, stop_event=self.stop_event,
                                          log=self.log, request_calibration=self.request_calibration)
            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            watcher = threading.Thread(target=self._watch, daemon=True)
            watcher.start()
            while thread.is_alive() and not self.stop_event.is_set():
                thread.join(0.2)
            self.stop_event.set()
            thread.join(self.lim['stop_grace_seconds'])     # stop requested: give it time to return
            graced = True
            exc = crash.get('exc')
            if thread.is_alive():
                pass                                        # reported in finally
            elif exc is not None:
                self.errors.append(''.join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
                self.save_frame('error', str(exc))
                self.finish('error', f'script raised {type(exc).__name__}: {exc}')
            else:
                self.finish('script-ended', 'script returned on its own')
        except KeyboardInterrupt:
            self.save_frame('interrupted')
            self.finish('interrupted', 'Ctrl+C')
        except Exception as e:
            self.errors.append(traceback.format_exc())
            self.save_frame('error', str(e))
            self.finish('error', f'runner error: {e}')
        finally:
            self.stop_event.set()
            if thread is not None and thread.is_alive():
                if not graced:
                    thread.join(self.lim['stop_grace_seconds'])
                if thread.is_alive():
                    self.errors.append(f'script did not return within {self.lim["stop_grace_seconds"]} s of stop')
            try:
                self.controller.release_all()
                self.log('[test] release_all sent', internal=True)
            except Exception as e:
                self.errors.append(f'release_all failed: {e}')
            self.save_frame('final')
        return self._write(started, script_name)

    def _write(self, started, script_name) -> dict:
        ct = self.cycle_times
        durations = [round(b - a, 2) for a, b in zip(ct, ct[1:])]
        result = {
            'status': self.status,
            'reason': self.reason,
            'script': os.path.relpath(self.script_path, self.scripts_dir).replace('\\', '/')
                      if self.scripts_dir else self.script_path,
            'script_name': script_name,
            'script_sha256': sha256(self.script_path),
            'spec': self.spec.get('_path'),
            'spec_draft': bool(self.spec.get('draft')),
            'rig': self.rig.get('name'),
            'started': started,
            'ended': dt.datetime.now().isoformat(timespec='seconds'),
            'duration_s': round(self.now(), 1),
            'cycles_target': self.cycles_target,
            'cycles_completed': len(ct),
            'cycle_log_times_s': ct,
            'cycle_durations_s': durations,
            'mean_cycle_s': round(sum(durations) / len(durations), 2) if durations else None,
            'target_line': self.target_line,
            'limits': self.lim,
            'settings': getattr(self, 'settings_used', None),
            'calibration': self.cal_used,
            'errors': self.errors,
            'warnings': self.warnings,
            'frames': self.frames,
            'log': 'log.txt',
            'versions': {'app': git_rev(APP_DIR),
                         'scripts': git_rev(self.source_dir) if self.source_dir else ''},
        }
        with open(os.path.join(self.out_dir, 'result.json'), 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2)
        if self.log_file:
            self.log_file.close()
            self.log_file = None
        return result


# ── Fake hardware (tests of this tool, no robot attached) ────────────────────

class FakeController(GameProController):
    """Records commands instead of sending them. Same button methods as the real one."""

    def __init__(self, light_value=500):
        self._lock = threading.Lock()
        self._ldr_display_callback = None
        self._waiting_for_direction = False
        self._direction_event = threading.Event()
        self._direction_value = None
        self.sent = []
        self.light_value = light_value
        self._open = True

    def _send(self, char):
        time.sleep(0.02)
        self.sent.append(char)

    def fire(self, char):
        self.sent.append(char)

    def read_light_value(self):
        return self.light_value

    def is_open(self):
        return self._open

    def close(self):
        self._open = False


class FakeGrabber(FrameGrabber):
    """Serves a still image (or a folder of images in turn) with sensor-like noise."""

    def __init__(self, source=None, noise=3, frozen=False, period=1.0):
        self._lock = threading.Lock()
        self._crop = None
        self._detect_overlay = None
        self._frame = None
        self._running = True
        imgs = []
        if source and os.path.isdir(source):
            imgs = [cv2.imread(p) for p in sorted(glob.glob(os.path.join(source, '*.png')))]
        elif source:
            imgs = [cv2.imread(source)]
        imgs = [cv2.resize(i, (640, 480)) for i in imgs if i is not None]
        self._imgs = imgs or [np.full((480, 640, 3), 90, np.uint8)]
        self._noise, self._frozen, self._period = noise, frozen, period
        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def _grab_loop(self):
        t0 = time.time()
        first = None
        while self._running:
            img = self._imgs[int((time.time() - t0) / self._period) % len(self._imgs)]
            if self._frozen and first is not None:
                frame = first
            else:
                n = np.random.randint(-self._noise, self._noise + 1, img.shape, dtype=np.int16)
                frame = np.clip(img.astype(np.int16) + n, 0, 255).astype(np.uint8)
                first = first if first is not None else frame
            with self._lock:
                self._frame = frame
            time.sleep(1 / 30)

    def is_opened(self):
        return True

    def release(self):
        self._running = False


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description='Run one GamePRo script headless against its test spec.')
    ap.add_argument('--script', required=True,
                    help="e.g. Beta/gen_2_vc/crystal_shiny_eevee, or just crystal_shiny_eevee")
    ap.add_argument('--spec', help='spec YAML (default: tests/specs/**/<script>.yaml)')
    ap.add_argument('--rig', help='rig name in tests/rigs/ or a path (port, camera, calibration answers)')
    ap.add_argument('--port', help='serial port (overrides the rig)')
    ap.add_argument('--camera', type=int, help='camera index (overrides the rig)')
    ap.add_argument('--cycles', type=int, help='stop after N cycles (default: spec cycles)')
    ap.add_argument('--timeout-minutes', type=float, help='hard timeout (default: spec, else 30)')
    ap.add_argument('--out', default=os.path.join(APP_DIR, 'test_runs'),
                    help='results folder; each run gets its own subfolder')
    ap.add_argument('--set', action='append', default=[], metavar='KEY=VALUE',
                    help='override a script SETTINGS value (YAML value syntax)')
    ap.add_argument('--calibrate', action='store_true',
                    help='if the rig lacks a calibration answer, ask for it in a window and save it')
    ap.add_argument('--no-auto-corners', action='store_true',
                    help='do not detect the screen corners; use the rig corners')
    ap.add_argument('--fresh-calibration', action='store_true',
                    help="forget calibration the scripts saved in this rig's sandbox")
    ap.add_argument('--scripts-dir', default=default_scripts_dir())
    ap.add_argument('--fake-hw', nargs='?', const='', metavar='IMAGE_OR_DIR',
                    help='no robot: fake controller + still-image camera (tests this tool only)')
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    source = resolve_script(a.script, a.scripts_dir)
    rel = os.path.relpath(source, a.scripts_dir)
    sandbox = prepare_sandbox(a.scripts_dir,
                              os.path.join(a.out, '_sandbox', (a.rig and os.path.splitext(
                                  os.path.basename(a.rig))[0]) or ('fake' if a.fake_hw is not None else 'default')),
                              a.fresh_calibration)
    script_path = os.path.join(sandbox, rel)
    spec_path = a.spec or find_spec(rel)
    spec = {}
    if spec_path:
        with open(spec_path, encoding='utf-8') as f:
            spec = yaml.safe_load(f) or {}
        spec['_path'] = os.path.relpath(spec_path, APP_DIR).replace('\\', '/')
    else:
        print('warning: no spec found - running with default limits and no cycle counting')
    rig = rigmod.load_rig(rigmod.default_rig_path(a.rig)) if a.rig else {'corners': {}, 'rects': {}}
    settings = {}
    for kv in a.set:
        k, _, v = kv.partition('=')
        settings[k.strip()] = yaml.safe_load(v)

    stem = os.path.splitext(os.path.basename(script_path))[0]
    out_dir = os.path.join(a.out, f'{dt.datetime.now():%Y%m%d-%H%M%S}_{stem}')
    os.makedirs(out_dir, exist_ok=True)

    if a.fake_hw is not None:
        controller, grabber = FakeController(), FakeGrabber(a.fake_hw or None)
    else:
        port = a.port or rig.get('port')
        cam = a.camera if a.camera is not None else rig.get('camera')
        if not port or cam is None:
            raise SystemExit('Need a serial port and camera: --rig <name>, or --port and --camera')
        grabber = FrameGrabber(int(cam))
        try:
            controller = GameProController(port)
        except Exception:
            grabber.release()
            raise

    run = HeadlessRun(script_path, spec, rig, out_dir, controller, grabber,
                      cycles=a.cycles, timeout_minutes=a.timeout_minutes,
                      calibrate=a.calibrate, settings=settings, scripts_dir=sandbox,
                      auto_corners=not a.no_auto_corners,
                      source_dir=a.scripts_dir)
    try:
        result = run.execute()
    finally:
        try:
            controller.close()
        finally:
            grabber.release()
    print(f"\n{result['status']}: {result['reason']}  "
          f"({result['cycles_completed']}/{result['cycles_target']} cycles, {result['duration_s']} s)")
    print(f'results: {os.path.join(out_dir, "result.json")}')
    return 0 if result['status'] in ('completed', 'target-found') else 1


if __name__ == '__main__':
    sys.exit(main())
