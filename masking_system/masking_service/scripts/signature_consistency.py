"""Modueller arasi cagri/imza uyumu: saf AST, hicbir app/ modulu import edilmez.

TypeError olayi (diagnostics/llm-typeerror-20260928) intranette karisik
surumde kod calismasindan dogdu: bir modul yeni imzayla, onu cagiran modul
eski parametre listesiyle kopyalanmisti. Bu kontrol app/ altindaki her
dosyayi ayristirir ve dar bir kapsamda cagrilari cagrilan fonksiyonun
gercek imzasiyla karsilastirir:

- yalnizca baska bir app/ modulundeki MODUL DUZEYI fonksiyona dogrudan
  cagrilar: `modul.f(...)` (import app.x.y as modul / from app.x import y)
  ve `from app.x.y import f; f(...)`;
- cagrida *args/**kwargs varsa, cagrilan fonksiyon *args/**kwargs aliyorsa
  ya da dekoratorluyse, ad yerel olarak golgeleniyorsa veya cagri dinamikse
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


def _module_name(app_dir: Path, path: Path) -> str:
    parts = list(path.relative_to(app_dir).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join([app_dir.name, *parts])


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> _Signature | None:
    args = node.args
    if node.decorator_list or args.vararg or args.kwarg:
        return None
    positional = [a.arg for a in (*args.posonlyargs, *args.args)]
    with_default = len(args.defaults)
    required = positional[: len(positional) - with_default] if with_default else positional
    kwonly = [a.arg for a in args.kwonlyargs]
    required_kw = [a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults) if default is None]
    return _Signature(tuple(positional), len(args.posonlyargs), frozenset(required), tuple(kwonly),
                      frozenset(required_kw))


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
    bound: set[str] = set()
    open_namespace = False
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[stmt.name] = _signature(stmt)
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
    return _Module(_module_name(app_dir, path), path, tree, functions, bound, open_namespace)


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
        for func, names in scopes:
            for node in ast.walk(func):
                if isinstance(node, ast.Call):
                    shadowed_at.setdefault(id(node), set()).update(names)
        for call in (n for n in ast.walk(module.tree) if isinstance(n, ast.Call)):
            parts = _dotted(call.func)
            if not parts or parts[0] in shadowed_at.get(id(call), set()):
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
    print("RESULT=" + ("OK" if not findings else "FAILED"))
    return int(bool(findings))


if __name__ == "__main__":
    raise SystemExit(main())
