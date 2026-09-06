"""Kontrollera att FastAPI-routerna dekorerar rätt funktion.

Bakgrund: en hjälpfunktion lades in mellan `@app.get(...)` och den funktion
dekoratorn var tänkt för. Dekoratorn hamnade då på hjälpfunktionen, och
`/api/v1/player/{name}` började svara `HTTP 422` — FastAPI krävde
hjälpfunktionens parametrar som query-parametrar.

Felet syns inte i någon syntaxkontroll och inte i en typkontroll. Det syns
bara i drift, som en trasig endpoint. Den här kontrollen läser koden med ast
och kräver att varje route-funktions parametrar antingen står i sökvägen
eller har ett standardvärde.

    python3 tests/routes_check.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "api" / "main.py"
ROUTE = re.compile(r"^app\.(get|post|put|delete)$")


def route_path(decorator: ast.expr) -> str | None:
    """Sökvägen i `@app.get("/api/v1/...")`, annars None."""
    if not isinstance(decorator, ast.Call):
        return None
    if not ROUTE.match(ast.unparse(decorator.func)):
        return None
    if decorator.args and isinstance(decorator.args[0], ast.Constant):
        return str(decorator.args[0].value)
    return None


def main() -> int:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    problems: list[str] = []
    routes = 0

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        paths = [p for p in (route_path(d) for d in node.decorator_list) if p]
        if not paths:
            continue
        routes += 1
        path = paths[0]
        in_path = set(re.findall(r"\{(\w+)\}", path))

        args = node.args.args
        defaults = node.args.defaults
        # Parametrar utan standardvärde är de första i listan.
        without_default = args[: len(args) - len(defaults)] if defaults else args

        for arg in without_default:
            if arg.arg in in_path:
                continue
            problems.append(
                f"{path} -> {node.name}(): parametern '{arg.arg}' saknar både "
                f"standardvärde och plats i sökvägen. FastAPI kräver den som "
                f"query-parameter, och endpointen svarar HTTP 422."
            )

        # En route som pekar på en privat hjälpfunktion är nästan alltid en
        # dekorator som hamnat fel vid en infogning.
        if node.name.startswith("_"):
            problems.append(
                f"{path} -> {node.name}(): en route ska inte dekorera en privat "
                f"hjälpfunktion. Dekoratorn har troligen hamnat fel."
            )

    print(f"{routes} routes kontrollerade")
    for line in problems:
        print(f"  FEL  {line}")
    if problems:
        print(f"\n{len(problems)} problem.")
        return 1
    print("Alla routes dekorerar en funktion vars parametrar går att uppfylla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
