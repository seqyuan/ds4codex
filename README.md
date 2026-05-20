# ds4codex

`ds4codex` is a small local proxy that lets Codex use DeepSeek models through a Responses-to-Chat translation layer.

The current shape is intentionally simple:

- only one human-edited config file: `~/.codex/config.toml`
- one generated file for `/model`: `~/.codex/ds4codex-model-catalog.json`
- only two CLI commands: `init` and `run`

## Install

```bash
cd /Volumes/data/github/seqyuan/ds4codex
pip install .
```

or:

```bash
pipx install .
```

## Commands

Initialize Codex config and model catalog:

```bash
ds4codex init --apikey sk-your-deepseek-api-key
```

`init` will try to reuse Codex's bundled model schema when `codex` is available, but it no longer requires a working local `codex` executable.

Start the proxy:

```bash
ds4codex run
```

## What `init` Writes

`ds4codex init` updates `~/.codex/config.toml` and generates:

```text
~/.codex/ds4codex-model-catalog.json
```

It writes three managed blocks into `~/.codex/config.toml`:

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

## Where Settings Live

The proxy now reads its local runtime settings from:

```toml
[ds4codex]
port = 8099
target_url = "https://api.deepseek.com/v1/chat/completions"
thinking = "disabled"
```

That means `~/.config/ds4codex/config.toml` is no longer needed.

`thinking` is only the default when Codex does not send an explicit reasoning level.

Accepted practical values are:

- `disabled`
- `enabled`
- `high`
- `max`

Compatibility mapping is applied for Codex-style reasoning levels:

- `low`, `medium`, `minimal` -> `high`
- `xhigh` -> `max`

## Why the Proxy Is Still Required

Moving settings into `~/.codex/config.toml` makes configuration cleaner, but it does not remove the need for the server itself.

Codex custom providers send:

- `wire_api = "responses"`

DeepSeek currently documents:

- `chat.completions`

So the proxy is still the protocol adapter between Codex Responses requests and DeepSeek Chat Completions requests.

## `/model` Support

`ds4codex init` generates a model catalog JSON and points Codex at it through `model_catalog_json`.

This is a file, not a directory. Its only purpose is to make Codex's `/model` menu aware of the custom DeepSeek entries exposed through `ds4codex`.

That makes `/model` show:

- `DeepSeek V4 Flash`
- `DeepSeek V4 Pro`

and the catalog advertises these reasoning levels:

- `low`
- `medium`
- `high`
- `xhigh`

The proxy maps those levels to DeepSeek-compatible request fields before forwarding upstream.
