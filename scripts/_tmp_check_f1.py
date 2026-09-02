"""临时校验脚本：AST 扫描 _build_doc_metadata 内未定义名称（批次一 F1 验收用）。"""
import ast
import builtins

SRC = "backend/rag/indexing/indexer.py"

src = open(SRC, encoding="utf-8").read()
tree = ast.parse(src)


def find_func(t, name):
    for node in ast.walk(t):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    return None


fn = find_func(tree, "_build_doc_metadata")
assert fn, "function not found"


def collect_bindings(scope):
    bound = set()

    class Binder(ast.NodeVisitor):
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            self.generic_visit(node)

        def visit_arg(self, node):
            bound.add(node.arg)

    Binder().visit(scope)
    for n in ast.walk(scope):
        if isinstance(n, ast.ImportFrom):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.Import):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(n.name)
            for a in n.args.args:
                bound.add(a.arg)
    return bound


module_bound = collect_bindings(tree)  # ast.walk(tree) 已含函数名等
fn_bound = collect_bindings(fn)

used = set()


class Used(ast.NodeVisitor):
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            used.add(node.id)
        self.generic_visit(node)


Used().visit(fn)

allowed = set(dir(builtins)) | fn_bound | module_bound
undefined = sorted(u for u in used - allowed if not u.startswith("__"))
print("unresolved names in _build_doc_metadata:", undefined or "NONE")

# 顺带验证 asyncio 已在模块级导入、enriched 已绑定
print("module has asyncio import:", "asyncio" in module_bound)
print("fn has enriched binding:", "enriched" in fn_bound)
