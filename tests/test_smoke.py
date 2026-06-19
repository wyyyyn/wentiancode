import wentian


def test_import():
    """v0.8 · C52 · F61/F62（任务 T96）— version landed at 0.8.0."""
    assert wentian.__version__ == "0.8.0"


def test_version():
    """v0.8 · C52 · F61/F62（任务 T96）— version landed at 0.8.0."""
    assert wentian.__version__ == "0.8.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
