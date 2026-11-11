import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    'configure_alerts', Path(__file__).resolve().parents[1] / 'scripts/configure-alerts.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AlertPlanTests(unittest.TestCase):
    def resource(self, kind, actions):
        return {'mode': 'managed', 'type': kind, 'address': f'{kind}.test',
                'change': {'actions': actions}}

    def test_allows_alert_creates_and_updates(self):
        resources = [self.resource(kind, ['create']) for kind in MODULE.TARGETS]
        resources.append(self.resource(MODULE.TARGETS[0], ['update']))
        self.assertEqual(len(MODULE.checked_changes({'resource_changes': resources})), 4)

    def test_rejects_infrastructure_and_receiver_changes(self):
        for kind in ('azurerm_kubernetes_cluster', 'azurerm_monitor_action_group'):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                MODULE.checked_changes({'resource_changes': [self.resource(kind, ['update'])]})

    def test_rejects_deletion_and_replacement(self):
        for actions in (['delete'], ['delete', 'create'], ['create', 'delete']):
            with self.subTest(actions=actions), self.assertRaises(RuntimeError):
                MODULE.checked_changes({'resource_changes': [self.resource(MODULE.TARGETS[0], actions)]})

    def test_ignores_unchanged_dependencies(self):
        resources = [self.resource('azurerm_monitor_action_group', ['no-op'])]
        self.assertEqual(MODULE.checked_changes({'resource_changes': resources}), [])


if __name__ == '__main__':
    unittest.main()