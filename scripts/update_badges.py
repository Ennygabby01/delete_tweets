"""Refresh badge values from project metadata without changing their artwork."""

import argparse
import ast
import re
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


def versions(root: Path) -> dict[str, str]:
    module = ast.parse((root / "app" / "__init__.py").read_text())
    version = next(
        (
            ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__version__"
                for target in node.targets
            )
        ),
        None,
    )
    if not isinstance(version, str) or not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?", version
    ):
        raise ValueError("app/__init__.py must define a literal release version")
    minimum = re.search(
        r"^MIN_PYTHON\s*:=\s*([0-9]+\.[0-9]+)\s*$", (root / "Makefile").read_text(), re.M
    )
    if not minimum:
        raise ValueError("Makefile must define MIN_PYTHON")
    return {"version": version, "python": minimum[1] + "+"}


def update(root: Path, *, check: bool = False) -> bool:
    fresh = True
    for name, value in versions(root).items():
        path = root / "docs" / "badges" / f"{name}.svg"
        original = path.read_text()
        badge = ET.fromstring(original)
        title = badge.find(f"{{{SVG}}}title")
        text = badge.find(f'.//{{{SVG}}}text[@id="badge-value"]')
        if title is None or text is None:
            raise ValueError(f"Missing title/value in {path.name}")
        title.text = ("Version " if name == "version" else "Python ") + value
        text.text = value
        ET.indent(badge, space="  ")
        rendered = ET.tostring(badge, encoding="unicode") + "\n"
        if original != rendered:
            fresh = False
            if check:
                print(f"Stale badge: {path.name}. Run make badges.")
            else:
                path.write_text(rendered)
                print(f"Updated {path.name}")
    return fresh


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    fresh = update(ROOT, check=args.check)
    if args.check and not fresh:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
