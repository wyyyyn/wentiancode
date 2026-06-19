import wentian


def test_import():
    """v0.9 · C59 · F68/F69（任务 T106）— version landed at 0.9.0."""
    assert wentian.__version__ == "0.9.0"


def test_version():
    """v0.9 · C59 · F68/F69（任务 T106）— version landed at 0.9.0."""
    assert wentian.__version__ == "0.9.0"


def test_ui_package():
    import wentian.ui  # noqa: F401


def test_prompt_toolkit_available():
    import prompt_toolkit  # noqa: F401
