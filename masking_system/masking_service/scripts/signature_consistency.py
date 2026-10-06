"""Modueller arasi cagri/imza uyumu: saf AST, hicbir app/ modulu import edilmez.

TypeError olayi (diagnostics/llm-typeerror-20260928) intranette karisik
surumde kod calismasindan dogdu: bir modul yeni imzayla, onu cagiran modul
eski parametre listesiyle kopyalanmisti. Bu kontrol app/ altindaki her
dosyayi ayristirir ve dar bir kapsamda cagrilari cagrilan fonksiyonun
gercek imzasiyla karsilastirir:

- baska bir app/ modulundeki fonksiyonlara ve statik olarak sinifi bilinen
  nesnelerin metotlarina cagrilar; dogrudan kurulan nesneler ve bir kez
  atanan yerel nesneler desteklenir;
- cagrida *args/**kwargs varsa, cagrilan fonksiyon *args/**kwargs aliyorsa
  ya da imzayi korudugu bilinmeyen dekorator varsa, ad yerel olarak golgeleniyorsa veya cagri dinamikse
  atlanir (yanlis pozitif yerine kapsam disi).
Cagrilan ad hedef modulde hic tanimli degilse (fonksiyon silinmis/eski
modul) de hata verilir.

Kullanim (masking_service klasorunde):
    python scripts/signature_consistency.py            -> RESULT=OK | RESULT=FAILED
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

SERVICE = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Finding:
    caller: str  # app/ altindaki goreli dosya:satir
    callee: str  # modul.fonksiyon
    reason: str

    def line(self) -> str:
        return f"FAIL stage=signature_consistency caller={self.caller} callee={self.callee} reason={self.reason}"


@dataclass(frozen=True)
class _Signature:
    positional: tuple[str, ...]  # posonly + normal, sirali
    posonly: int
    required_positional: frozenset[str]
    kwonly: tuple[str, ...]
    required_kwonly: frozenset[str]


@dataclass
class _Module:
    name: str
    path: Path
    tree: ast.Module
    functions: dict[str, _Signature | None]  # None: imzasi kontrol edilemez (dekorator, *args)
    bound: set[str]  # modul duzeyinde baglanan her ad
    open_namespace: bool  # `from x import *` ya da modul __getattr__
    classes: dict[str, dict[str, _Signature | None]]
    unknown_decorators: set[str]


def _module_name(app_dir: Path, path: Path) -> str:
    parts = list(path.relative_to(app_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([app_dir.name, *parts])


def _signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    transparent: frozenset[str] = frozenset(), *, bound_method: bool = False,
) -> _Signature | None:
    args = node.args
    decorators = {_decorator_name(d) for d in node.decorator_list}
    allowed = transparent | ({"staticmethod", "classmethod"} if bound_method else set())
    if not decorators <= allowed or args.vararg or args.kwarg:
        return None
    positional = [a.arg for a in (*args.posonlyargs, *args.args)]
    with_default = len(args.defaults)
    required = positional[: len(positional) - with_default] if with_default else positional
    posonly = len(args.posonlyargs)
    if bound_method and "staticmethod" not in decorators:
        if not positional:
            return None
        required = [name for name in required if name != positional[0]]
        positional = positional[1:]
        posonly = max(0, posonly - 1)
    kwonly = [a.arg for a in args.kwonlyargs]
    required_kw = [a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None]
    return _Signature(tuple(positional), posonly, frozenset(required), tuple(kwonly),
                      frozenset(required_kw))


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        if node.args or node.keywords:
            return "<dynamic>"
        node = node.func
    return ".".join(_dotted(node) or ["<dynamic>"])


def _transparent_decorators(tree: ast.Module) -> frozenset[str]:
    # Resolve imports, rather than trusting any decorator with the same name.
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "app.services.llm_transport":
            names.update(a.asname or a.name for a in node.names if a.name == "llm_http_scope")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.services.llm_transport":
                    names.add(f"{alias.asname or alias.name}.llm_http_scope")
    # A local reassignment/function may replace the imported decorator.
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.discard(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            names.difference_update(_stored_names(node))
    return frozenset(names)


def _top_level_statements(body):
    # if/try bloklarindaki modul duzeyi baglamalar da sayilir (ad tanimli mi?).
    for stmt in body:
        yield stmt
        for attr in ("body", "orelse", "finalbody"):
            if isinstance(stmt, (ast.If, ast.Try)) and getattr(stmt, attr, None):
                yield from _top_level_statements(getattr(stmt, attr))
        if isinstance(stmt, ast.Try):
            for handler in stmt.handlers:
                yield from _top_level_statements(handler.body)


def _stored_names(node) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def _load_module(app_dir: Path, path: Path) -> _Module:
    tree = ast.parse(path.read_bytes(), filename=str(path))
    functions: dict[str, _Signature | None] = {}
    classes: dict[str, dict[str, _Signature | None]] = {}
    unknown_decorators: set[str] = set()
    transparent = _transparent_decorators(tree)
    bound: set[str] = set()
    open_namespace = False
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[stmt.name] = _signature(stmt, transparent)
            if any(_decorator_name(d) not in transparent for d in stmt.decorator_list):
                unknown_decorators.add(stmt.name)
        elif isinstance(stmt, ast.ClassDef):
            methods = {}
            for method in stmt.body:
                if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods[method.name] = _signature(method, transparent, bound_method=True)
                    if any(_decorator_name(d) not in transparent | {"staticmethod", "classmethod"}
                           for d in method.decorator_list):
                        unknown_decorators.add(f"{stmt.name}.{method.name}")
            classes[stmt.name] = methods
    for stmt in _top_level_statements(tree.body):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(stmt.name)
            if stmt.name == "__getattr__":
                open_namespace = True
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                if alias.name == "*":
                    open_namespace = True
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For, ast.With)):
            bound |= _stored_names(stmt)
    # Ayni ad modul duzeyinde def disinda da baglaniyorsa imza belirsizdir.
    for stmt in _top_level_statements(tree.body):
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            for name in _stored_names(stmt) & functions.keys():
                functions[name] = None
    return _Module(_module_name(app_dir, path), path, tree, functions, bound, open_namespace,
                   classes, unknown_decorators)


def _resolve_relative(module: _Module, level: int, target: str | None) -> str:
    package = module.name.split(".")
    if module.path.name != "__init__.py":
        package = package[:-1]
    base = package[: len(package) - (level - 1)] if level > 1 else package
    return ".".join([*base, target] if target else base)


def _aliases(module: _Module, modules: dict[str, _Module], root: str):
    module_aliases: dict[str, str] = {}
    function_aliases: dict[str, tuple[str, str]] = {}
    for stmt in _top_level_statements(module.tree.body):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if alias.name in modules and alias.asname:
                    module_aliases[alias.asname] = alias.name
                elif alias.name.split(".")[0] == root and not alias.asname:
                    module_aliases[root] = root
        elif isinstance(stmt, ast.ImportFrom):
            source = _resolve_relative(module, stmt.level, stmt.module) if stmt.level else stmt.module
            if not source or source.split(".")[0] != root:
                continue
            for alias in stmt.names:
                local = alias.asname or alias.name
                if f"{source}.{alias.name}" in modules:
                    module_aliases[local] = f"{source}.{alias.name}"
                elif source in modules:
                    function_aliases[local] = (source, alias.name)
    # Modul duzeyinde yeniden baglanan adlar artik import'u gostermez.
    rebound = set()
    for stmt in module.tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(stmt.name)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            rebound |= _stored_names(stmt)
    for name in rebound:
        module_aliases.pop(name, None)
        function_aliases.pop(name, None)
    return module_aliases, function_aliases


def _dotted(node) -> list[str] | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return parts[::-1]


def _function_locals(func) -> set[str]:
    args = func.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    names |= {a.arg for a in (args.vararg, args.kwarg) if a is not None}
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not func:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def _check_call(call: ast.Call, sig: _Signature) -> str | None:
    if any(isinstance(arg, ast.Starred) for arg in call.args) or any(kw.arg is None for kw in call.keywords):
        return None
    npos = len(call.args)
    keywords = [kw.arg for kw in call.keywords]
    if npos > len(sig.positional):
        return f"fazla_positional({npos}>{len(sig.positional)})"
    allowed = set(sig.positional[sig.posonly:]) | set(sig.kwonly)
    unknown = [kw for kw in keywords if kw not in allowed]
    if unknown:
        return "bilinmeyen_keyword(" + ",".join(unknown) + ")"
    duplicated = [kw for kw in keywords if kw in sig.positional[:npos]]
    if duplicated:
        return "iki_kez_verilen(" + ",".join(duplicated) + ")"
    missing = [name for name in sig.positional[npos:] if name in sig.required_positional and name not in keywords]
    missing += [name for name in sig.kwonly if name in sig.required_kwonly and name not in keywords]
    if missing:
        return "eksik_parametre(" + ",".join(missing) + ")"
    return None


def _class_reference(expr, module_aliases, function_aliases, modules, shadowed):
    parts = _dotted(expr)
    if not parts or parts[0] in shadowed:
        return None
    if len(parts) == 1 and parts[0] in function_aliases:
        source, name = function_aliases[parts[0]]
    elif len(parts) >= 2 and parts[0] in module_aliases:
        source = ".".join([module_aliases[parts[0]], *parts[1:-1]])
        name = parts[-1]
    else:
        return None
    if source in modules and name in modules[source].classes:
        return source, name
    return None


def _scope_nodes(scope):
    yield scope
    for child in ast.iter_child_nodes(scope):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _scope_nodes(child)


def _known_receivers(scope, module_aliases, function_aliases, modules, shadowed):
    nodes = list(_scope_nodes(scope))
    counts: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            counts[node.id] = counts.get(node.id, 0) + 1
    receivers = {}
    for node in nodes:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        else:
            continue
        if isinstance(target, ast.Name) and counts.get(target.id) == 1 and isinstance(value, ast.Call):
            reference = _class_reference(value.func, module_aliases, function_aliases, modules, shadowed)
            if reference is not None:
                receivers[target.id] = reference
    return receivers


def check(app_dir: Path = SERVICE / "app", stats: dict[str, int] | None = None) -> list[Finding]:
    app_dir = app_dir.resolve()
    root = app_dir.name
    modules: dict[str, _Module] = {}
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = _load_module(app_dir, path)
        modules[module.name] = module

    findings: list[Finding] = []
    for module in modules.values():
        module_aliases, function_aliases = _aliases(module, modules, root)
        scopes: list[tuple[ast.AST, set[str]]] = [
            (node, _function_locals(node)) for node in ast.walk(module.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and hasattr(node, "args")
            and not isinstance(node, ast.Lambda)
        ]
        shadowed_at: dict[int, set[str]] = {}
        receiver_at: dict[int, dict[str, tuple[str, str]]] = {}
        for func, names in scopes:
            receivers = _known_receivers(func, module_aliases, function_aliases, modules, names)
            for node in _scope_nodes(func):
                if isinstance(node, ast.Call):
                    receiver_at[id(node)] = receivers
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    shadowed_at.setdefault(id(node), set()).update(names)
        for call in (n for n in ast.walk(module.tree) if isinstance(n, ast.Call)):
            shadowed = shadowed_at.get(id(call), set())
            method_target = None
            if isinstance(call.func, ast.Attribute):
                receiver = call.func.value
                if isinstance(receiver, ast.Call):
                    method_target = _class_reference(receiver.func, module_aliases, function_aliases, modules, shadowed)
                elif isinstance(receiver, ast.Name):
                    method_target = receiver_at.get(id(call), {}).get(receiver.id)
            if method_target is not None:
                target_module, class_name = method_target
                if target_module == module.name:
                    continue
                function = f"{class_name}.{call.func.attr}"
                target = modules[target_module]
                sig = target.classes[class_name].get(call.func.attr)
                # Inherited/dynamic methods are deliberately outside this check.
                if sig is None:
                    if stats is not None and function in target.unknown_decorators:
                        stats["skipped_decorated"] = stats.get("skipped_decorated", 0) + 1
                    continue
                if stats is not None:
                    stats["checked"] = stats.get("checked", 0) + 1
                    stats["checked_methods"] = stats.get("checked_methods", 0) + 1
                reason = _check_call(call, sig)
                if reason:
                    caller = f"{module.path.relative_to(app_dir.parent).as_posix()}:{call.lineno}"
                    findings.append(Finding(caller, f"{target_module}.{function}", reason))
                continue
            parts = _dotted(call.func)
            if not parts or parts[0] in shadowed:
                continue
            if len(parts) == 1 and parts[0] in function_aliases:
                target_module, function = function_aliases[parts[0]]
            elif len(parts) >= 2 and parts[0] in module_aliases:
                target_module = ".".join([module_aliases[parts[0]], *parts[1:-1]])
                function = parts[-1]
                if target_module not in modules:
                    continue  # sinif/nesne uzerinden cagri: kapsam disi
            else:
                continue
            if target_module == module.name:
                continue
            target = modules[target_module]
            caller = f"{module.path.relative_to(app_dir.parent).as_posix()}:{call.lineno}"
            if function not in target.bound and not target.open_namespace:
                findings.append(Finding(caller, f"{target_module}.{function}", "tanimsiz"))
                continue
            sig = target.functions.get(function)
            if sig is None:
                if stats is not None and function in target.unknown_decorators:
                    stats["skipped_decorated"] = stats.get("skipped_decorated", 0) + 1
                continue
            if stats is not None:
                stats["checked"] = stats.get("checked", 0) + 1
            reason = _check_call(call, sig)
            if reason:
                findings.append(Finding(caller, f"{target_module}.{function}", reason))
    return findings


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-dir", type=Path, default=SERVICE / "app")
    args = parser.parse_args(argv)
    stats: dict[str, int] = {}
    findings = check(args.app_dir, stats)
    for finding in findings:
        print(finding.line())
    print(f"checked_calls={stats.get('checked', 0)}")
    print(f"checked_methods={stats.get('checked_methods', 0)} skipped_decorated_calls={stats.get('skipped_decorated', 0)}")
    print("RESULT=" + ("OK" if not findings else "FAILED"))
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
