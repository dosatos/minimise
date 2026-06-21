# Contributing to minimise

We welcome contributions! Please follow these guidelines:

## Getting Started

1. Fork and clone the repository
2. Create a virtual environment: `python -m venv venv`
3. Install development dependencies: `pip install -e ".[dev]"`
4. Run tests: `pytest tests/ -v`

## Development

- Write tests for new features
- Ensure all tests pass before submitting a PR
- Follow PEP 8 style guidelines
- Keep commits atomic and well-described

## Testing

```bash
pytest tests/ -v        # Run all tests
pytest tests/test_cli.py -v  # Run specific test file
```

All tests must pass before merging.

## Submitting Changes

1. Create a feature branch: `git checkout -b feature/your-feature`
2. Make your changes and commit with clear messages
3. Push to your fork and open a pull request
4. Describe your changes and reference any related issues

## Code of Conduct

Be respectful and constructive in all interactions.

## Questions?

Open an issue or check the README for more information.
