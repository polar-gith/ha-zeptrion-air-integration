# Contributing

Bug reports, ideas and pull requests are welcome.

## Issues

Use the [issue forms](../../issues/new/choose). For bugs they ask for the integration version, the zApp model and firmware, the button face and a diagnostics download; that is usually what it takes to reproduce a problem. One problem per issue.

## Pull requests

1. Fork the repository and branch from `main`.
2. Keep the change focused; update the README and `CHANGELOG.md` (section of the next version) if behaviour changes.
3. Run the checks below; CI runs the same on every pull request.
4. Open the pull request against `main`.

## Checks

```bash
pip install -r requirements_test.txt
ruff check .                              # lint
python -m pytest -q                       # integration tests against a simulated zApp
node --test tests/js/*.test.mjs           # dashboard parts (Node.js 22, no packages needed)
```

The HACS action and hassfest run in the *Validate* workflow without exceptions.

## Releases

- Versions follow Home Assistant: `YEAR.MONTH.PATCH`; the first release of a month is `YYYY.M.0`.
- The version in `custom_components/zeptrion_air/manifest.json` is the release version; the *Publish release* workflow creates tag and release from it.
- Every release has notes and an entry in `CHANGELOG.md`.

## Licence

Contributions are under the [MIT licence](LICENSE) of this project.
