import ast
import glob
import importlib
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')
texts_mod = importlib.import_module("bot.texts")

bot_files = glob.glob('bot/**/*.py', recursive=True) + glob.glob('services/**/*.py', recursive=True)

print("=== 1. CHECKING MISSING TEXTS ATTRIBUTES ===")
missing_texts = []

class TextsVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
    def visit_Attribute(self, node):
        if isinstance(node.value, ast.Name) and node.value.id == 'texts':
            if not hasattr(texts_mod, node.attr):
                missing_texts.append((self.filename, node.lineno, node.attr))
        self.generic_visit(node)

for fpath in bot_files:
    with open(fpath, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=fpath)
            TextsVisitor(fpath).visit(tree)
        except Exception as e:
            print(f"Error parsing {fpath}: {e}")

if missing_texts:
    print("[MISSING TEXTS ATTRIBUTES]:")
    for f, line, attr in missing_texts:
        print(f"  {f}:{line} -> texts.{attr}")
else:
    print("[OK] AST check: 0 missing texts.* attributes across all python files!")


print("\n=== 2. CHECKING CALLBACK DATA MATCHER ===")
emitted_callbacks = [] # (file, line, raw_value)

class CallbackEmitterVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename
    def visit_Call(self, node):
        for kw in node.keywords:
            if kw.arg == 'callback_data':
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    emitted_callbacks.append((self.filename, node.lineno, kw.value.value))
                elif isinstance(kw.value, ast.JoinedStr):
                    prefix = ''
                    for part in kw.value.values:
                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                            prefix += part.value
                        else:
                            prefix += '{...}'
                    emitted_callbacks.append((self.filename, node.lineno, prefix))
                elif isinstance(kw.value, ast.Name):
                    emitted_callbacks.append((self.filename, node.lineno, f"VAR({kw.value.id})"))
                elif isinstance(kw.value, ast.Attribute):
                    emitted_callbacks.append((self.filename, node.lineno, f"ATTR({kw.value.attr})"))
        self.generic_visit(node)

for fpath in bot_files:
    with open(fpath, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=fpath)
            CallbackEmitterVisitor(fpath).visit(tree)
        except Exception:
            pass

# AST Visitor for handlers to catch all F.data filters reliably!
handler_filters = [] # (file, line, type, values)

class HandlerVisitor(ast.NodeVisitor):
    def __init__(self, filename):
        self.filename = filename

    def visit_FunctionDef(self, node):
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node):
        self._check_decorators(node)
        self.generic_visit(node)

    def _check_decorators(self, node):
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                for arg in dec.args:
                    self._parse_filter_expr(arg, node.lineno)

    def _parse_filter_expr(self, expr, lineno):
        if isinstance(expr, ast.Compare):
            # F.data == "..."
            if (isinstance(expr.left, ast.Attribute) and expr.left.attr == 'data' and
                isinstance(expr.left.value, ast.Name) and expr.left.value.id == 'F'):
                for comparator in expr.comparators:
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        handler_filters.append((self.filename, lineno, 'exact', [comparator.value]))
        elif isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            # F.data.startswith(...) or F.data.in_(...)
            fn = expr.func
            if isinstance(fn.value, ast.Attribute) and fn.value.attr == 'data':
                if fn.attr == 'startswith':
                    vals = []
                    for a in expr.args:
                        if isinstance(a, ast.Constant) and isinstance(a.value, str):
                            vals.append(a.value)
                        elif isinstance(a, ast.Tuple) or isinstance(a, ast.List):
                            for elt in a.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    vals.append(elt.value)
                    if vals:
                        handler_filters.append((self.filename, lineno, 'startswith', vals))
                elif fn.attr == 'in_':
                    vals = []
                    for a in expr.args:
                        if isinstance(a, ast.List) or isinstance(a, ast.Set) or isinstance(a, ast.Tuple):
                            for elt in a.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    vals.append(elt.value)
                    if vals:
                        handler_filters.append((self.filename, lineno, 'in', vals))

for fpath in bot_files:
    with open(fpath, 'r', encoding='utf-8') as f:
        try:
            tree = ast.parse(f.read(), filename=fpath)
            HandlerVisitor(fpath).visit(tree)
        except Exception:
            pass

print(f"Total emitted callbacks found: {len(emitted_callbacks)}")
print(f"Total handler filters found: {len(handler_filters)}")

unhandled = []
for fpath, line, cb in emitted_callbacks:
    if cb.startswith("VAR(") or cb.startswith("ATTR("):
        continue
    cb_clean = cb.split('{')[0]
    matched = False
    for h_file, h_line, h_type, h_vals in handler_filters:
        if h_type == 'exact':
            if cb in h_vals or cb_clean in h_vals:
                matched = True
                break
        elif h_type == 'startswith':
            if any(cb_clean.startswith(p) for p in h_vals):
                matched = True
                break
        elif h_type == 'in':
            if cb in h_vals or cb_clean in h_vals:
                matched = True
                break
    if not matched:
        unhandled.append((fpath, line, cb))

if unhandled:
    print("\n[UNHANDLED CALLBACKS EMITTED]:")
    for f, line_no, cb in unhandled:
        print(f"  {f}:{line_no} -> '{cb}'")
else:
    print("\n[OK] Every emitted callback_data has a matching registered handler!")
