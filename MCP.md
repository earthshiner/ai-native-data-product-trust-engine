# Trust Engine MCP Server

The Trust Engine MCP server exposes validation evidence as a metadata-first orientation layer for
agents. It lets an AI client discover data products, read the latest trust posture, inspect failures
and generate repair plans before using any data product access path.

This first MCP slice is report-backed and read-only. Run validation first, then point the MCP server
at the directory containing the generated JSON reports.

## Install

Install the project with the optional MCP dependency:

```powershell
pip install .[mcp]
```

For live Teradata validation, also install the Teradata optional dependency:

```powershell
pip install .[mcp,teradata]
```

During local development from the repository root, set `PYTHONPATH` if the package is not installed:

```powershell
$env:PYTHONPATH='src'
```

## Generate Trust Evidence

The MCP server reads Trust Engine JSON reports. Generate a report before starting the server:

```powershell
python -m ai_native_data_product_trust_engine validate `
  --prefix CallCentre `
  --output reports\callcentre-validation.json `
  --html-output reports\callcentre-validation.html
```

The server discovers products by reading `*.json` files in the reports directory and selecting the
latest report for each `prefix`.

## Start The Server

Start the MCP server over the report directory:

```powershell
python -m ai_native_data_product_trust_engine mcp-server --reports-dir reports
```

Or use the installed console script:

```powershell
adp-trust mcp-server --reports-dir reports
```

## Client Configuration

Configure an MCP client to launch the server as a stdio command. Example:

```json
{
  "mcpServers": {
    "adp-trust": {
      "command": "python",
      "args": [
        "-m",
        "ai_native_data_product_trust_engine",
        "mcp-server",
        "--reports-dir",
        "reports"
      ],
      "env": {
        "PYTHONPATH": "src"
      }
    }
  }
}
```

If the package is installed in the client runtime, `PYTHONPATH` is not required and the command can
use `adp-trust` instead:

```json
{
  "mcpServers": {
    "adp-trust": {
      "command": "adp-trust",
      "args": ["mcp-server", "--reports-dir", "reports"]
    }
  }
}
```

Use an absolute `--reports-dir` path when the MCP client starts the command from a different working
directory.

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
pip install .[mcp]
```

If no products are listed, check that the reports directory exists and contains Trust Engine JSON
reports with a `prefix` field.

If a product cannot be found, rerun validation for that prefix:

```powershell
python -m ai_native_data_product_trust_engine validate `
  --prefix CallCentre `
  --output reports\callcentre-validation.json
```

If an MCP client starts the server but cannot find reports, change `--reports-dir` to an absolute
path.
