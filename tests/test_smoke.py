import wentian


def test_import():
    """v0.7 · T88 — version landed at 0.7.0."""
    assert wentian.__version__ == "0.7.0"


def test_version():
    """v0.7 · T88 — version landed at 0.7.0."""
    assert wentian.__version__ == "0.7.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
