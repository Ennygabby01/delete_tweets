from pathlib import Path
from shutil import copyfile
from xml.etree import ElementTree as ET

from scripts.update_badges import SVG, update


def test_refresh_uses_metadata_without_importing_app_and_preserves_artwork(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text(
        '__version__ = "2.1.7"\nraise RuntimeError("must not import app")\n'
    )
    (tmp_path / "Makefile").write_text("MIN_PYTHON := 3.14\n")
    badges = tmp_path / "docs" / "badges"
    badges.mkdir(parents=True)
    for name in ("version", "python"):
        copyfile(root / "docs" / "badges" / f"{name}.svg", badges / f"{name}.svg")
    before = {path: path.read_bytes() for path in badges.iterdir()}
    assert not update(tmp_path, check=True)
    assert all(path.read_bytes() == original for path, original in before.items())
    assert not update(tmp_path)
    assert update(tmp_path, check=True)
    assert update(tmp_path)
    for name, value in (("version", "2.1.7"), ("python", "3.14+")):
        path = badges / f"{name}.svg"
        original = ET.fromstring(before[path])
        refreshed = ET.parse(path).getroot()
        assert refreshed.find(f'.//{{{SVG}}}text[@id="badge-value"]').text == value
        assert ET.tostring(original.find(f"{{{SVG}}}g")) == ET.tostring(
            refreshed.find(f"{{{SVG}}}g")
        )
