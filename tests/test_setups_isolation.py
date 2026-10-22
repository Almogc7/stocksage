"""PR 0c: the new setups/ stack must never import or share Stack A's
(agent/core.py) internals -- its scheduler loop, in-memory session dedup,
or DB cooldown table (docs/SETUP_SPEC.md §14/§15). Two complementary
checks: a static AST import scan, and a real import in a clean
subprocess interpreter (catching any transitive import the static scan
alone could miss).
"""
import ast
import subprocess
import sys
import unittest
from pathlib import Path

_SETUPS_DIR = Path(__file__).resolve().parent.parent / "setups"

_FORBIDDEN_NAMES = {"agent", "agent.core", "_alerted_this_session", "was_alerted_today"}


def _imported_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            for alias in node.names:
                names.add(f"{node.module}.{alias.name}")
    return names


class TestSetupsIsolationStatic(unittest.TestCase):

    def test_no_file_imports_agent_core_or_its_internals(self):
        py_files = sorted(_SETUPS_DIR.glob("*.py"))
        self.assertTrue(py_files, "setups/ package appears empty")
        for path in py_files:
            with self.subTest(file=path.name):
                source = path.read_text(encoding="utf-8")
                imported = _imported_names(source)
                offending = imported & _FORBIDDEN_NAMES
                self.assertFalse(
                    offending,
                    f"{path.name} imports forbidden name(s): {offending}",
                )
                # Also guard direct attribute references to the dedup/
                # cooldown state, in case it's accessed without a literal
                # "import agent" line (e.g. via a re-export elsewhere).
                for forbidden in ("_alerted_this_session", "was_alerted_today"):
                    self.assertNotIn(
                        forbidden, source,
                        f"{path.name} references Stack A internal '{forbidden}'",
                    )


class TestSetupsIsolationRuntime(unittest.TestCase):

    def test_importing_setups_evaluate_never_pulls_in_agent_core(self):
        result = subprocess.run(
            [sys.executable, "-c",
             "import setups.evaluate, sys; "
             "assert 'agent.core' not in sys.modules, sys.modules.keys()"],
            capture_output=True, text=True, cwd=str(_SETUPS_DIR.parent),
        )
        self.assertEqual(
            result.returncode, 0,
            f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
