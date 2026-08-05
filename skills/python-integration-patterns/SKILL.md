---
name: python-integration-patterns
description: >
  Starter code patterns for this project's recommended frameworks — a Flask web
  app, a FastAPI REST endpoint, a FastMCP server, and a Click CLI. Consult when
  scaffolding a web app, REST API, MCP server, or command-line interface.
version: 1
scope: reference
metadata:
  tags:
    - python
    - flask
    - fastapi
    - fastmcp
    - click
    - mcp
    - cli
    - patterns
    - reference
---

# Python Integration Patterns

## Overview

Minimal, idiomatic starting points for the frameworks this project recommends.
Each pattern imports from a `package_name.core` module as a placeholder for your
own logic — replace it with your package.

## When to Use

- Scaffolding a web app (Flask), a REST API (FastAPI), an MCP server (FastMCP),
  or a command-line interface (Click). Install the matching optional-dependency
  group first (e.g. `pip install -e ".[web]"`, `".[api]"`, `".[mcp]"`, `".[cli]"`).

## Flask web app

```python
from flask import Flask, render_template, request
from package_name.core import process_data

app = Flask(__name__)

@app.route('/')
def index():
    """Render main page."""
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process():
    """Process uploaded data."""
    data = request.get_json()
    result = process_data(data['values'])
    return {'result': result}
```

## FastAPI REST endpoint

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from package_name.core import process_data

app = FastAPI()

class DataRequest(BaseModel):
    values: list[float]
    threshold: float = 0.5

@app.post("/process")
async def process_endpoint(request: DataRequest):
    """Process data via a REST endpoint."""
    try:
        result = process_data(request.values, request.threshold)
        return {"status": "success", "result": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```

## FastMCP server (MCP tools)

```python
from fastmcp import FastMCP
from package_name.core import process_data

mcp = FastMCP("package-name")

@mcp.tool()
def process(values: list[float], threshold: float = 0.5) -> dict:
    """Expose data processing as an MCP tool."""
    return {"result": process_data(values, threshold)}

if __name__ == "__main__":
    mcp.run()
```

## Click CLI

```python
import click
from package_name.core import process_data

@click.command()
@click.argument('input_file', type=click.Path(exists=True))
@click.option('--threshold', default=0.5, help='Filtering threshold')
@click.option('--output', '-o', help='Output file path')
def process_cli(input_file, threshold, output):
    """Process data from INPUT_FILE."""
    # Load data
    with open(input_file) as f:
        data = [float(x) for x in f]

    # Process
    result = process_data(data, threshold)

    # Save or print
    if output:
        with open(output, 'w') as f:
            f.write('\n'.join(map(str, result)))
        click.echo(f"Saved {len(result)} values to {output}")
    else:
        click.echo(result)
```
