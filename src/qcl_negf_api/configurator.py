"""One bounded, source-bound Runner child; no scientific parsing or resolution."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import stat
import sys
import tempfile
import time
import uuid

CAPS = {'cpu.max': '100000 100000', 'memory.max': '2147483648',
        'memory.swap.max': '0', 'memory.oom.group': '1', 'pids.max': '64'}
ENV = {'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'JULIA_NUM_THREADS': '1',
       'JULIA_NUM_GC_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
       'OMP_NUM_THREADS': '1', 'JULIA_PKG_OFFLINE': 'true'}
REQUEST_BYTES = 256 * 1024
STDOUT_BYTES = 8 * 1024 * 1024
STDERR_BYTES = 64 * 1024
CATALOG_BYTES = 1024 * 1024


class ConfiguratorError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _json(raw: bytes):
    def nonfinite(value):
        raise ValueError('nonfinite')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object,
                           parse_constant=nonfinite)
        stack, nodes = [(value, 0)], 0
        while stack:
            item, depth = stack.pop()
            nodes += 1
            if depth > 64 or nodes > 100000:
                raise ValueError('tree bound')
            if isinstance(item, str):
                item.encode('utf-8')
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError('nonfinite')
            if isinstance(item, dict):
                stack.extend((x, depth + 1) for x in (*item.keys(), *item.values()))
            elif isinstance(item, list):
                stack.extend((x, depth + 1) for x in item)
        return value
    except (ValueError, RecursionError, UnicodeError, OverflowError) as exc:
        raise ConfiguratorError('invalid_envelope') from exc


def decode_request(raw: bytes, source: str):
    if len(raw) > REQUEST_BYTES:
        raise ConfiguratorError('limit')
    value = _json(raw)
    if not isinstance(value, dict):
        raise ConfiguratorError('invalid_envelope')
    mode = value.get('mode')
    payload = 'fields' if mode == 'form' else 'yaml'
    if (mode not in ('form', 'yaml') or
            set(value) != {'mode', 'template_id', 'catalog_source_id', payload} or
            value.get('catalog_source_id') != source or
            not isinstance(value.get('template_id'), str) or
            not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', value['template_id'])):
        raise ConfiguratorError('invalid_envelope')
    if mode == 'form':
        if not isinstance(value['fields'], dict):
            raise ConfiguratorError('invalid_envelope')
    elif not isinstance(value['yaml'], str):
        raise ConfiguratorError('invalid_envelope')
    elif len(value['yaml'].encode('utf-8')) > 128 * 1024:
        raise ConfiguratorError('limit')
    return value


def decode_response(raw: bytes, source: str, plan_limit: int, catalog: bool):
    if len(raw) > (CATALOG_BYTES if catalog else STDOUT_BYTES):
        raise ConfiguratorError('limit')
    value = _json(raw)
    if not isinstance(value, dict) or value.get('status') not in ('available', 'error'):
        raise ConfiguratorError('invalid_response')
    if value['status'] == 'error':
        # Owner errors remain refusals, but untrusted diagnostics never cross HTTP.
        raise ConfiguratorError('runner_refused')
    if value.get('catalog_source_id') != source:
        raise ConfiguratorError('source_mismatch')
    if catalog:
        if not isinstance(value.get('templates'), list) or not 1 <= len(value['templates']) <= 32:
            raise ConfiguratorError('invalid_response')
    else:
        plan = value.get('plan')
        if not isinstance(plan, str):
            raise ConfiguratorError('invalid_response')
        plan_bytes = plan.encode('utf-8')
        if len(plan_bytes) > min(4 * 1024 * 1024, plan_limit):
            raise ConfiguratorError('limit')
        if hashlib.sha256(plan_bytes).hexdigest() != value.get('plan_sha256'):
            raise ConfiguratorError('invalid_response')
        counts = value.get('counts')
        if not isinstance(counts, dict) or any(
            type(counts.get(k)) is not int or not 0 <= counts[k] <= 100
            for k in ('points', 'executions')
        ):
            raise ConfiguratorError('invalid_response')
    # Runner owns ExactScalar tags. Reject an untagged unsafe JS integer rather
    # than rounding or silently changing the owning DTO. The raw plan is a string.
    stack = [value]
    while stack:
        item = stack.pop()
        if type(item) is int and abs(item) > 9007199254740991:
            raise ConfiguratorError('invalid_response')
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return value


class Cgroup:
    """Operate only in the configured systemd delegated v2 root."""
    def __init__(self, root: str):
        self.root = Path(root)

    def ready(self):
        root = self.root
        if not root.is_absolute() or root.resolve() != root or not root.is_dir():
            raise OSError('invalid root')
        mounts = []
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            before, after = line.split(' - ', 1)
            fields = before.split()
            if after.split()[0] == 'cgroup2' and '\\' not in fields[4]:
                mount = Path(fields[4])
                if root.is_relative_to(mount):
                    mounts.append((mount, fields[3]))
        if len(mounts) != 1:
            raise OSError('not unified v2')
        mount, mount_root = mounts[0]
        expected = str(Path(mount_root) / root.relative_to(mount) / 'api')
        membership = Path('/proc/self/cgroup').read_text().splitlines()
        if membership != ['0::' + expected] or (root / 'cgroup.procs').read_text().strip():
            raise OSError('wrong placement')
        if any(root.glob('configurator-*')):
            raise OSError('stale leaf')
        controllers = set((root / 'cgroup.controllers').read_text().split())
        if not {'cpu', 'memory', 'pids'} <= controllers:
            raise OSError('controllers missing')
        if not os.access(root / 'cgroup.events', os.R_OK):
            raise OSError('missing lifecycle controls')
        control = root / 'cgroup.subtree_control'
        if not os.access(root, os.W_OK) or not os.access(control, os.W_OK):
            raise OSError('not delegated')
        enabled = set(control.read_text().split())
        missing = {'cpu', 'memory', 'pids'} - enabled
        if missing:
            control.write_text(' '.join('+' + x for x in sorted(missing)))
        if not {'cpu', 'memory', 'pids'} <= set(control.read_text().split()):
            raise OSError('enable mismatch')

    def create(self):
        leaf = self.root / ('configurator-' + uuid.uuid4().hex)
        leaf.mkdir()
        try:
            for name, value in CAPS.items():
                path = leaf / name
                path.write_text(value)
                if path.read_text().strip() != value:
                    raise OSError('cap mismatch')
            for name in ('cgroup.kill', 'cgroup.procs'):
                if not os.access(leaf / name, os.W_OK):
                    raise OSError('missing control')
            if not os.access(leaf / 'cgroup.events', os.R_OK):
                raise OSError('missing events')
            return str(leaf)
        except BaseException:
            leaf.rmdir()
            raise

    def kill(self, leaf):
        (Path(leaf) / 'cgroup.kill').write_text('1')

    def empty(self, leaf):
        values = dict(line.split() for line in (Path(leaf) / 'cgroup.events').read_text().splitlines())
        return values.get('populated') == '0'

    def remove(self, leaf):
        Path(leaf).rmdir()


def _killpg(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _shielded(task):
    """Keep ownership of spawn/cleanup across repeated asyncio cancellation."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


