import wentian


def test_import():
    """v0.11 · C107a（任务 T134a）— version landed at 0.11.0."""
    assert wentian.__version__ == "0.11.0"


def test_version():
    """v0.11 · C107a（任务 T134a）— version landed at 0.11.0."""
    assert wentian.__version__ == "0.11.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
