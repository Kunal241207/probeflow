"""Pytest configuration and shared fixtures for probeflow."""

def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Regenerate golden files from current parser output.",
    )
