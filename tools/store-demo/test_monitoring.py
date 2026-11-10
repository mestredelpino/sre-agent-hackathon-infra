import unittest
from unittest.mock import patch

from configure_monitoring import build_dashboard, verify_queries


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.dashboard = build_dashboard("test-prometheus", "/subscriptions/test/providers/Microsoft.OperationalInsights/workspaces/test")

    def test_dashboard_scopes_metrics_and_legacy_logs(self):
        self.assertEqual(len(self.dashboard["panels"]), 6)
        for panel in self.dashboard["panels"][:4]:
            self.assertIn('namespace="aks-store-demo"', panel["targets"][0]["expr"])
            self.assertEqual(panel["datasource"]["uid"], "test-prometheus")
        for panel in self.dashboard["panels"][4:6]:
            query = panel["targets"][0]["azureLogAnalytics"]["query"]
            self.assertIn("ContainerLog |", query)
            self.assertIn("aks-store-demo", query)
            self.assertEqual(panel["targets"][0]["queryType"], "Azure Log Analytics")

    def test_graph_has_live_nodes_and_dependency_edges(self):
        dashboard = build_dashboard("test-prometheus", "/subscriptions/test/providers/Microsoft.OperationalInsights/workspaces/test", True)
        graph = dashboard["panels"][-1]
        self.assertEqual(graph["type"], "grafana-graphviz-panel")
        query = graph["targets"][0]["azureLogAnalytics"]["query"]
        self.assertIn("documentdb", query)
        self.assertIn("public-entry", query)
        self.assertIn("Public HTTP: not monitored", query)
        self.assertIn("not observed traffic", graph["description"])
        self.assertEqual(graph["gridPos"]["y"], 0)
        self.assertTrue(all(panel["gridPos"]["y"] >= graph["gridPos"]["h"] for panel in dashboard["panels"][:6]))

    def test_optional_diagrams_bind_live_data(self):
        dashboard = build_dashboard("test-prometheus", "/subscriptions/test/providers/Microsoft.OperationalInsights/workspaces/test", True)
        graphviz = dashboard["panels"][-1]
        self.assertEqual(len(dashboard["panels"]), 7)
        self.assertFalse(any(panel["type"] in {"nodeGraph", "gapit-htmlgraphics-panel"} for panel in dashboard["panels"]))
        self.assertEqual(graphviz["options"]["inputMode"], "query")
        self.assertIn("ago(5m)", graphviz["targets"][0]["azureLogAnalytics"]["query"])
        self.assertIn("leftouter Health", graphviz["targets"][0]["azureLogAnalytics"]["query"])

    @patch("configure_monitoring.grafana_request", return_value={"results": {"A": {"error": "denied"}}})
    def test_denied_query_fails(self, request):
        with self.assertRaisesRegex(RuntimeError, "query failed"):
            verify_queries("https://test.grafana.azure.com", self.dashboard)

    @patch("configure_monitoring.grafana_request", return_value={"results": {"A": {"frames": []}}})
    def test_empty_query_fails(self, request):
        with self.assertRaisesRegex(RuntimeError, "No live data"):
            verify_queries("https://test.grafana.azure.com", self.dashboard)


if __name__ == "__main__":
    unittest.main()