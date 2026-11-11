"""
Tests for the headless test mode (tools/testing/gamepro_test.py), using fake hardware.

    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import textwrap
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, 'tools', 'testing'))
sys.path.insert(0, APP_DIR)

import gamepro_test as gt   # noqa: E402

HEADER = 'from scripts.base_script import BaseScript\nimport time\n\n'


class HeadlessRunTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_script(self, body, spec=None, rig=None, grabber=None, **kw):
        path = os.path.join(self.tmp.name, 'probe.py')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(HEADER + textwrap.dedent(body))
        out = os.path.join(self.tmp.name, 'out')
        self.ctrl = gt.FakeController()
        grabber = grabber or gt.FakeGrabber()
        self.addCleanup(grabber.release)
        spec = dict({'camera_lost_seconds': 2, 'max_static_seconds': 60}, **(spec or {}))
        run = gt.HeadlessRun(path, spec, rig, out, self.ctrl, grabber,
                             scripts_dir=self.tmp.name, echo=False, **kw)
        result = run.execute()
        self.assertEqual(self.ctrl.sent[-1], '0', 'release_all must be the last command')
        self.assertTrue(os.path.isfile(os.path.join(out, 'result.json')))
        return result

    def test_stops_after_cycles(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    n = 0
                    while not stop_event.is_set():
                        n += 1
                        controller.press_a()
                        log(f"Soft reset #{n}")
                        self.wait(0.1, stop_event)
            ''', spec={'cycle_log': r'Soft reset #(\d+)'}, cycles=3)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['cycles_completed'], 3)
        self.assertEqual(len(r['cycle_durations_s']), 2)
        self.assertIn('final', [f['name'] for f in r['frames']])

    def test_hard_timeout(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    while not stop_event.is_set():
                        controller.press_b()
                        self.wait(0.1, stop_event)
            ''', timeout_minutes=0.03)
        self.assertEqual(r['status'], 'timeout')

    def test_camera_lost(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    while not stop_event.is_set():
                        self.wait(0.1, stop_event)
            ''', grabber=gt.FakeGrabber(frozen=True), timeout_minutes=1)
        self.assertEqual(r['status'], 'camera-lost')

    def test_picture_unchanged(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    while not stop_event.is_set():
                        self.wait(0.1, stop_event)
            ''', spec={'max_static_seconds': 1.5}, timeout_minutes=1)
        self.assertEqual(r['status'], 'camera-frozen')

    def test_script_crash(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    controller.press_a()
                    raise ValueError("boom")
            ''')
        self.assertEqual(r['status'], 'error')
        self.assertIn('ValueError: boom', r['errors'][0])
        self.assertIn('error', [f['name'] for f in r['frames']])

    def test_target_found_stops_parked_script(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    log("*** SHINY EEVEE! R:1 G:2 B:3 ***")
                    log("Script paused - catch your shiny! Press Stop when done.")
                    stop_event.wait()
            ''', timeout_minutes=1)
        self.assertEqual(r['status'], 'target-found')
        names = [f['name'] for f in r['frames']]
        self.assertIn('target', names)
        self.assertIn('target_settled', names)

    def test_script_ignoring_stop_still_releases(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    t = time.time()
                    while time.time() - t < 4:
                        time.sleep(0.05)
            ''', spec={'stop_grace_seconds': 1}, timeout_minutes=0.02)
        self.assertEqual(r['status'], 'timeout')
        self.assertTrue(any('did not return' in e for e in r['errors']))

    def test_calibration_from_rig(self):
        rig = {'name': 't', 'corners': {'default': [[10, 10], [600, 12], [598, 470], [12, 468]]},
               'rects': {'probe.1': [100, 120, 30, 40]}}
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    w = request_calibration("Click the 4 corners", mode="corners")
                    rect = request_calibration("Draw region over the sprite")
                    log(f"warp {w['out_w']}x{w['out_h']} rect {rect}")
                    log("Soft reset #1")
            ''', spec={'cycle_log': 'Soft reset'}, rig=rig, cycles=1)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual([c['rig_key'] for c in r['calibration']], ['corners.default', 'rects.probe.1'])
        with open(os.path.join(self.tmp.name, 'out', 'log.txt'), encoding='utf-8') as f:
            self.assertIn('rect (100, 120, 30, 40)', f.read())

    def test_missing_calibration_stops(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    rect = request_calibration("Draw region over the sprite")
                    while not stop_event.is_set():
                        self.wait(0.1, stop_event)
            ''')
        self.assertEqual(r['status'], 'needs-calibration')
        self.assertIn('rects.probe.1', r['reason'])

    def test_settings_and_directions(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                SETTINGS = [{'key': 'mode', 'type': 'choice', 'choices': ['a', 'b'], 'default': 'a'},
                            {'key': 'n', 'type': 'int', 'default': 1}]
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    d = controller.wait_for_direction(stop_event, timeout=5)
                    log(f"cycle mode={self.settings['mode']} n={self.settings['n']} dir={d}")
            ''', spec={'cycle_log': 'cycle', 'settings': {'mode': 'b'}, 'directions': ['left']},
            cycles=1)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['settings'], {'mode': 'b', 'n': 1})
        with open(os.path.join(self.tmp.name, 'out', 'log.txt'), encoding='utf-8') as f:
            self.assertIn('mode=b n=1 dir=left', f.read())

    def test_checkpoints(self):
        r = self.run_script('''
            class P(BaseScript):
                NAME = "probe"
                def run(self, controller, frame_grabber, stop_event, log, request_calibration):
                    for n in range(1, 4):
                        log("Encounter started")
                        self.wait(0.3, stop_event)
                        log(f"Soft reset #{n}")
            ''', spec={'cycle_log': 'Soft reset', 'checkpoints': [
                {'name': 'encounter', 'when': {'log': 'Encounter', 'delay_seconds': 0.1}},
                {'name': 'bad', 'when': 'TODO: log line or minutes'}]}, cycles=3)
        self.assertEqual([f['name'] for f in r['frames']].count('encounter'), 3)
        self.assertEqual(len(r['warnings']), 1)


if __name__ == '__main__':
    unittest.main()
