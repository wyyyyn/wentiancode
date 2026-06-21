import wentian


def test_import():
    """v0.10 · C92（任务 T115）— version landed at 0.10.0."""
    assert wentian.__version__ == "0.10.0"


def test_version():
    """v0.10 · C92（任务 T115）— version landed at 0.10.0."""
    assert wentian.__version__ == "0.10.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
