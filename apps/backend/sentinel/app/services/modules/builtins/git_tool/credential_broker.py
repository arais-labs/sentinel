"""Authenticate HTTP on the host; relay only credential-free bytes to the guest."""

from __future__ import annotations

import asyncio
import base64
import fnmatch
import json
import shlex
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from graphql import OperationType, parse, value_from_ast_untyped
from graphql.language import FieldNode, OperationDefinitionNode, StringValueNode, VariableNode

from sentral.errors import ToolValidationError


def certificate(hosts):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sentinel operation proxy")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


def graphql_document(body):
    value = json.loads(body)
    return parse(value["query"]), value.get("variables") or {}


def graphql_read(body):
    try:
        document, _ = graphql_document(body)
        operations = [
            node for node in document.definitions if isinstance(node, OperationDefinitionNode)
        ]
        return bool(operations) and all(
            node.operation == OperationType.QUERY for node in operations
        )
    except Exception:
        return False


def graphql_in_scope(body, host, scope):
    """Scoped accounts may query explicit repositories, never arbitrary node IDs."""
    if not graphql_read(body):
        return False
    try:
        document, variables = graphql_document(body)
        for operation in document.definitions:
            if not isinstance(operation, OperationDefinitionNode):
                continue
            for field in operation.selection_set.selections:
                if not isinstance(field, FieldNode) or field.name.value != "repository":
                    return False
                args = {}
                for argument in field.arguments:
                    value = argument.value
                    args[argument.name.value] = (
                        variables.get(value.name.value)
                        if isinstance(value, VariableNode)
                        else value.value if isinstance(value, StringValueNode) else None
                    )
                if not all(isinstance(args.get(key), str) for key in ("owner", "name")):
                    return False
                if not fnmatch.fnmatch(
                    f"{host}/{args['owner']}/{args['name']}".lower(), scope.lower()
                ):
                    return False
        return True
    except Exception:
        return False


async def verify_graphql_scope(client, account, body):
    """Resolve opaque GitHub IDs on the host before granting scoped operations."""
    if graphql_in_scope(body, account.host, account.scope_pattern):
        return True
    try:
        document, variables = graphql_document(body)
        ids = set()
        for operation in document.definitions:
            if not isinstance(operation, OperationDefinitionNode):
                continue
            for field in operation.selection_set.selections:
                if not isinstance(field, FieldNode):
                    return False
                args = {
                    arg.name.value: value_from_ast_untyped(arg.value, variables)
                    for arg in field.arguments
                }
                if operation.operation == OperationType.QUERY and field.name.value == "node":
                    resources = [args.get("id")]
                elif operation.operation == OperationType.MUTATION:
                    data = args.get("input") or {}
                    resources = [
                        data[k]
                        for k in (
                            "repositoryId",
                            "pullRequestId",
                            "issueId",
                            "subjectId",
                            "discussionId",
                            "commentId",
                            "pullRequestReviewId",
                        )
                        if k in data
                    ]
                else:
                    return False
                if not resources or any(not isinstance(item, str) for item in resources):
                    return False
                ids.update(resources)
        if not ids:
            return False
        host = account.host.lower()
        endpoint = (
            "https://api.github.com/graphql"
            if host == "github.com"
            else f"https://{host}/api/graphql"
        )
        query = "query($id:ID!){node(id:$id){... on Repository{nameWithOwner} ... on PullRequest{repository{nameWithOwner}} ... on Issue{repository{nameWithOwner}} ... on IssueComment{repository{nameWithOwner}} ... on PullRequestReview{repository{nameWithOwner}} ... on Discussion{repository{nameWithOwner}}}}"
        for node_id in ids:
            response = await client.post(
                endpoint,
                json={"query": query, "variables": {"id": node_id}},
                headers={"authorization": f"Bearer {account.token}"},
            )
            response.raise_for_status()
            node = response.json().get("data", {}).get("node") or {}
            repo = node.get("nameWithOwner") or (node.get("repository") or {}).get("nameWithOwner")
            if not repo or not fnmatch.fnmatch(
                f"{host}/{repo}".lower(), account.scope_pattern.lower()
            ):
                return False
        return True
    except Exception:
        return False


