"""
Tests for the spec checker and checkpoint drafting (tools/testing/spec_check.py).

    python -m unittest discover -s tests -v
"""

import os
import re
import sys
import tempfile
import textwrap
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(APP_DIR, 'tools', 'testing'))

import spec_check as sc   # noqa: E402

SCRIPT = textwrap.dedent('''
    class P:
        def run(self, controller, frame_grabber, stop_event, log, request_calibration):
            log("Wild hunt started.")
            n = 0
            while True:
                n += 1
                log(f"Encounter #{n} detected!")
                frame = frame_grabber.get_latest_frame()
                log(f"Encounter #{n}: R:{1:.1f} G:{2:.1f} B:{3:.1f}  (baseline R:{4} G:{5} B:{6})")
                if n > 99:
                    log(f"*** SHINY DETECTED! R:{1} G:{2} B:{3} ***")
                    log("Script paused - catch your shiny! Press Stop when done.")
                    return
                log(f"Encounter #{n}: not shiny - fleeing")

        def calibrate(self, log):
            log(f"Baseline - R:{1}  G:{2}  B:{3}")
''')


def spec(**kw):
    s = {'script': 'Beta/x/probe.py', 'name': 'Probe', 'game': 'g', 'console': 'Switch', 'draft': True,
         'kind': 'wild encounters', 'start_state': 'in the grass', 'cycles': 5, 'timeout_minutes': 30,
         'success': 'clean cycles',
         'checkpoints': [{'name': 'overview', 'when': {'every_minutes': 10}, 'expect': 'game running'}]}
    s.update(kw)
    return s


class SpecCheckTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, 'Beta', 'x'))
        with open(os.path.join(self.tmp.name, 'Beta', 'x', 'probe.py'), 'w', encoding='utf-8') as f:
            f.write(SCRIPT)
        self.texts = sc.log_texts(SCRIPT)

    def errors(self, s):
        return [t for lv, t in sc.check(s, self.tmp.name) if lv == 'error']

    def test_log_texts_fill_fields(self):
        self.assertIn('Encounter #{} detected!', [t for _, t in self.texts])

    def test_regex_for_keeps_spaces(self):
        rx = sc.regex_for('Encounter #{} detected!')
        self.assertRegex('Encounter #12 detected!', rx)
        self.assertNotRegex('Encounter #12: not shiny', rx)

    def test_double_counting_cycle_log_is_an_error(self):
        errs = self.errors(spec(cycle_log=r'Encounter\s*\#(\d+)'))
        self.assertTrue(any('more than one log line' in e for e in errs), errs)

    def test_one_cycle_log_picks_the_not_shiny_line(self):
        rx = sc.one_cycle_log(self.texts, r'Encounter\s*\#(\d+)')
        self.assertRegex('Encounter #3: not shiny - fleeing', rx)
        self.assertEqual(len(sc.lines_matching(rx, self.texts)), 1)
        self.assertEqual(self.errors(spec(cycle_log=rx)), [])

    def test_cycle_log_that_matches_nothing(self):
        errs = self.errors(spec(cycle_log='Soft reset'))
        self.assertTrue(any('matches none' in e for e in errs), errs)

    def test_target_pattern_must_catch_the_find(self):
        errs = self.errors(spec(target_log='DETECTED!!!'))
        self.assertTrue(any('a find would not stop the run' in e for e in errs), errs)
        self.assertFalse(any('a find would not stop' in e for e in self.errors(spec())))

    def test_checked_screen_is_the_colour_line(self):
        rx, _ = sc.checked_screen_log(self.texts)
        self.assertRegex('Encounter #4: R:1.0 G:2.0 B:3.0  (baseline R:4 G:5 B:6)', rx)
        self.assertNotRegex('Baseline - R:1  G:2  B:3', rx)

    def test_draft_checkpoints(self):
        cps = sc.draft_checkpoints({'kind': 'wild encounters'}, SCRIPT)
        self.assertEqual([c['name'] for c in cps], ['checked screen', 'overview'])
        self.assertEqual(cps[0]['per_cycle'], 1)
        farm = sc.draft_checkpoints({'kind': 'item / BP farm', 'cycle_log': 'Encounter #\\S+: not'}, SCRIPT)
        self.assertEqual(farm[0]['name'], 'cycle end')

    def test_checkpoints_yaml_round_trip(self):
        import yaml
        cps = sc.draft_checkpoints({'kind': 'wild encounters'}, SCRIPT)
        back = yaml.safe_load(sc.checkpoints_yaml(cps))['checkpoints']
        self.assertEqual(back[0]['when']['log'], cps[0]['when']['log'])
        self.assertTrue(re.search(back[0]['when']['log'], 'Encounter #7: R:1 G:2 B:3'))

    def test_confirmed_spec_needs_proof_and_no_todo(self):
        s = spec(draft=False, cycle_log=r'Encounter \#\S+: not shiny',
                 checkpoints=[{'name': 'c', 'when': 'TODO: log line or minutes', 'expect': 'x'}])
        errs = self.errors(s)
        self.assertTrue(any('detection_proof' in e for e in errs), errs)
        self.assertTrue(any('TODO' in e for e in errs), errs)
        self.assertTrue(any('`when` must be' in e for e in errs), errs)

    def test_missing_fields_and_script(self):
        errs = self.errors({'script': 'Beta/x/nope.py', 'draft': True})
        self.assertTrue(any('script not found' in e for e in errs))
        self.assertTrue(any('missing `start_state`' in e for e in errs))

    def test_replace_block(self):
        text = 'a: 1\ncheckpoints:\n  - name: x\n    when: y\nsuccess: z\n'
        self.assertEqual(sc.replace_block(text, 'checkpoints', 'checkpoints: []\n'),
                         'a: 1\ncheckpoints: []\nsuccess: z\n')

    def test_repo_specs_are_valid(self):
        scripts = os.path.join(os.path.dirname(APP_DIR), 'gamepro-scripts')
        if not os.path.isdir(scripts):
            self.skipTest('gamepro-scripts checkout not next to the app')
        bad = {}
        for p in sc.all_specs():
            errs = [t for lv, t in sc.check(sc.load_spec(p), scripts, p) if lv == 'error']
            if errs:
                bad[os.path.relpath(p, APP_DIR)] = errs
        self.assertEqual(bad, {})


if __name__ == '__main__':
    unittest.main()
