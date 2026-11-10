"""Text-only Responses API with a persistent, pessimistic USD10 campaign cap."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import time
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

from aim_ci.owned_paths import (Refused, owned_directory, owned_file, owned_mkdir,
                               read_owned, safe_path, write_owned)

MODEL = "gpt-5-mini-2025-08-07"
LIMIT = 10_000_000_000  # nanodollars; USD10 across this entire experiment
INPUT_RATE = 250  # USD0.25 / million tokens, uncached standard service
OUTPUT_RATE = 2000  # USD2 / million tokens, including reasoning
MAX_CALLS = 4
DEFAULT_OUTPUT = 8192
MAX_OUTPUT = 8192  # Immutable historical ledger/probe allowance, not a provider or product maximum.
# Actual pinned-model limits, not local product policy. Verified 2026-11-09:
# https://developers.openai.com/api/docs/models/gpt-5-mini
PROVIDER_MAX_INPUT = 272_000
PROVIDER_MAX_OUTPUT = 128_000
PROVIDER_CONTEXT = 400_000
PRICING = "https://developers.openai.com/api/docs/pricing"
REQUEST_DEADLINE_SECONDS = 240


class ApiFailure(Refused):
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__('OpenAI HTTP ' + str(status) + ' (' + code + ')')


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def provider_token_bounds(input_tokens, output_tokens):
    if (type(input_tokens) is not int or input_tokens < 0
            or type(output_tokens) is not int or output_tokens < 1):
        raise Refused('Invalid provider token count or output allowance')
    if (input_tokens > PROVIDER_MAX_INPUT or output_tokens > PROVIDER_MAX_OUTPUT
            or input_tokens + output_tokens > PROVIDER_CONTEXT):
        raise Refused('Request exceeds the pinned model provider token limits')


def atomic_json(path, value):
    safe_path(path)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def terminal_diagnostic(response):
    """Allowlist terminal metadata and visible text, never private reasoning/input.

    This is structural filtering, not secret detection in generated visible text.
    Unknown reasons/metadata are omitted rather than copying provider error text.
    """
    response = response if isinstance(response, dict) else {}
    identifier, status = response.get('id'), response.get('status')
    details = response.get('incomplete_details')
    reason = details.get('reason') if isinstance(details, dict) else None
    usage = response.get('usage')
    observed = {}
    if isinstance(usage, dict):
        for field in ('input_tokens', 'output_tokens', 'total_tokens'):
            if type(usage.get(field)) is int and usage[field] >= 0:
                observed[field] = usage[field]
        for field, count in (('input_tokens_details', 'cached_tokens'),
                             ('output_tokens_details', 'reasoning_tokens')):
            detail = usage.get(field)
            if isinstance(detail, dict) and type(detail.get(count)) is int and detail[count] >= 0:
                observed[field] = {count: detail[count]}
    visible = []
    output = response.get('output')
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict) or item.get('type') != 'message' or item.get('role', 'assistant') != 'assistant':
                continue
            content = item.get('content')
            if isinstance(content, list):
                visible.extend(part['text'] for part in content if isinstance(part, dict)
                               and part.get('type') == 'output_text' and isinstance(part.get('text'), str))
    return {'responseId': identifier if isinstance(identifier, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', identifier) else None,
            'responseStatus': status if isinstance(status, str) and status in
                ('completed', 'incomplete', 'failed', 'cancelled', 'queued', 'in_progress') else None,
            'incomplete_details': {'reason': reason} if isinstance(reason, str) and reason in
                ('max_output_tokens', 'content_filter') else None,
            'usage': observed, 'visibleOutputText': visible}


def private_terminal_record(root, name, value):
    """Publish one no-clobber private record; interrupted staging stays inert.

    Fixed per-operation staging prevents accumulating a streaming/temp log.
    Existing identical final/staged bytes are reusable, never overwritten.
    """
    def check(info, mode):
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
            raise Refused('Terminal diagnostic ownership or permissions are unsafe')

    def read(directory, relative):
        with os.fdopen(owned_file(directory, relative, os.O_RDONLY), 'rb') as stream:
            check(os.fstat(stream.fileno()), 0o600)
            return stream.read(len(encoded) + 1)  # Compare exact expected bytes, without trusting an existing file's size.

    if not isinstance(name, str) or not re.fullmatch(r'[a-f0-9]{64}\.json', name):
        raise Refused('Invalid terminal diagnostic name')
    encoded = (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    parent = owned_directory(root)
    directory = None
    try:
        if os.fstat(parent).st_uid != os.geteuid():
            raise Refused('Terminal diagnostic parent is not owned by the invoking user')
        try:
            os.mkdir('terminal-diagnostics', 0o700, dir_fd=parent)
        except FileExistsError:
            pass
        directory = os.open('terminal-diagnostics', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        check(os.fstat(directory), 0o700)
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        else:
            existing = read(directory, name)
        if existing is not None:
            if existing != encoded:
                raise Refused('Terminal diagnostic already exists with different evidence')
            return
        pending = name + '.pending'
        try:
            descriptor = owned_file(directory, pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        except Refused:
            if read(directory, pending) != encoded:
                raise Refused('Interrupted terminal diagnostic differs; retain for manual reconciliation') from None
        else:
            with os.fdopen(descriptor, 'wb') as stream:
                check(os.fstat(stream.fileno()), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        # link is an atomic no-replace publication. No consumer reads a partial final file.
        os.link(pending, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(pending, dir_fd=directory)
        os.fsync(directory)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


class Campaign:
    def __init__(self, directory, scope, *, maximum_operation_nano_usd=None):
        self.directory = Path(directory).absolute()
        safe_path(self.directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ledger = self.directory / "budget.json"
        self.marker = self.directory / "campaign.json"
        self.scope = scope
        self.maximum_operation_nano_usd = maximum_operation_nano_usd
        with self.lock():
            if not self.ledger.exists():
                if self.marker.exists():
                    raise Refused("Established campaign ledger is missing")
                atomic_json(self.marker, {"scope": scope, "limitNanoUsd": LIMIT, "model": MODEL})
                atomic_json(self.ledger, {"version": 1, "scope": scope, "limitNanoUsd": LIMIT, "model": MODEL,
                                          "inputRate": INPUT_RATE, "outputRate": OUTPUT_RATE,
                                          "pricingSource": PRICING, "requests": []})
            self.read()

    @contextlib.contextmanager
    def lock(self):
        path = self.directory / "budget.lock"
        safe_path(path)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self):
        safe_path(self.ledger)
        safe_path(self.marker)
        data = json.loads(self.ledger.read_text())
        marker = json.loads(self.marker.read_text())
        if (marker != {"scope": self.scope, "limitNanoUsd": LIMIT, "model": MODEL}
                or data.get("version") != 1 or data.get("scope") != self.scope
                or data.get("limitNanoUsd") != LIMIT or data.get("model") != MODEL
                or data.get("inputRate") != INPUT_RATE or data.get("outputRate") != OUTPUT_RATE):
            raise Refused("Campaign identity or pricing changed")
        entries = data.get("requests")
        if not isinstance(entries, list) or len(entries) > MAX_CALLS:
            raise Refused("Invalid campaign request history")
        seen = set()
        for item in entries:
            if (not isinstance(item, dict) or item.get("id") in seen
                    or not isinstance(item.get("id"), str)
                    or type(item.get("reservedNanoUsd")) is not int or item["reservedNanoUsd"] <= 0
                    or item.get("status") not in ("reserved", "completed", "unknown")):
                raise Refused("Invalid reservation history")
            seen.add(item["id"])
        if sum(item["reservedNanoUsd"] for item in entries) > LIMIT:
            raise Refused("Campaign exceeds approved ceiling")
        return data

    def reserve(self, request_id, payload_hash, input_tokens, output_tokens):
        if (type(input_tokens) is not int or input_tokens < 0
                or type(output_tokens) is not int or output_tokens < 1):
            raise Refused("Invalid token bounds")
        # Extra input headroom; reservations are NEVER refunded, even on success.
        amount = (input_tokens + 1024) * INPUT_RATE + output_tokens * OUTPUT_RATE
        if self.maximum_operation_nano_usd is not None and amount > self.maximum_operation_nano_usd:
            raise Refused("Legacy operation exceeds its established financial reservation")
        with self.lock():
            data = self.read()
            if (len(data["requests"]) >= MAX_CALLS
                    or any(item["id"] == request_id for item in data["requests"])
                    or sum(item["reservedNanoUsd"] for item in data["requests"]) + amount > LIMIT):
                raise Refused("Duplicate operation, call limit or USD10 ceiling")
            data["requests"].append({"id": request_id, "payloadSha256": payload_hash,
                                     "inputTokens": input_tokens, "maxOutputTokens": output_tokens,
                                     "reservedNanoUsd": amount, "status": "reserved"})
            atomic_json(self.ledger, data)
        return amount

    def finish(self, request_id, response, failure_class=None, failure_metadata=None):
        with self.lock():
            data = self.read()
            item = next(item for item in data["requests"] if item["id"] == request_id)
            if item["status"] != "reserved":
                raise Refused("Operation already settled")
            item["status"] = "unknown"
            if failure_class:
                item['failureClass'] = failure_class
            if failure_metadata:
                item['failureMetadata'] = failure_metadata
            if isinstance(response, dict) and response.get("model") == MODEL:
                usage = response.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                ins, outs = usage.get("input_tokens"), usage.get("output_tokens")
                if type(ins) is int and type(outs) is int and min(ins, outs) >= 0:
                    actual = ins * INPUT_RATE + outs * OUTPUT_RATE
                    if actual <= item["reservedNanoUsd"] and outs <= item["maxOutputTokens"]:
                        item.update(status="completed", actualNanoUsd=actual, usage=usage,
                                    responseId=response.get("id"), responseStatus=response.get("status"))
            atomic_json(self.ledger, data)
            if item["status"] != "completed":
                raise Refused("Unknown usage/model outcome; full reservation retained, no automatic retry")

    def retain_terminal(self, request_id, response):
        with self.lock():
            item = next(item for item in self.read()['requests'] if item['id'] == request_id)
            value = {'version': 1, 'operation': request_id, 'requestSha256': item['payloadSha256'],
                     'campaignScopeSha256': fingerprint(self.scope), **terminal_diagnostic(response)}
            private_terminal_record(self.directory, hashlib.sha256(request_id.encode()).hexdigest() + '.json', value)

    def call(self, request_id, payload, transport):
        allowed = {"model", "input", "instructions", "max_output_tokens", "reasoning", "text", "service_tier", "store", "stream"}
        if (set(payload) - allowed or payload.get("model") != MODEL or payload.get("store") is not False
                or payload.get("service_tier") != "default" or not isinstance(payload.get("input"), str)
                or payload.get('stream', True) is not True
                or not isinstance(payload.get("instructions"), str)):
            raise Refused("Only pinned-model text requests without tools are admitted")
        count_payload = {key: payload[key] for key in ("model", "input", "instructions", "text") if key in payload}
        count = transport("responses/input_tokens", count_payload)
        provider_token_bounds(count.get('input_tokens'), payload.get('max_output_tokens'))
        self.reserve(request_id, fingerprint(payload), count.get("input_tokens"), payload.get("max_output_tokens"))
        try:
            response = transport("responses", payload)
        except Exception as error:
            failure = type(error).__name__
            metadata = {'httpStatus': error.status, 'code': error.code} if isinstance(error, ApiFailure) else None
            try:
                self.finish(request_id, None, failure_class=failure, failure_metadata=metadata)
            except Refused:
                detail = str(error) if isinstance(error, ApiFailure) else failure
                raise Refused('API request failed (' + detail + '); full reservation retained, no automatic retry') from None
            raise
        incomplete = not isinstance(response, dict) or response.get('status') != 'completed'
        diagnostic_failed = False
        try:
            if incomplete:
                self.retain_terminal(request_id, response)
        except Exception:
            diagnostic_failed = True  # Never log raw response text or skip pessimistic settlement.
        finally:
            self.finish(request_id, response)
        if incomplete:
            detail = '; private terminal diagnostic unavailable' if diagnostic_failed else ''
            raise Refused("Model response incomplete; paid call remains recorded" + detail)
        return response


def transport_from_key_file(path, *, require_private=False, deadline_seconds=REQUEST_DEADLINE_SECONDS):
    # Scoped override for explicit Sol calls; legacy defaults and pricing remain unchanged.
    if type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 3600:
        raise Refused("Invalid API operation deadline")
    path = Path(path)
    safe_path(path)
    if require_private:
        import stat
        try:
            with os.fdopen(owned_file(path.parent, path.name, os.O_RDONLY), 'rb') as stream:
                info = os.fstat(stream.fileno())
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise Refused('Private OpenAI credential file is unavailable or unsafe')
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise Refused('Private OpenAI credential file is unavailable or unsafe')
            content = raw.decode('utf-8')
        except (OSError, UnicodeError, Refused):
            raise Refused('Private OpenAI credential file is unavailable or unsafe') from None
    else:
        content = path.read_text()
    key = None
    for line in content.splitlines():
        if line.startswith("OPENAI_API_KEY="):
            key = line.split("=", 1)[1].strip().strip("\"'")
    if not key or not key.startswith("sk-"):
        raise Refused("No configured OpenAI API key")

    def post(endpoint, payload):
        if endpoint not in ("responses", "responses/input_tokens"):
            raise Refused("Endpoint outside experiment")
        request = urllib.request.Request("https://api.openai.com/v1/" + endpoint,
                                         data=canonical(payload).encode(), method="POST",
                                         headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        deadline_at = time.monotonic() + deadline_seconds
        try:
            # urllib does not automatically retry POSTs. No request/error body is logged.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with operation_deadline(deadline_seconds):
                with opener.open(request, timeout=min(180, deadline_seconds)) as stream:
                    if endpoint == 'responses' and payload.get('stream') is True:
                        return collect_response_stream(stream)
                    raw = stream.read(2_000_001)
                    if len(raw) > 2_000_000:
                        raise Refused("Response exceeds experiment limit")
                    return json.loads(raw)
        except urllib.error.HTTPError as error:
            code = 'unclassified_http_error'
            try:
                with operation_deadline(max(0.001, deadline_at - time.monotonic())):
                    candidate = json.loads(error.read(65536)).get('error', {}).get('code')
                if isinstance(candidate, str) and re.fullmatch('[a-z0-9_]{1,80}', candidate):
                    code = candidate
            except (ValueError, OSError, AttributeError):
                pass
            raise ApiFailure(error.code, code) from None
    return post


def collect_response_stream(stream):
    """Retain only the terminal Responses object; never log provider event text."""
    size = 0
    while True:
        line = stream.readline(2_000_001)
        if not line:
            break
        size += len(line)
        if size > 10_000_000 or len(line) > 2_000_000:
            raise Refused('API event stream exceeds limit')
        if not line.startswith(b'data: '):
            continue
        value = line[6:].strip()
        if value == b'[DONE]':
            break
        event = json.loads(value)
        if event.get('type') in ('response.completed', 'response.incomplete', 'response.failed'):
            result = event.get('response')
            if not isinstance(result, dict):
                raise Refused('Invalid terminal response event')
            return result
    raise Refused('API stream ended without terminal usage; no automatic retry')


@contextlib.contextmanager
def operation_deadline(seconds):
    """Unix main-thread wall deadline, including headers and periodic SSE events."""
    if signal.getitimer(signal.ITIMER_REAL)[0] > 0:
        raise Refused('Existing process deadline must not be overridden')
    previous = signal.getsignal(signal.SIGALRM)
    def expired(signum, frame):
        raise TimeoutError('OpenAI operation wall deadline reached')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Refused("API redirect refused")
