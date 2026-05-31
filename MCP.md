# Trust Engine MCP Server

The Trust Engine MCP server exposes validation evidence as a metadata-first orientation layer for
agents. It lets an AI client discover data products, read the latest trust posture, inspect failures
and generate repair plans before using any data product access path.

This first MCP slice is report-backed and read-only. Run validation first, then point the MCP server
at the directory containing the generated JSON reports.

## Install

Use Python 3.10 or later. On Windows, create a dedicated virtual environment from the repository
root so the MCP client does not accidentally launch an older global Python:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .[mcp]
```

For live Teradata validation, also install the Teradata optional dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[mcp,teradata]
```

If `py -3.13` is not available, use another installed Python 3.10+ interpreter. Do not configure the
MCP client to use Python 3.8; this project requires Python 3.10 or later.

During local development from the repository root, setting `PYTHONPATH` can work if the package is
not installed, but installing into a virtual environment is preferred:

```powershell
$env:PYTHONPATH='src'
```

## Generate Trust Evidence

The MCP server reads Trust Engine JSON reports. Generate a report before starting the server:

```powershell
.\.venv\Scripts\python.exe -m ai_native_data_product_trust_engine validate `
  --prefix CallCentre `
  --output reports\callcentre-validation.json `
  --html-output reports\callcentre-validation.html
```

The server discovers products by reading `*.json` files in the reports directory and selecting the
latest report for each `prefix`.

## Start The Server

Start the MCP server over the report directory:

```powershell
.\.venv\Scripts\python.exe -m ai_native_data_product_trust_engine mcp-server --reports-dir reports
```

Or use the installed console script:

```powershell
.\.venv\Scripts\adp-trust.exe mcp-server --reports-dir reports
```

## Client Configuration

Configure an MCP client to launch the server as a stdio command. Use the absolute path to the
virtual environment Python and an absolute `--reports-dir` path. This avoids the client resolving
`python` to an unsupported interpreter such as Python 3.8.

```json
{
  "mcpServers": {
    "adp-trust": {
      "command": "C:\\SCM\\ai-native-data-product-trust-engine\\.venv\\Scripts\\python.exe",
      "args": [
        "-m",
        "ai_native_data_product_trust_engine",
        "mcp-server",
        "--reports-dir",
        "C:\\SCM\\ai-native-data-product-trust-engine\\reports"
      ]
    }
  }
}
```

If you prefer the installed console script, point directly at the venv script:

```json
{
  "mcpServers": {
    "adp-trust": {
      "command": "C:\\SCM\\ai-native-data-product-trust-engine\\.venv\\Scripts\\adp-trust.exe",
      "args": [
        "mcp-server",
        "--reports-dir",
        "C:\\SCM\\ai-native-data-product-trust-engine\\reports"
      ]
    }
  }
}
```

If you run from source without installing the package, set an absolute `PYTHONPATH` and still use a
Python 3.10+ interpreter:

```json
{
  "mcpServers": {
    "adp-trust": {
      "command": "C:\\Users\\pd185014\\AppData\\Local\\Programs\\Python\\Python313\\python.exe",
      "args": [
        "-m",
        "ai_native_data_product_trust_engine",
        "mcp-server",
        "--reports-dir",
        "C:\\SCM\\ai-native-data-product-trust-engine\\reports"
      ],
      "env": {
        "PYTHONPATH": "C:\\SCM\\ai-native-data-product-trust-engine\\src"
      }
    }
  }
}
```

## Resources

Start at `trust://products`. The client should not guess where to begin.

- `trust://products`
- `trust://products/{prefix}/orientation`
- `trust://products/{prefix}/latest-report`
- `trust://products/{prefix}/scores`
- `trust://products/{prefix}/checks`
- `trust://products/{prefix}/failures`
- `trust://products/{prefix}/repair-candidates`

Recommended navigation:

1. `trust://products`
2. `trust://products/{prefix}/orientation`
3. `trust://products/{prefix}/scores`
4. `trust://products/{prefix}/failures`
5. `trust://products/{prefix}/repair-candidates`
6. `trust://products/{prefix}/checks`
7. `trust://products/{prefix}/latest-report`

## Tools

The server exposes read-only tools backed by the latest report:

- `search_data_products`
- `describe_data_product`
- `get_recommended_entrypoint`
- `list_failed_checks`
- `generate_repair_plan`
- `explain_check`

The tools are deliberately read-only in this slice. They do not connect to Teradata, run validation
or apply repairs. Mutation-oriented self-healing tools should be added later with explicit approval
semantics.

## Agent Handshake

Agents should use this metadata-first handshake:

1. List products with `trust://products` or `search_data_products`.
2. Read the product orientation manifest.
3. Inspect scores, failures and repair candidates.
4. Resolve critical trust issues or seek steward approval.
5. Only then use the approved data product access path outside the Trust Engine.

The design goal is simple: expose product trust first, not tables first.

## Troubleshooting

If the server says the MCP SDK is unavailable, install the optional dependency:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[mcp]
```

If the MCP log says `No module named ai_native_data_product_trust_engine`, the MCP client is using a
Python environment where the project is not installed. Configure the client to use the absolute path
to `.venv\Scripts\python.exe`, or install the project into the interpreter the client is launching.

If the MCP log shows Python 3.8, change the client command to a Python 3.10+ interpreter:

```json
"command": "C:\\SCM\\ai-native-data-product-trust-engine\\.venv\\Scripts\\python.exe"
```

If no products are listed, check that the reports directory exists and contains Trust Engine JSON
reports with a `prefix` field.

If a product cannot be found, rerun validation for that prefix:

```powershell
.\.venv\Scripts\python.exe -m ai_native_data_product_trust_engine validate `
  --prefix CallCentre `
  --output reports\callcentre-validation.json
```

If an MCP client starts the server but cannot find reports, change `--reports-dir` to an absolute
path.
