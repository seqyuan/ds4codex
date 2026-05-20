# ds4codex

`ds4codex` lets Codex use DeepSeek models through a small local proxy.

Codex talks to custom providers with the Responses API shape. DeepSeek exposes a Chat Completions compatible API. `ds4codex` sits between them and translates requests, responses, streaming events, tool calls, and thinking-mode options.

The package keeps the user-facing setup intentionally small:

- one human-edited config file: `~/.codex/config.toml`
- one generated model catalog for Codex `/model`: `~/.codex/ds4codex-model-catalog.json`
- two CLI commands: `ds4codex init` and `ds4codex run`

## Install

Install from PyPI:

```bash
pip install ds4codex -i https://pypi.org/simple
```

For local development from this repository:

```bash
pip install -e .
```

## Quick Start

Initialize Codex and write your DeepSeek API key into the managed provider block:

```bash
ds4codex init --apikey sk-your-deepseek-api-key
```

Start the local proxy:

```bash
ds4codex run
```

Keep `ds4codex run` running while using Codex. Codex will connect to `http://127.0.0.1:8099/v1`.

After initialization, start Codex and use `/model` to choose:

- `DeepSeek V4 Flash`
- `DeepSeek V4 Pro`

## What `init` Does

`ds4codex init` writes managed blocks into `~/.codex/config.toml` and generates the model catalog JSON used by Codex `/model`.

Generated file:

```text
~/.codex/ds4codex-model-catalog.json
```

Codex config blocks:

```toml
# BEGIN DS4CODEX ROOT
model = "deepseek-v4-flash"
model_provider = "ds4codex"
model_context_window = 1048576
model_catalog_json = "/home/you/.codex/ds4codex-model-catalog.json"
# END DS4CODEX ROOT

# BEGIN DS4CODEX SETTINGS
[ds4codex]
port = 8099
target_url = "https://api.deepseek.com/v1/chat/completions"
thinking = "disabled"
# END DS4CODEX SETTINGS

# BEGIN DS4CODEX PROVIDER
[model_providers.ds4codex]
name = "DeepSeek via ds4codex"
base_url = "http://127.0.0.1:8099/v1"
wire_api = "responses"
experimental_bearer_token = "sk-your-deepseek-api-key"
# END DS4CODEX PROVIDER
```

If `~/.codex/config.toml` already exists, `init` preserves existing user config and only adds or refreshes the managed ds4codex blocks.

Useful options:

```bash
ds4codex init --apikey sk-your-deepseek-api-key
ds4codex init --force
ds4codex init --config-path /path/to/config.toml
ds4codex init --model-catalog-path /path/to/ds4codex-model-catalog.json
```

## Model Catalog

The model catalog is required for a good Codex `/model` experience. It is a JSON file, not a directory.

`ds4codex init` points Codex at the catalog through:

```toml
model_catalog_json = "/home/you/.codex/ds4codex-model-catalog.json"
```

The catalog makes Codex aware of the two DeepSeek entries:

- `deepseek-v4-flash`
- `deepseek-v4-pro`

It also advertises Codex reasoning choices:

- `low`
- `medium`
- `high`
- `xhigh`

`ds4codex` first tries to reuse Codex's bundled model schema when the local `codex` executable is available. If `codex` is missing, not executable, or fails, `ds4codex` uses its own built-in template. This means `ds4codex init` does not require a working local `codex` binary.

## Runtime Settings

The proxy reads runtime settings from the `[ds4codex]` section inside `~/.codex/config.toml`:

```toml
[ds4codex]
port = 8099
target_url = "https://api.deepseek.com/v1/chat/completions"
thinking = "disabled"
```

`~/.config/ds4codex/config.toml` is not used.

Runtime values can also be overridden from the CLI:

```bash
ds4codex run --port 8099
ds4codex run --target-url https://api.deepseek.com/v1/chat/completions
ds4codex run --thinking high
```

or with environment variables:

```bash
DS4CODEX_PORT=8099 ds4codex run
DS4CODEX_TARGET_URL=https://api.deepseek.com/v1/chat/completions ds4codex run
DS4CODEX_THINKING=high ds4codex run
```

## API Key Handling

The recommended path is:

```bash
ds4codex init --apikey sk-your-deepseek-api-key
```

This writes the token into:

```toml
[model_providers.ds4codex]
experimental_bearer_token = "sk-your-deepseek-api-key"
```

Codex sends that token to the local proxy as the incoming bearer token, and the proxy forwards it to DeepSeek.

Alternative runtime sources are also supported:

```bash
DS4CODEX_API_KEY=sk-your-deepseek-api-key ds4codex run
DEEPSEEK_API_KEY=sk-your-deepseek-api-key ds4codex run
ds4codex run --api-key sk-your-deepseek-api-key
```

## Thinking Mode

`thinking` in `[ds4codex]` is only the default used when Codex does not send an explicit reasoning level.

Accepted practical values:

- `disabled`
- `enabled`
- `high`
- `max`

Codex `/model` exposes these reasoning levels through the generated catalog:

- `low`
- `medium`
- `high`
- `xhigh`

DeepSeek currently documents `high` and `max` for reasoning effort, so `ds4codex` maps Codex-style values before forwarding upstream:

- `low`, `medium`, `minimal` -> `high`
- `high` -> `high`
- `xhigh` -> `max`

## Why the Proxy Is Required

The proxy is still required even though configuration lives in `~/.codex/config.toml`.

Codex custom providers use:

```toml
wire_api = "responses"
```

DeepSeek uses a Chat Completions compatible endpoint:

```text
https://api.deepseek.com/v1/chat/completions
```

`ds4codex` translates between those two protocols.

## Troubleshooting

If `/model` does not show DeepSeek models, rerun:

```bash
ds4codex init --force
```

Then confirm `~/.codex/config.toml` contains `model_catalog_json` pointing to an existing `ds4codex-model-catalog.json` file.

If `ds4codex init` previously failed with `PermissionError: [Errno 13] Permission denied: 'codex'`, upgrade to `ds4codex >= 0.1.2`. Current versions fall back to the built-in model template when local `codex` cannot be executed.

If DeepSeek returns `messages[0].role: unknown variant developer`, upgrade to `ds4codex >= 0.1.3`. Current versions map Codex `developer` messages to DeepSeek-compatible `system` messages.

If requests fail with an API-key error, check that one of these is true:

- `experimental_bearer_token` in `~/.codex/config.toml` contains a real DeepSeek key
- `DS4CODEX_API_KEY` is set when running `ds4codex run`
- `DEEPSEEK_API_KEY` is set when running `ds4codex run`
- `ds4codex run --api-key ...` was used
