"""Structured model payloads and bounded local validation helpers.

No product-specific task, provider dispatch or command-line entrypoint lives here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from openai_budget import MAX_OUTPUT, MODEL, Refused, canonical, safe_path, owned_file, read_owned, write_owned

from .owned_paths import read_owned_content

ROOT = Path(__file__).resolve().parents[2]


def frozen_inputs(project):
    paths = [p for p in project.iterdir() if p.is_file()]
    for folder in ('src', 'tests', 'scripts', 'fixtures'):
        paths.extend((project / folder).rglob('*'))
    result = {}
    for path in paths:
        if path.is_symlink():
            raise Refused('Linked product input')
        if path.is_file():
            result[str(path.relative_to(project))] = hashlib.sha256(read_owned_content(project, path.relative_to(project))).hexdigest()
    return result

def payload(instructions, data, schema, name, *, output_tokens=MAX_OUTPUT):
    return {"model": MODEL, "service_tier": "default", "store": False,
            "reasoning": {"effort": "low"}, "max_output_tokens": output_tokens,
            "instructions": instructions, "input": canonical(data),
            "text": {"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}}}

def output_json(response):
    parts = [part["text"] for item in response.get("output", []) if item.get("type") == "message"
             for part in item.get("content", []) if part.get("type") == "output_text"]
    if len(parts) != 1:
        raise Refused("Missing or ambiguous structured model result")
    return json.loads(parts[0])

PATCH_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "edits"],
                "properties": {"summary": {"type": "string"}, "edits": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["path", "old", "new"],
                    "properties": {k: {"type": "string"} for k in ("path", "old", "new")}}}}}

REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["summary", "findings"],
                 "properties": {"summary": {"type": "string"}, "findings": {"type": "array", "items": {
                     "type": "object", "additionalProperties": False, "required": ["priority", "title", "detail"],
                     "properties": {"priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3", "P4"]},
                                    "title": {"type": "string"}, "detail": {"type": "string"}}}}}}

def product_root(candidate, product):
    if not isinstance(product, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,40}', product):
        raise Refused('Invalid registered product directory')
    path = candidate.resolve() / product
    if path.is_symlink():
        raise Refused('Linked product directory')
    return path

def sandbox_profile(candidate, key_file, product):
    real = str(candidate.resolve())
    project = product_root(candidate, product)
    protected = [project / folder for folder in ('src', 'tests', 'scripts', 'fixtures', 'node_modules')]
    protected.extend(p for p in project.iterdir() if p.is_file())
    protected.append(candidate.resolve() / 'validation.sb')
    return '\n'.join([
        '(version 1)', '(allow default)', '(deny network*)',
        '(allow network-inbound (local ip "localhost:4173"))',
        '(allow network-outbound (remote ip "localhost:4173"))',
        '(deny file-write*)', '(allow file-write* (literal "/dev/null") (subpath ' + json.dumps(real) + '))',
        *['(deny file-write* (subpath ' + json.dumps(str(p)) + '))' for p in protected],
        *['(allow file-write* (subpath ' + json.dumps(str(project / 'node_modules' / p)) + '))'
          for p in ('.tmp', '.vite', '.vite-temp')],
        '(deny file-read-data (literal ' + json.dumps(str(key_file)) + '))',
        '(deny file-read-data (regex #"/([.]env($|[.])|[.]git-credentials$|[.]npmrc$)"))',
        '(deny file-read-data (regex #"^/Users/[^/]+/([.]codex|[.]ssh|[.]aws|[.]azure|[.]kube|[.]docker|[.]config/(gh|gcloud)|Library/Keychains)(/|$)"))',
    ]) + '\n'

def validate(candidate, commands, prefix, *, product, capture_stdout=False):
    import contextlib
    project = product_root(candidate, product)
    frozen = frozen_inputs(project)
    profile = candidate / "validation.sb"
    safe_path(profile)
    policy = sandbox_profile(candidate, ROOT / ".env.local", product)
    write_owned(candidate, profile.name, policy)
    for folder in (candidate / "home", candidate / "tmp"):
        safe_path(folder)
        folder.mkdir(exist_ok=True)
    environment = {"PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                   "HOME": str(candidate / "home"), "TMPDIR": str(candidate / "tmp") + "/",
                   "PLAYWRIGHT_BROWSERS_PATH": str(Path.home() / 'Library/Caches/ms-playwright'),
                   "npm_config_offline": "true", "npm_config_audit": "false", "npm_config_fund": "false"}
    outcomes = []
    for index, command in enumerate(commands):
        if read_owned(candidate, profile.name).decode('utf-8') != policy:
            raise Refused('Sandbox policy changed before command execution')
        logfile = candidate / (prefix + "-" + str(index) + ".log")
        with contextlib.ExitStack() as stack:
            stream = stack.enter_context(os.fdopen(owned_file(candidate, logfile.name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), 'w'))
            output_file = candidate / (prefix + "-" + str(index) + ".stdout")
            output = stack.enter_context(os.fdopen(owned_file(candidate, output_file.name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC), 'wb')) if capture_stdout else stream
            child = subprocess.Popen(["/usr/bin/sandbox-exec", "-f", str(profile), *command],
                                     cwd=project, env=environment,
                                     stdout=output, stderr=stream if capture_stdout else subprocess.STDOUT,
                                     start_new_session=True)
            try:
                code = child.wait(timeout=180)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                code = 124
        outcomes.append({"command": command, "exitCode": code, "log": str(logfile.relative_to(candidate))})
        if read_owned(candidate, profile.name).decode('utf-8') != policy:
            raise Refused('Sandbox policy changed during command execution')
        safe_path(logfile)
        if capture_stdout:
            safe_path(output_file)
            outcomes[-1]['stdout'] = str(output_file.relative_to(candidate))
        if frozen_inputs(project) != frozen:
            raise Refused('Validation changed frozen source, tests or configuration')
        if code:
            break
    return outcomes
