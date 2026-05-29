# Contributing to keel-trade

Thanks for the interest. Quick read so we don't waste each other's time.

## What this repository is

This repo is the public mirror of the `keel-trade` Python package
that ships to PyPI. It contains the CLI, the stdio MCP server, and
the bundled component registry / agent skills / reference docs.

The repo is **maintained in a private monorepo** and synced here on
every release tag. That has two practical implications for
contributors:

1. **PRs are welcome but may take longer to land** than typical OSS.
   We will read every PR and either port it back to the monorepo
   (your patch ships in the next release with attribution) or close
   it with a reason. We aim for one week to first response; sometimes
   it slips.
2. **Sync overwrites this repo on every release.** Do not rely on
   commits being immutable here; the canonical history lives upstream.
   This is fine for typical PR flow but matters if you're building on
   top: pin to a tagged release, not a branch HEAD.

## What we want PRs for

- Typo fixes, doc improvements, README clarity — fastest path
- Bug fixes with a reproducing test — strongly preferred
- Missing edge cases in the typed component schemas
- Agent skill additions or improvements (under `keel/skills/`)
- Bundled example strategies for the agent-reference docs

## What we want issues for

- **Bug reports** — use the bug template. Include `keel doctor`
  output, Python version, MCP host (Claude Code / Cursor / Codex /
  Windsurf / other), and the exact error message
- **Feature requests** — use the feature template
- **Security issues** — please DO NOT open a public issue; email
  `team@usekeel.io` directly

## What we want Discussions for

- **Q&A** ("how do I do X?")
- **Show & tell** — strategies you've built, prompt patterns that
  worked, agent workflows worth sharing
- **Ideas** — bigger directional questions before they become RFCs

## Things to never paste

In any issue, PR, comment, or discussion:

- **Wallet private keys, seed phrases, or API keys** of any kind
- **Strategy source you want to keep private** — Keel is non-custodial
  and we don't see your strategies; don't accidentally share them in
  a public forum
- **Personally identifying information** beyond what you'd put on a
  public GitHub profile
- **Full `.env` files** — redact secrets before pasting

If you accidentally post a credential, rotate it immediately and then
ask us to delete the comment via Discussions or `team@usekeel.io`.

## Development setup (if you want to run a PR locally)

```bash
# clone
git clone https://github.com/keel-trade/keel-trade.git
cd keel-trade

# install in editable mode with dev deps
pip install -e ".[dev]"

# run tests
pytest tests/ -v --ignore=tests/test_tools_remote.py
```

Most tests run offline against mocks (`respx`). The `test_tools_remote`
suite hits a live API and is skipped by default.

## Code style

- Python 3.11+. Type hints required on new code (we use Pydantic 2
  models throughout).
- No `from x import *`. Explicit imports keep IDE jump-to-def working.
- Tests use `pytest`. Add a regression test for any bug fix.
- Docstrings on public functions; short comments only when the WHY
  isn't obvious from the code.

## License

By contributing, you agree your contribution is licensed under the
[MIT License](LICENSE) the same way the rest of the project is.