def authorize_request(
    *,
    host,
    api_host,
    authority,
    method,
    path,
    mode,
    scope,
    repo,
    body,
    verified_graphql_scope=False,
):
    authority = authority.lower().removesuffix(":443")
    if authority not in {host, api_host} or not path.startswith("/") or path.startswith("//"):
        raise ToolValidationError("Git broker rejected an unexpected destination")
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    if any(part in {".", ".."} for part in decoded.split("/")):
        # Do not authorize one path and let HTTP normalization send another.
        raise ToolValidationError(
            "Encoded or traversing API paths are not supported by the Git broker"
        )
    if method == "GET" and parsed.path in {"/graphql", "/api/graphql"}:
        params = parse_qs(parsed.query)
        body = json.dumps(
            {
                "query": params.get("query", [""])[0],
                "variables": json.loads(params.get("variables", ["{}"])[0]),
            }
        ).encode()
        if not graphql_read(body):
            raise ToolValidationError("GraphQL GET must contain a query")
    if authority == host and not parsed.path.startswith("/api/"):
        suffix = next(
            (
                x
                for x in ("/info/refs", "/git-upload-pack", "/git-receive-pack")
                if parsed.path.endswith(x)
            ),
            None,
        )
        if suffix is None:
            raise ToolValidationError("Git broker permits only Git protocol requests on this host")
        target = parsed.path[: -len(suffix)].strip("/").removesuffix(".git")
        if repo and target != repo.removesuffix(".git"):
            raise ToolValidationError("Git request does not match the selected repository")
        if scope != "*" and not fnmatch.fnmatch(f"{host}/{target}", scope):
            raise ToolValidationError("Repository is outside the selected account scope")
        if mode == "read" and (suffix == "/git-receive-pack" or "git-receive-pack" in parsed.query):
            raise ToolValidationError("Git read permission cannot push")
        if method not in {"GET", "POST"}:
            raise ToolValidationError("Unsupported Git protocol method")
    else:
        if scope != "*":
            # REST repo operations can be scoped exactly. General discovery and
            # GraphQL cannot safely enforce arbitrary patterns server-side.
            parts = parsed.path.removeprefix("/api/v3").strip("/").split("/")
            in_scope = (
                (verified_graphql_scope or graphql_in_scope(body, host, scope))
                if parsed.path in {"/graphql", "/api/graphql"}
                else len(parts) >= 3
                and parts[0] == "repos"
                and fnmatch.fnmatch(f"{host}/{parts[1]}/{parts[2]}".lower(), scope.lower())
            )
            if not in_scope:
                raise ToolValidationError(
                    "This API request cannot be authorized within the account repository scope; use a scoped REST repository endpoint"
                )
        if mode == "read" and method not in {"GET", "HEAD"}:
            if not (
                method == "POST"
                and parsed.path in {"/graphql", "/api/graphql"}
                and graphql_read(body)
            ):
                raise ToolValidationError("GitHub read permission cannot send a mutation")
    return f"https://{authority}{path}"


