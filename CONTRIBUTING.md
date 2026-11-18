# Contributing

Thanks for helping to improve this integration! Bug reports, player test
results and pull requests are all welcome.

## Reporting bugs and requesting features

- Open an [issue](https://github.com/Videobarista/brightsign-ha/issues)
  with your player model, BrightSignOS version and integration version.
- Add the integration's diagnostics (**Settings → Devices & services →
  BrightSign → ⋮ → Download diagnostics**) and the relevant log lines.
  Diagnostics redact the password, host, serial number, MAC address and
  player names; please check logs for personal data before posting them.
- Security problems are **not** reported in public issues: see
  [SECURITY.md](SECURITY.md).

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Make your change, with tests.
3. Run the checks locally (see below) and open a pull request against `main`.
4. All CI checks must pass: Ruff, Hassfest, HACS validation, Tests, CodeQL and
   Local only.

### Requirements

- **Tests**: new functionality comes with tests in `tests/`, and every bug fix
  comes with a test that fails without the fix. The config flow stays fully
  covered, in line with the Home Assistant quality scale (Bronze).
- **Style**: code passes `ruff check` and `ruff format` with the settings in
  `ruff.toml` (including the security rules).
- **Language**: code, comments, documentation and commit messages in English.
- **No new dependencies** unless there is no reasonable alternative.
- **Only documented interfaces**: the DWS REST API, the presentation's Local
  Web Server XML API, UDP commands and passive sync listening. No scraping of
  web interface pages.
- **Local only**: the integration talks to the configured players only.
  Network code stays in `api.py` (the receive-only sync listener in `sync.py`),
  there are no hard-coded addresses, and `python3 scripts/check_local_only.py`
  must pass.
- **Untrusted input**: treat everything a player sends as untrusted, never log
  credentials, and keep personal data out of test fixtures.

## Running the checks locally

```bash
pip install -r requirements_test.txt -r requirements_lint.txt
ruff check .
ruff format --check .
python3 scripts/check_local_only.py
pytest
```

Coverage guided fuzzing of the parsers with Atheris:

```bash
pip install atheris
python tests/fuzz/fuzz_parsers.py -max_total_time=300
```
