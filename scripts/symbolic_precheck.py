#!/usr/bin/env python3
"""Compact, read-only SymPy prechecks for already-formalized mathematics.

This tool intentionally has no database access.  It reduces model context by
returning only pass/fail/unknown and short deterministic evidence.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = PROJECT_ROOT / ".runtime" / "math"
REQUIREMENTS = PROJECT_ROOT / "requirements-math.txt"
ALLOWED_FUNCTIONS = {
    "Abs",
    "Rational",
    "cos",
    "exp",
    "factorial",
    "log",
    "sin",
    "sqrt",
    "tan",
}
ALLOWED_CONSTANTS = {"E", "pi"}
ALLOWED_AST_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Call,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
    ast.UAdd,
    ast.USub,
)


def _activate_runtime() -> bool:
    if RUNTIME.is_dir() and str(RUNTIME) not in sys.path:
        sys.path.insert(0, str(RUNTIME))
    return importlib.util.find_spec("sympy") is not None


def ensure_runtime(install: bool) -> dict[str, Any]:
    if not _activate_runtime() and install:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--target",
                str(RUNTIME),
                "-r",
                str(REQUIREMENTS),
            ],
            check=True,
        )
    available = _activate_runtime()
    result: dict[str, Any] = {"available": available, "library": "sympy"}
    if available:
        import sympy

        result["version"] = sympy.__version__
    return result


def _symbols(item: dict[str, Any]) -> dict[str, Any]:
    import sympy as sp

    names = set(item.get("variables") or [])
    if item.get("variable"):
        names.add(str(item["variable"]))
    return {name: sp.Symbol(name, real=True) for name in names}


def _sympify(value: Any, symbols: dict[str, Any]) -> Any:
    import sympy as sp

    text = str(value)
    tree = ast.parse(text, mode="eval")
    allowed_names = set(symbols) | ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_AST_NODES):
            raise ValueError(f"unsupported expression syntax: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in allowed_names:
            raise ValueError(f"unsupported expression name: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCTIONS:
                raise ValueError("unsupported function call")
    locals_map = {name: getattr(sp, name) for name in ALLOWED_FUNCTIONS | ALLOWED_CONSTANTS}
    return sp.sympify(text, locals={**locals_map, **symbols})


def check_one(item: dict[str, Any]) -> dict[str, Any]:
    import sympy as sp

    kind = str(item.get("kind") or "")
    symbols = _symbols(item)
    if kind == "identity":
        # ``trigsimp`` handles the trigonometric cases this helper was first
        # built for, but it intentionally does not always expand or cancel
        # ordinary polynomial/rational expressions.  Finish with the general
        # simplifier so exact algebraic identities do not become false
        # ``fail`` results that have to be sent back to a model.
        delta = sp.simplify(sp.trigsimp(
            _sympify(item["left"], symbols) - _sympify(item["right"], symbols)
        ))
        return {"status": "pass" if delta == 0 else "fail", "evidence": str(delta)}
    if kind == "equation":
        variable = item.get("variable") or (item.get("variables") or [None])[0]
        if not variable:
            raise ValueError("equation requires variable")
        symbol = symbols.setdefault(str(variable), sp.Symbol(str(variable), real=True))
        solutions = sp.solveset(
            _sympify(item["equation"], symbols), symbol, domain=sp.S.Reals
        )
        expected = item.get("expected")
        passed = expected is None or solutions == sp.FiniteSet(
            *[_sympify(value, symbols) for value in expected]
        )
        return {
            "status": "pass" if passed else "fail",
            "evidence": f"{variable}={solutions}",
        }
    if kind == "substitution":
        expression = _sympify(item["expression"], symbols)
        substitutions = {}
        for name, value in (item.get("values") or {}).items():
            symbol = symbols.setdefault(name, sp.Symbol(name, real=True))
            substitutions[symbol] = _sympify(value, symbols)
        actual = sp.simplify(expression.subs(substitutions))
        expected = item.get("expected")
        passed = expected is None or sp.simplify(
            actual - _sympify(expected, symbols)
        ) == 0
        return {
            "status": "pass" if passed else "fail",
            "evidence": str(actual),
        }
    raise ValueError(f"unsupported kind: {kind}")


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def run_packet(packet: dict[str, Any], full: bool = False) -> dict[str, Any]:
    checks = packet.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a non-empty list")
    results = []
    for index, item in enumerate(checks, 1):
        check_id = str(item.get("id") or index) if isinstance(item, dict) else str(index)
        try:
            result = check_one(item)
        except Exception as exc:
            result = {"status": "unknown", "evidence": _short(exc)}
        result["evidence"] = _short(result["evidence"])
        results.append({"id": check_id, **result})
    counts = {
        status: sum(result["status"] == status for result in results)
        for status in ("pass", "fail", "unknown")
    }
    return {
        "status": "pass" if counts["fail"] == counts["unknown"] == 0 else "attention",
        "checked": len(results),
        "counts": counts,
        "checks": results if full else [
            result for result in results if result["status"] != "pass"
        ],
        "database_modified": False,
        "human_or_model_review_required": counts["fail"] + counts["unknown"] > 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compact exact prechecks; input must already be formalized"
    )
    parser.add_argument("packet", type=Path)
    parser.add_argument(
        "--install",
        action="store_true",
        help="install the pinned SymPy dependency into .runtime/math if missing",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="include passing checks; default output contains only attention items",
    )
    parser.add_argument("--pretty-json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = ensure_runtime(args.install)
        if not runtime["available"]:
            raise RuntimeError(
                "SymPy unavailable; rerun once with --install"
            )
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        result = {"engine": runtime, **run_packet(packet, full=args.full)}
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2 if args.pretty_json else None,
                separators=None if args.pretty_json else (",", ":"),
            )
        )
        return 0 if result["status"] == "pass" else 2
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": str(exc),
                    "database_modified": False,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
