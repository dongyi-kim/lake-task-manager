"""Mechanical Query context extraction must not change its previous behavior."""
import ast
import hashlib
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[3] / "deploy/maxis-dify/code"


def functions(name):
    return {row.name: row for row in ast.parse((DIRECTORY / name).read_text(encoding="utf-8")).body
            if isinstance(row, ast.FunctionDef)}


def digest(body):
    return hashlib.sha256(ast.dump(ast.Module(body=body, type_ignores=[]), include_attributes=False).encode()).hexdigest()


def test_context_body_is_previous_query_input_with_only_parameter_substitution():
    function = functions("core_draft_context.py")["_draft_context"]
    assert [arg.arg for arg in function.args.args] == ["raw", "role_id", "schema_json", "schema_sha256", "draft_schema", "fail"]

    class OriginalNames(ast.NodeTransformer):
        def visit_Name(self, node):
            if node.id == "role_id":
                return ast.Constant(value="query_specialist")
            return ast.Name(id={"schema_json": "_QUERY_SCHEMA_JSON", "schema_sha256": "_QUERY_SCHEMA_SHA256",
                                "draft_schema": "_DRAFT_SCHEMA", "fail": "_fail"}.get(node.id, node.id), ctx=node.ctx)

    restored = OriginalNames().visit(function)
    # Captured from the committed Query implementation before this extraction.
    assert digest(restored.body) == "553b7acaa17d45f1b91d219596c32493f2e7820bd01fb86b183e05f7b2044499"
    assert digest(functions("core_draft_context.py")["_digest"].body) == "99024b88cdb1146503efea3e25d27fde88274ba0badf9023e58e41eb45fbab79"


def test_query_assembly_and_error_body_are_unchanged_and_binding_is_explicit():
    query = functions("core_query_assembler.py")
    assert digest(query["main"].body) == "d3a1a5c0bea733b6b448330926021fd1bf89f38714f8b247a0a1b7ea744f3af1"
    assert digest(query["_fail"].body) == "ed8e61c444d1a28ff820e58d9a55df0379337fb298c9bf808d11e9ce695c225c"
    expected = ast.parse('return _draft_context(raw, "query_specialist", _QUERY_SCHEMA_JSON, _QUERY_SCHEMA_SHA256, _DRAFT_SCHEMA, _fail)').body
    assert ast.dump(ast.Module(body=query["_input"].body, type_ignores=[])) == ast.dump(ast.Module(body=expected, type_ignores=[]))
