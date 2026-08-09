import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "symbolic_precheck.py"
SPEC = importlib.util.spec_from_file_location("symbolic_precheck", SCRIPT)
symbolic_precheck = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = symbolic_precheck
SPEC.loader.exec_module(symbolic_precheck)


@unittest.skipUnless(
    symbolic_precheck.ensure_runtime(False)["available"],
    "isolated SymPy runtime is not installed",
)
class SymbolicPrecheckTests(unittest.TestCase):
    def test_compact_pass_packet(self):
        result = symbolic_precheck.run_packet(
            {
                "checks": [
                    {
                        "id": "eq",
                        "kind": "equation",
                        "equation": "x**2-4",
                        "variable": "x",
                        "expected": ["-2", "2"],
                    },
                    {
                        "id": "identity",
                        "kind": "identity",
                        "variables": ["x"],
                        "left": "sin(x)**2+cos(x)**2",
                        "right": "1",
                    },
                ]
            }
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["counts"], {"pass": 2, "fail": 0, "unknown": 0})
        self.assertEqual(result["checks"], [])
        self.assertFalse(result["database_modified"])

    def test_failure_requires_review(self):
        result = symbolic_precheck.run_packet(
            {
                "checks": [
                    {
                        "kind": "substitution",
                        "variables": ["x"],
                        "expression": "x**2",
                        "values": {"x": "3"},
                        "expected": "8",
                    }
                ]
            }
        )
        self.assertEqual(result["status"], "attention")
        self.assertTrue(result["human_or_model_review_required"])
        self.assertEqual(len(result["checks"]), 1)

    def test_algebraic_identity_expands_and_cancels(self):
        result = symbolic_precheck.run_packet(
            {
                "checks": [
                    {
                        "id": "polynomial",
                        "kind": "identity",
                        "variables": ["x"],
                        "left": "(1-2*x)**3",
                        "right": "1-6*x+12*x**2-8*x**3",
                    },
                    {
                        "id": "rational",
                        "kind": "identity",
                        "variables": ["k"],
                        "left": "((-16*k**2-8*k)/(4*k**2+3)+4) / "
                        "(2*(-16*k**2-8*k)/(4*k**2+3)+4+"
                        "(16*k**2+16*k-8)/(4*k**2+3))",
                        "right": "3-2*k",
                    },
                ]
            }
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["counts"], {"pass": 2, "fail": 0, "unknown": 0})
        self.assertEqual(result["checks"], [])

    def test_unsafe_or_unrecognized_expression_is_unknown(self):
        result = symbolic_precheck.run_packet(
            {
                "checks": [
                    {
                        "kind": "identity",
                        "left": "__import__('os').getcwd()",
                        "right": "0",
                    }
                ]
            }
        )
        self.assertEqual(result["counts"]["unknown"], 1)
        self.assertEqual(result["checks"][0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