async def run_brokered(
    *,
    terminal,
    tokens,
    cwd,
    account,
    mode,
    timeout,
    repo=None,
    env=None,
    git_identity=None,
):
    host = account.host.lower()
    api_host = "api.github.com" if host == "github.com" else host
    cert, key = certificate({host, api_host})
    spec = {
        "argv": tokens,
        "cwd": cwd,
        "host": host,
        "env": env or {},
        "git_identity": git_identity,
        "cert": cert,
        "key": key,
        "authorities": [f"{h}:443" for h in {host, api_host}],
    }
    source = Path(__file__).with_name("credential_proxy_guest.py").read_text()
    command = (
        "python3 -c "
        + shlex.quote(source)
        + " "
        + shlex.quote(base64.b64encode(json.dumps(spec).encode()).decode())
    )
    process = await terminal.ssh.create_process(command, encoding=None)
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    requests = {}
    tasks = set()
    code = -1
    timed_out = False

    async def send(event):
        process.stdin.write((json.dumps(event) + "\n").encode())
        # Bound the runtime stream queue instead of filling it with a pack file.
        queue = getattr(process, "queue", None)
        while queue is not None and queue.qsize() > 16:
            await asyncio.sleep(0.01)

    async with httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=timeout
    ) as client:

        async def forward(request_id):
            entry, body = requests[request_id]
            try:
                body.seek(0)
                preview = (
                    body.read(1024 * 1024)
                    if entry["path"].split("?")[0] in {"/graphql", "/api/graphql"}
                    else b""
                )
                headers = {k.lower(): v for k, v in entry["headers"].items()}
                verified_scope = False
                if account.scope_pattern != "*" and entry["path"].split("?")[0] in {
                    "/graphql",
                    "/api/graphql",
                }:
                    verified_scope = await verify_graphql_scope(client, account, preview)
                url = authorize_request(
                    host=host,
                    api_host=api_host,
                    authority=headers.get("host", ""),
                    method=entry["method"],
                    path=entry["path"],
                    mode=mode,
                    scope=account.scope_pattern or "*",
                    repo=repo,
                    body=preview,
                    verified_graphql_scope=verified_scope,
                )
                for name in (
                    "authorization",
                    "proxy-authorization",
                    "host",
                    "connection",
                    "transfer-encoding",
                    "content-length",
                    "cookie",
                ):
                    headers.pop(name, None)
                headers["authorization"] = (
                    "Basic " + base64.b64encode(f"x-access-token:{account.token}".encode()).decode()
                    if urlsplit(url).hostname == host and "/api/" not in urlsplit(url).path
                    else f"Bearer {account.token}"
                )
                headers["accept-encoding"] = "identity"
                body.seek(0)

                async def chunks():
                    while chunk := body.read(128 * 1024):
                        yield chunk

                async with client.stream(
                    entry["method"], url, headers=headers, content=chunks()
                ) as response:
                    await send(
                        {
                            "id": request_id,
                            "status": response.status_code,
                            "headers": dict(response.headers),
                        }
                    )
                    async for chunk in response.aiter_bytes(128 * 1024):
                        await send(
                            {
                                "id": request_id,
                                "type": "body",
                                "data": base64.b64encode(chunk).decode(),
                            }
                        )
            except Exception as exc:
                await send({"id": request_id, "status": 403, "headers": {}})
                await send(
                    {
                        "id": request_id,
                        "type": "body",
                        "data": base64.b64encode(
                            str(exc).replace(account.token, "***").encode()
                        ).decode(),
                    }
                )
            finally:
                await send({"id": request_id, "type": "end"})
                body.close()
                requests.pop(request_id, None)

        try:
            async with asyncio.timeout(timeout):
                while line := await process.stdout.readline():
                    try:
                        event = json.loads(line)
                    except ValueError as exc:
                        raise ToolValidationError(
                            "Git authentication broker process failed"
                        ) from exc
                    kind = event["type"]
                    if kind in output:
                        data = base64.b64decode(event["data"])
                        remaining = max(0, 50000 - len(output[kind]))
                        output[kind].extend(data[:remaining])
                        truncated[kind] |= len(data) > remaining
                    elif kind == "request":
                        requests[event["id"]] = (
                            event,
                            tempfile.SpooledTemporaryFile(max_size=1024 * 1024),
                        )
                    elif kind == "body":
                        requests[event["id"]][1].write(base64.b64decode(event["data"]))
                    elif kind == "end":
                        task = asyncio.create_task(forward(event["id"]))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif kind == "exit":
                        code = event["code"]
                        break
        except TimeoutError:
            timed_out = True
            output["stderr"].extend(b"Git command timed out")
        finally:
            try:
                if code == -1:
                    await send({"type": "cancel"})
                await asyncio.wait_for(process.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError, BrokenPipeError, ConnectionError):
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except (TimeoutError, ProcessLookupError):
                    pass
            for task in list(tasks):
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for _, body in requests.values():
                body.close()
    return {
        "ok": code == 0,
        "returncode": code,
        "timed_out": timed_out,
        **{
            name: bytes(value).decode(errors="replace").replace(account.token, "***")
            for name, value in output.items()
        },
        "cwd": cwd,
        "command": shlex.join(tokens),
        "argv": tokens,
        "truncated": truncated,
    }
