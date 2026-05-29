"""Smoke test: package import + version sanity."""
import karaoke


def test_package_imports() -> None:
    assert hasattr(karaoke, "__version__")


def test_version_string() -> None:
    assert isinstance(karaoke.__version__, str)
    assert karaoke.__version__.count(".") == 2


def test_main_returns_zero(capsys) -> None:
    assert karaoke.main() == 0
    captured = capsys.readouterr()
    assert "karaoke" in captured.out
