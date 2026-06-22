import wentian


def test_import():
    """v0.13（2026-06-22 用户拍板）— version bumped 0.11.0 → 0.13.0."""
    assert wentian.__version__ == "0.13.0"


def test_version():
    """v0.13（2026-06-22 用户拍板）— version bumped 0.11.0 → 0.13.0."""
    assert wentian.__version__ == "0.13.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
