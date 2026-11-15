import bookcore


def test_the_extension_imports_and_reports_its_version() -> None:
    assert bookcore.version() == "0.1.0"
