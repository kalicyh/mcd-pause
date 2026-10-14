"""Chat entrypoints use real input data without creating reminders or orders."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1] / '.agents/skills/mcd-pause'
sys.path.insert(0, str(SKILL / 'scripts'))
from mcpause import main
from state import State
from views import render_window


class WindowTests(unittest.TestCase):
    def test_preference_constraints_are_transient_and_rendering_is_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = State(root / 'state')
            before = state.profile()
            constraints = {'budget_cents': 4800, 'min_protein': 25}
            (root / 'targets.json').write_text(json.dumps(constraints))
            with patch('mcpause.Client', side_effect=AssertionError('Rendering must not connect')):
                main(['--state-dir', str(root / 'state'), 'render-window', '--view', 'preferences',
                      '--intent', '四件套', '--constraints', str(root / 'targets.json'), '--out', str(root / 'view.html')])
            html = (root / 'view.html').read_text()
            data = json.loads(re.search(r'id="mcpause-window-data">(.*?)</script>', html).group(1))
            self.assertEqual(data['constraints'], constraints)
            self.assertEqual(data['skill_path'], str(SKILL / 'SKILL.md'))
            self.assertEqual(state.profile(), before)
            self.assertEqual(state.keys('order:'), [])
            state.db.close()

    def test_live_game_rejects_demo_and_uses_supplied_menu(self):
        catalog = json.loads((SKILL / 'assets/demo.json').read_text())
        with self.assertRaises(ValueError):
            render_window('game', catalog=catalog)
        catalog['mode'] = 'live'  # Synthetic test fixture for the live renderer.
        html = render_window('game', catalog=catalog, profile={'constraints': {'budget_cents': 2500}})
        data = json.loads(re.search(r'id="mcpause-data">(.*?)</script>', html).group(1))
        self.assertEqual(data['view'], 'game')
        self.assertEqual(data['catalog']['foods'], catalog['foods'])
        self.assertNotIn('__MCPAUSE_', html)

    def test_order_projection_omits_private_account_fields(self):
        html = render_window('orders', orders=[{'order_id': 'SYNTHETIC', 'label': '</script><PRIVATE>',
                            'mobilePhone': 'PRIVATE_PHONE', 'address': 'PRIVATE_ADDRESS'}])
        self.assertNotIn('PRIVATE_PHONE', html)
        self.assertNotIn('PRIVATE_ADDRESS', html)
        self.assertNotIn('</script><PRIVATE>', html)
        with self.assertRaises(ValueError):
            render_window('preferences', profile={'constraints': {'budget_cents': -100}})

    def test_ready_can_be_followed_through_delivery_and_celebrated_once(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(directory)
            self.addCleanup(state.db.close)
            state.configure({'celebrate_enabled': True})
            ready = state.observe('SYNTHETIC', {'orderStatusName': '配餐完成', 'orderType': 2})
            self.assertEqual(ready['order']['status'], 'ready')
            self.assertFalse(ready['order']['terminal'])
            self.assertFalse(ready['celebration_eligible'])
            delivered = state.observe('SYNTHETIC', {'orderStatus': '6', 'orderStatusName': '订单已完成', 'orderType': 2})
            self.assertTrue(delivered['celebration_eligible'])
            self.assertTrue(delivered['order']['terminal'])
            self.assertTrue(state.claim_celebration('SYNTHETIC')['fire'])
            self.assertFalse(state.claim_celebration('SYNTHETIC')['fire'])


if __name__ == '__main__':
    unittest.main()
