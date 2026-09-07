import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = PROJECT_ROOT / "contracts"
EXAMPLES = CONTRACTS / "examples"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validators() -> dict[str, Draft202012Validator]:
    request_schema = load_json(CONTRACTS / "performance-request.v1.schema.json")
    result_schema = load_json(CONTRACTS / "performance-result.v1.schema.json")
    Draft202012Validator.check_schema(request_schema)
    Draft202012Validator.check_schema(result_schema)

    registry = Registry().with_resource(
        request_schema["$id"], Resource.from_contents(request_schema)
    )
    format_checker = FormatChecker()
    return {
        "performance-request.v1": Draft202012Validator(
            request_schema, registry=registry, format_checker=format_checker
        ),
        "performance-result.v1": Draft202012Validator(
            result_schema, registry=registry, format_checker=format_checker
        ),
    }


def test_json_schemas_accept_valid_examples(
    validators: dict[str, Draft202012Validator],
) -> None:
    for path in sorted(EXAMPLES.glob("*.valid.json")):
        document = load_json(path)
        validators[document["contract_version"]].validate(document)


def test_request_schema_rejects_stored_authorization(
    validators: dict[str, Draft202012Validator],
) -> None:
    document = load_json(EXAMPLES / "invalid" / "request.secret-header.invalid.json")

    errors = list(validators["performance-request.v1"].iter_errors(document))

    assert errors
    assert any("Additional properties are not allowed" in error.message for error in errors)


def test_domain_contract_verifier() -> None:
    completed = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "verify_contract.py")],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Contract verification passed" in completed.stdout
