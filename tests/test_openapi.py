import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_openapi.py"
spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
export_openapi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export_openapi)


def test_committed_contract_is_current():
    assert export_openapi.SPEC_PATH.read_text() == export_openapi.render(), (
        "API changed: run `uv run python scripts/export_openapi.py` and commit the result"
    )


def test_every_route_is_versioned(client):
    paths = set(export_openapi.create_app().openapi()["paths"])
    assert paths and all(p.startswith("/v1/") or p == "/health" for p in paths)


def test_no_enum_reference_has_a_default():
    """The dart-dio generator emits code that does not compile for `$ref` plus `default`."""
    schemas = export_openapi.create_app().openapi()["components"]["schemas"]
    offenders = [
        f"{name}.{field}"
        for name, schema in schemas.items()
        for field, prop in schema.get("properties", {}).items()
        if "$ref" in prop and "default" in prop
    ]
    assert offenders == []
