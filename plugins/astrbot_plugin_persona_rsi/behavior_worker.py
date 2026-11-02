"""One-shot pure-Python behavior worker; no AstrBot imports or chat side effects."""
import ast
import builtins
import json
import sys
import os

BUILTINS = ('len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
            'min', 'max', 'sum', 'any', 'all', 'sorted', 'reversed', 'enumerate',
            'zip', 'range', 'abs', 'round', 'isinstance')
METHODS = {'get', 'items', 'keys', 'values', 'append', 'extend', 'copy', 'strip', 'lower',
           'casefold', 'startswith', 'endswith', 'split', 'join', 'replace', 'find', 'count', 'index', 'sort'}


def validate_source(source):
    if not isinstance(source, str) or not 1 <= len(source) <= 24000:
        raise ValueError('Behavior source must be 1..24000 characters')
    tree = ast.parse(source, filename='behavior.py')
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if not {'plan_request', 'plan_response'} <= funcs:
        raise ValueError('Missing behavior entry points')
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if not isinstance(node, ast.FunctionDef) or node.decorator_list or node.returns or node.args.defaults or node.args.kw_defaults:
            raise ValueError('Only plain function definitions belong in behavior.py')
    forbidden = (ast.Import, ast.ImportFrom, ast.ClassDef, ast.Global, ast.Nonlocal, ast.With,
                 ast.AsyncWith, ast.AsyncFunctionDef, ast.Await, ast.Yield, ast.YieldFrom,
                 ast.Delete, ast.While, ast.Pow)
    for node in ast.walk(tree):
        if isinstance(node, forbidden):
            raise ValueError('Unsupported behavior operation: ' + type(node).__name__)
        if isinstance(node, (ast.Name, ast.arg)) and (node.id if isinstance(node, ast.Name) else node.arg).startswith('_'):
            raise ValueError('Private names are not behavior inputs')
        if isinstance(node, ast.Attribute) and node.attr not in METHODS:
            raise ValueError('Unsupported behavior method: ' + node.attr)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in set(BUILTINS) | funcs:
                    raise ValueError('Unsupported behavior call: ' + node.func.id)
            elif not isinstance(node.func, ast.Attribute):
                raise ValueError('Indirect calls are not supported')
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and abs(node.value) > 1000000:
            raise ValueError('Excessive numeric literal')
    compile(tree, 'behavior.py', 'exec')
    return tree


def memory_limit():
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', wintypes.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                    ('process_limit', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Limits(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                    ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    limits = Limits()
    limits.basic.flags = 0x100
    limits.process_memory = 384 * 1024 * 1024
    if not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError(ctypes.get_last_error(), 'Behavior process memory limit failed')
    return job


def main():
    job_handle = memory_limit()
    data = json.loads(sys.stdin.buffer.read(256000).decode('utf-8'))
    tree = validate_source(data['source'])
    namespace = {'__builtins__': {name: getattr(builtins, name) for name in BUILTINS}}
    exec(compile(tree, 'behavior.py', 'exec'), namespace)
    outputs = [namespace[job['function']](*job['args']) for job in data['jobs']]
    sys.stdout.buffer.write(json.dumps(outputs, ensure_ascii=False, allow_nan=False).encode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        sys.stderr.write(type(exc).__name__ + ': ' + str(exc)[:300])
        sys.exit(1)