class Configurator:
    def __init__(self, settings, *, group=None, spawn=None, killpg=None,
                 wall_seconds=60.0, cleanup_seconds=5.0, quantum_binding=None,
                 memory_admission=None, quantum_decoder=None):
        self.settings = settings
        self.group = group
        self.spawn = spawn or asyncio.create_subprocess_exec
        self.killpg = killpg or _killpg
        self.wall_seconds = wall_seconds
        self.cleanup_seconds = cleanup_seconds
        self.busy = False
        self.poisoned = False
        self.closed = False
        self.active = None
        self.pending_cleanup = None
        self._injected_group = group is not None
        self.memory_snapshot = None
        from .quantum_preview import runtime_context, memory_admission as admit, decode_quantum_response
        self.quantum_binding = quantum_binding or (lambda: runtime_context(settings))
        self.memory_admission = memory_admission or (lambda: admit(settings.configurator_cgroup_root))
        self.quantum_decoder = quantum_decoder or decode_quantum_response

    def quantum_availability(self):
        try:
            if not self.availability()['available']:
                raise ConfiguratorError('unavailable')
            context = self.quantum_binding()
            return {'available': True, 'reason': None, 'runner_source_id': context['runner_source_id'],
                    'core_source_id': context['core_source_id'], 'policy_id': 'quantum-preview-reference-v1',
                    'memory_admission': 'at_request', 'execution_seconds': 180}
        except ConfiguratorError:
            return {'available': False, 'reason': 'runtime_binding_unavailable'}

    async def quantum(self, raw, disconnect=None, *, deadline=None):
        return await self._run(raw, disconnect, quantum=True,
                               deadline=min(time.monotonic()+180, deadline) if deadline is not None else time.monotonic()+180)

    def availability(self):
        source = self.settings.configurator_catalog_source_id
        if self.closed or self.poisoned:
            reason = 'cleanup_unconfirmed' if self.poisoned else 'stopping'
        elif not all((self.settings.configurator_executable,
                      self.settings.configurator_catalog_root, source,
                      self.settings.configurator_cgroup_root)):
            reason = 'unconfigured'
        else:
            try:
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}', source):
                    raise OSError('source')
                if not self._injected_group:
                    executable = Path(self.settings.configurator_executable)
                    catalog = Path(self.settings.configurator_catalog_root)
                    if (not executable.is_absolute() or executable.resolve() != executable or
                        not executable.is_file() or not os.access(executable, os.X_OK) or
                        not catalog.is_absolute() or catalog.resolve() != catalog or not catalog.is_dir()):
                        raise OSError('binding')
                if self.group is None:
                    self.group = Cgroup(self.settings.configurator_cgroup_root)
                # The active leaf is deliberately not mistaken for stale state.
                if not self.busy:
                    self.group.ready()
                reason = None
            except (OSError, ValueError):
                reason = 'runtime_unavailable'
        return {'available': reason is None, 'catalog_source_id': source if reason is None else None,
                'reason': reason}

    async def catalog(self, disconnect=None):
        return await self._run(None, disconnect)

    async def preview(self, raw: bytes, disconnect=None):
        decode_request(raw, self.settings.configurator_catalog_source_id)
        return await self._run(raw, disconnect)

    async def close(self):
        self.closed = True
        task = self.active
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await _shielded(task)
            except (asyncio.CancelledError, ConfiguratorError):
                pass

    async def _run(self, raw, disconnect, *, quantum=False, deadline=None):
        if quantum and (deadline is None or time.monotonic()>=deadline):
            raise ConfiguratorError('timeout')
        if not self.availability()['available']:
            raise ConfiguratorError('unavailable')
        if self.busy:
            raise ConfiguratorError('busy')
        # No await between check and acquisition: event-loop-local try-acquire.
        self.busy = True
        self.active = asyncio.current_task()
        leaf, process, spawn_task = None, None, None
        output_directory = None
        readers, ancillary = [], []
        overflow = asyncio.Event()
        stdout = bytearray()
        stdout_limit = 2*1024*1024 if quantum else CATALOG_BYTES if raw is None else STDOUT_BYTES
        deadline = deadline if quantum else time.monotonic() + self.wall_seconds

        async def read(stream, limit, capture=False):
            seen = 0
            while True:
                chunk = await stream.read(16384)
                if not chunk:
                    return
                seen += len(chunk)
                if seen > limit:
                    overflow.set()
                elif capture:
                    stdout.extend(chunk)
                # Overflow still drains to EOF without retaining additional bytes.

        async def watch_disconnect():
            if disconnect is None:
                await asyncio.Future()
            while not await disconnect():
                await asyncio.sleep(0.05)
            raise ConfiguratorError('disconnected')

        async def cleanup():
            nonlocal process
            failed = False
            # Kill the leaf immediately even if subprocess creation is still
            # resolving; retain spawn ownership and kill again after its handle.
            if leaf is not None:
                try:
                    self.group.kill(leaf)
                except OSError:
                    failed = True
            if spawn_task is not None and process is None:
                try:
                    process = await spawn_task
                except Exception:
                    pass
            if process is not None:
                try:
                    process.stdin.close()
                except (OSError, RuntimeError):
                    failed = True
                try:
                    self.killpg(process.pid)
                except OSError:
                    failed = True
                if leaf is not None:
                    try:
                        self.group.kill(leaf)
                    except OSError:
                        failed = True
                # A spawn cancellation can precede reader creation.
                if not readers:
                    readers.extend([asyncio.create_task(read(process.stdout, stdout_limit)),
                                    asyncio.create_task(read(process.stderr, STDERR_BYTES))])
                await process.wait()
                await asyncio.gather(*readers)
            if leaf is not None:
                while not self.group.empty(leaf):
                    await asyncio.sleep(0.01)
                self.group.remove(leaf)
            if failed:
                raise OSError('cleanup unconfirmed')

        try:
            if quantum:
                from .quantum_preview import decode_quantum_request, RECORD, canonical
                if time.monotonic()>=deadline:
                    raise ConfiguratorError('timeout')
                context = self.quantum_binding()
                if time.monotonic()>=deadline:
                    raise ConfiguratorError('timeout')
                request = decode_quantum_request(raw, context)
                if time.monotonic()>=deadline:
                    raise ConfiguratorError('timeout')
                admitted_at = time.monotonic()
                self.memory_snapshot = self.memory_admission()
                if time.monotonic() >= deadline:
                    raise ConfiguratorError('timeout')
            try:
                leaf = self.group.create()
            except (OSError, ValueError) as exc:
                raise ConfiguratorError('unavailable') from exc
            argv = [self.settings.configurator_executable, 'configure', '--catalog-root',
                    self.settings.configurator_catalog_root, '--catalog-source-id',
                    self.settings.configurator_catalog_source_id]
            if raw is None:
                argv.append('--catalog')
            environment = dict(ENV)
            if quantum:
                output_directory = tempfile.mkdtemp(prefix='qcl-quantum-')
                output_path = str(Path(output_directory)/'preview.json')
                argv = [self.settings.configurator_executable, 'quantum-preview', '--runner-source-id',
                        context['runner_source_id'], '--core-source-id', context['core_source_id'],
                        '--output', output_path]
                environment.update(QCL_NEGF_QUANTUM_RUNTIME_CONTEXT=canonical(context).decode('ascii'),
                    QCL_NEGF_QUANTUM_RUNTIME_CONTEXT_SHA256=context['context_sha256'],
                    QCL_NEGF_QUANTUM_RUNTIME_RELEASE_FILE=RECORD,
                    QCL_NEGF_RELEASE_ID=context['release_id'], QCL_NEGF_SOLVER_EXECUTABLE=context['solver_executable'])
            launcher = str(Path(__file__).with_name('configurator_exec.py'))
            disconnected = asyncio.create_task(watch_disconnect())
            ancillary.append(disconnected)
            if quantum and time.monotonic()>=deadline:
                raise ConfiguratorError('timeout')
            if quantum and time.monotonic()-admitted_at>5:
                raise ConfiguratorError('unavailable')
            spawn_task = asyncio.create_task(self.spawn(
                sys.executable, '-I', launcher, leaf, *argv,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True, close_fds=True,
                env=environment))
            # asyncio.wait never cancels the spawn task: cleanup retains its
            # eventual handle even if cancellation/disconnect wins this race.
            started, _ = await asyncio.wait([spawn_task, disconnected],
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED)
            if not started:
                raise ConfiguratorError('timeout')
            if disconnected in started:
                await disconnected
            process = spawn_task.result()
            readers.extend([asyncio.create_task(read(process.stdout, stdout_limit, True)),
                            asyncio.create_task(read(process.stderr, STDERR_BYTES))])

            async def write():
                if raw is not None:
                    process.stdin.write(raw)
                    await process.stdin.drain()
                process.stdin.close()

            async def finish():
                await write()
                await process.wait()
                await asyncio.shield(asyncio.gather(*readers))

            finished = asyncio.create_task(finish())
            flooded = asyncio.create_task(overflow.wait())
            ancillary.extend([finished, flooded])
            done, _ = await asyncio.wait(ancillary, timeout=max(0, deadline-time.monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise ConfiguratorError('timeout')
            if overflow.is_set():
                raise ConfiguratorError('limit')
            if disconnected in done:
                await disconnected
            await finished
            if process.returncode not in (0, 2):
                raise ConfiguratorError('runner_failed')
            if quantum:
                self.quantum_decoder(bytes(stdout), request, context)
                if process.returncode != 0:
                    raise ConfiguratorError('runner_failed')
                decoded = _json(bytes(stdout))
                if decoded.get('identity', {}).get('request_sha256') != hashlib.sha256(raw).hexdigest():
                    raise ConfiguratorError('invalid_response')
                descriptor=os.open(output_path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                with os.fdopen(descriptor,'rb') as stream:
                    info=os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size>2*1024*1024 or stream.read(2*1024*1024+1)!=bytes(stdout):
                        raise ConfiguratorError('invalid_response')
                if time.monotonic()>=deadline:
                    raise ConfiguratorError('timeout')
                return bytes(stdout)
            result = decode_response(bytes(stdout), self.settings.configurator_catalog_source_id,
                                     self.settings.max_body_bytes - 1024, raw is None)
            if process.returncode != 0:
                raise ConfiguratorError('runner_failed')
            if raw is not None and result.get('draft_sha256') != hashlib.sha256(raw).hexdigest():
                raise ConfiguratorError('invalid_response')
            return result
        except asyncio.TimeoutError as exc:
            raise ConfiguratorError('timeout') from exc
        except (OSError, ValueError, BrokenPipeError) as exc:
            raise ConfiguratorError('runner_failed') from exc
        finally:
            async def bounded_cleanup():
                try:
                    self.pending_cleanup = asyncio.create_task(cleanup())
                    self.pending_cleanup.add_done_callback(
                        lambda task: task.exception() if not task.cancelled() else None)
                    await asyncio.wait_for(asyncio.shield(self.pending_cleanup), self.cleanup_seconds)
                    return True
                except Exception:
                    return False
            clean = await _shielded(asyncio.create_task(bounded_cleanup()))
            for task in ancillary:
                if not task.done():
                    task.cancel()
            if ancillary:
                await _shielded(asyncio.create_task(_settle(ancillary)))
            self.active = None
            if clean:
                if output_directory is not None:
                    try:
                        shutil.rmtree(output_directory)
                    except OSError as error:
                        self.poisoned = True
                        raise ConfiguratorError('cleanup_unconfirmed') from error
                self.busy = False
            else:
                self.poisoned = True
                # Never reuse an unconfirmed leaf or report the preview as success.
                raise ConfiguratorError('cleanup_unconfirmed')


async def _settle(tasks):
    await asyncio.gather(*tasks, return_exceptions=True)
