# Contributing to CortexCloud API

Thank you for interest in contributing to CortexCloud API! As an open-source enterprise AI gateway, we value your contributions.

## How to Contribute

### 1. Reporting Bugs & Requesting Features
*   Please check existing Issues before opening a new one.
*   Clearly describe the bug or feature request, providing reproduction steps or code blocks where applicable.

### 2. Submitting Pull Requests
1.  Fork the repository and create your branch from `main`.
2.  Install dependencies: `.venv/bin/pip install -r requirements.txt`.
3.  Implement your changes, keeping coding styles clean and adhering to SOLID and Clean Architecture principles.
4.  Write comprehensive tests in `tests/` for any new logic.
5.  Run the test suite to ensure no regressions:
    ```bash
    .venv/bin/python -m pytest
    ```
6.  Commit your changes using Conventional Commit messages:
    *   `feat: add new provider`
    *   `fix: resolve balance race condition`
    *   `docs: update API usage examples`
7.  Submit a Pull Request and detail your changes and verification tests.

## Code Style & Formatting
*   We use standard Python style guides.
*   Ensure code is formatted before committing.
