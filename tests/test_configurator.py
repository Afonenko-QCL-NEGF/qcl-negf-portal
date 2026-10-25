"""Synthetic process/cgroup controls; no Julia or live cgroup required."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from qcl_negf_api.configurator import (
    CAPS, Configurator, ConfiguratorError, decode_request, decode_response,
)

RAW_PLAN = '{ "integer": 9007199254740993, "decimal": 1.234567890123456789 }\n'
REQUEST = b'{ "mode":"yaml", "template_id":"one", "catalog_source_id":"source", "yaml":"schema: example\\n" }'


def settings():
    return SimpleNamespace(configurator_executable='/nix/store/example/bin/qcl-negf',
        configurator_catalog_root='/nix/store/research',
        configurator_catalog_source_id='source', configurator_cgroup_root='/delegated',
        max_body_bytes=2000000)


class Group:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail
        self.path = '/delegated/configurator-fake'

    def ready(self):
        if self.fail:
            raise OSError('private host path')

    def create(self):
        self.events.append(('caps', dict(CAPS)))
        return self.path

    def kill(self, leaf):
        self.events.append('kill')

    def empty(self, leaf):
        return True

    def remove(self, leaf):
        self.events.append('remove')


class Input:
    def __init__(self):
        self.raw = b''
    def write(self, data):
        self.raw += data
    async def drain(self):
        pass
    def close(self):
        pass


class Process:
    pid = 987654321  # Synthetic PID; killpg is separately injected.
    def __init__(self, out=b'', err=b'', wait=None):
        self.stdin = Input()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stdout.feed_data(out)
        self.stderr.feed_data(err)
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.returncode = 0
        self.wait_event = wait
        self.waited = False
    async def wait(self):
        if self.wait_event:
            await self.wait_event.wait()
        self.waited = True
        return self.returncode


def response():
    return json.dumps({'status':'available', 'catalog_source_id':'source',
        'draft_sha256':hashlib.sha256(REQUEST).hexdigest(),
        'plan':RAW_PLAN, 'plan_sha256':hashlib.sha256(RAW_PLAN.encode()).hexdigest(),
        'counts':{'points':1,'executions':1},
        'used':{'kind':'integer','decimal':'9007199254740993'}}).encode()


def test_request_envelope_rejects_duplicate_nonfinite_and_foreign_keys():
    for raw in (b'{"mode":"form","mode":"yaml"}', b'{"n":NaN}',
                b'{"mode":"form","template_id":"x","catalog_source_id":"source","fields":{},"env":{}}'):
        with pytest.raises(ConfiguratorError):
            decode_request(raw, 'source')
    assert decode_request(REQUEST, 'source')['yaml'].endswith('\n')


def test_response_preserves_plan_and_tagged_integer():
    result = decode_response(response(), 'source', 1998976, False)
    assert result['plan'] == RAW_PLAN
    assert result['used'] == {'kind':'integer','decimal':'9007199254740993'}
    bad = json.loads(response())
    bad['plan_sha256'] = '0' * 64
    with pytest.raises(ConfiguratorError):
        decode_response(json.dumps(bad).encode(), 'source', 1998976, False)


def test_literal_resource_contract():
    # Independent literal oracle, not a self-comparison with implementation constants.
    assert CAPS == {'cpu.max':'100000 100000','memory.max':'2147483648',
        'memory.swap.max':'0','memory.oom.group':'1','pids.max':'64'}


def test_missing_readiness_zero_spawn():
    async def case():
        calls = []
        async def spawn(*args, **kwargs):
            calls.append(args)
        adapter = Configurator(settings(), group=Group(True), spawn=spawn)
        assert adapter.availability()['available'] is False
        with pytest.raises(ConfiguratorError) as error:
            await adapter.catalog()
        assert error.value.code == 'unavailable'
        assert calls == []
    asyncio.run(case())


def test_exact_stdin_fixed_argv_environment_cleanup():
    async def case():
        group, process, calls = Group(), Process(response()), []
        async def spawn(*args, **kwargs):
            assert group.events[0][0] == 'caps'
            calls.append((args, kwargs))
            return process
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None)
        result = await adapter.preview(REQUEST)
        assert result['plan'] == RAW_PLAN
        assert process.stdin.raw == REQUEST
        args, kw = calls[0]
        assert args[-6:] == ('/nix/store/example/bin/qcl-negf','configure',
            '--catalog-root','/nix/store/research','--catalog-source-id','source')
        assert kw['start_new_session'] is True and kw['close_fds'] is True
        assert kw['env'] == {'LANG':'C.UTF-8','LC_ALL':'C.UTF-8',
            'JULIA_NUM_THREADS':'1','JULIA_NUM_GC_THREADS':'1',
            'OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','JULIA_PKG_OFFLINE':'true'}
        assert group.events[-1] == 'remove' and 'kill' in group.events and process.waited
        assert not adapter.busy
    asyncio.run(case())


@pytest.mark.parametrize('which', ['stdout','stderr'])
def test_independent_stream_overflow_cleans_before_reuse(which):
    async def case():
        process = Process(b'x'*(8*1024*1024+1) if which=='stdout' else response(),
                          b'x'*(64*1024+1) if which=='stderr' else b'')
        group = Group()
        async def spawn(*args, **kwargs):
            return process
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None)
        with pytest.raises(ConfiguratorError) as error:
            await adapter.catalog()
        assert error.value.code == 'limit'
        assert process.waited and group.events[-1] == 'remove' and not adapter.busy
    asyncio.run(case())


def test_shared_busy_gate_and_spawn_cancellation_reaps_orphan():
    async def case():
        spawned, release = asyncio.Event(), asyncio.Event()
        group, process = Group(), Process(response())
        async def spawn(*args, **kwargs):
            spawned.set()
            await release.wait()
            return process
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None)
        task = asyncio.create_task(adapter.preview(REQUEST))
        await spawned.wait()
        with pytest.raises(ConfiguratorError) as error:
            await adapter.catalog()
        assert error.value.code == 'busy'
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.waited and group.events[-1] == 'remove' and not adapter.busy
    asyncio.run(case())


def test_unconfirmed_empty_poison_keeps_gate_closed():
    async def case():
        group = Group()
        group.empty = lambda leaf: False
        async def spawn(*args, **kwargs):
            return Process(response())
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None,
                               cleanup_seconds=0.01)
        with pytest.raises(ConfiguratorError):
            await adapter.catalog()
        assert not adapter.availability()['available'] and adapter.busy
    asyncio.run(case())


@pytest.mark.parametrize('trigger', ['timeout', 'disconnect', 'shutdown'])
def test_lifetime_triggers_kill_reap_eof_before_unlock(trigger):
    async def case():
        ready, killed = asyncio.Event(), asyncio.Event()
        group = Group()
        process = Process(response(), wait=killed)
        original_kill = group.kill
        def kill(leaf):
            original_kill(leaf)
            killed.set()
        group.kill = kill
        async def spawn(*args, **kwargs):
            ready.set()
            return process
        async def disconnected():
            return True
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None,
                               wall_seconds=0.01 if trigger=='timeout' else 60)
        task = asyncio.create_task(adapter.preview(REQUEST,
            disconnected if trigger=='disconnect' else None))
        await ready.wait()
        if trigger == 'shutdown':
            await adapter.close()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ConfiguratorError) as error:
                await task
            assert error.value.code == trigger.replace('disconnect','disconnected')
        assert process.waited and group.events[-1] == 'remove' and not adapter.busy
    asyncio.run(case())


def test_descendants_hold_gate_until_empty_and_leaf_removal():
    async def case():
        group, process = Group(), Process(response())
        observed, empty = asyncio.Event(), asyncio.Event()
        def populated(leaf):
            observed.set()
            return empty.is_set()
        group.empty = populated
        async def spawn(*args, **kwargs):
            return process
        adapter = Configurator(settings(), group=group, spawn=spawn, killpg=lambda pid:None)
        task = asyncio.create_task(adapter.preview(REQUEST))
        await observed.wait()
        assert adapter.busy and 'remove' not in group.events
        with pytest.raises(ConfiguratorError) as error:
            await adapter.catalog()
        assert error.value.code == 'busy'
        empty.set()
        assert (await task)['plan'] == RAW_PLAN
        assert not adapter.busy and group.events[-1] == 'remove'
    asyncio.run(case())


def test_launcher_places_self_before_exact_exec(tmp_path, monkeypatch):
    from qcl_negf_api import configurator_exec
    leaf = tmp_path / 'configurator-synthetic'
    leaf.mkdir()
    (leaf / 'cgroup.procs').write_text('')
    argv = ['/nix/store/example/bin/qcl-negf','configure','--catalog-root',
            '/nix/store/research','--catalog-source-id','source']
    monkeypatch.setattr(configurator_exec.sys, 'argv', ['launcher',str(leaf),*argv])
    monkeypatch.setattr(configurator_exec.os, 'getpid', lambda:123)
    captured = []
    def execve(executable, command, env):
        assert (leaf / 'cgroup.procs').read_text() == '123'
        captured.append((executable,command))
    monkeypatch.setattr(configurator_exec.os, 'execve', execve)
    configurator_exec.main()
    assert captured == [(argv[0],argv)]


def test_v2_preflight_and_cap_readback_refusals(tmp_path, monkeypatch):
    from pathlib import Path
    from qcl_negf_api.configurator import Cgroup
    root = tmp_path / 'delegated'
    root.mkdir()
    for name, content in {'cgroup.procs':'', 'cgroup.controllers':'cpu memory pids',
                          'cgroup.subtree_control':'cpu memory pids',
                          'cgroup.kill':'', 'cgroup.events':'populated 1'}.items():
        (root / name).write_text(content)
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return f'1 0 0:1 / {tmp_path} rw - cgroup2 cgroup rw\n'
        if str(path) == '/proc/self/cgroup':
            return '0::/delegated/api\n'
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',read)
    group = Cgroup(str(root))
    group.ready()
    (root / 'cgroup.controllers').write_text('cpu memory')
    with pytest.raises(OSError):
        group.ready()
    (root / 'cgroup.controllers').write_text('cpu memory pids')
    (root / 'cgroup.procs').write_text('123')
    with pytest.raises(OSError):
        group.ready()
    (root / 'cgroup.procs').write_text('')
    monkeypatch.setattr('qcl_negf_api.configurator.os.access',lambda path,mode:False)
    with pytest.raises(OSError):
        group.ready()


def test_cap_mismatch_zero_spawn():
    class Mismatch(Group):
        def create(self):
            raise OSError('readback mismatch')
    async def case():
        spawned = []
        async def spawn(*args, **kwargs):
            spawned.append(args)
        adapter = Configurator(settings(),group=Mismatch(),spawn=spawn)
        with pytest.raises(ConfiguratorError) as error:
            await adapter.preview(REQUEST)
        assert error.value.code == 'unavailable' and spawned == []
    asyncio.run(case())


def test_real_control_adapter_writes_reads_every_literal_cap(tmp_path, monkeypatch):
    from pathlib import Path
    from qcl_negf_api.configurator import Cgroup
    root = tmp_path / 'delegated'
    root.mkdir()
    original_mkdir = Path.mkdir
    original_read = Path.read_text
    reads = []
    def mkdir(path, *args, **kwargs):
        original_mkdir(path,*args,**kwargs)
        if path.name.startswith('configurator-'):
            for control in ('cgroup.kill','cgroup.procs','cgroup.events'):
                (path / control).write_text('populated 0' if control=='cgroup.events' else '')
    def read(path, *args, **kwargs):
        reads.append(path.name)
        return original_read(path,*args,**kwargs)
    monkeypatch.setattr(Path,'mkdir',mkdir)
    monkeypatch.setattr(Path,'read_text',read)
    leaf = Path(Cgroup(str(root)).create())
    expected = {'cpu.max':'100000 100000','memory.max':'2147483648',
                'memory.swap.max':'0','memory.oom.group':'1','pids.max':'64'}
    assert leaf.parent == root and leaf.name.startswith('configurator-')
    assert set(expected) <= set(reads)
    for name, value in expected.items():
        assert original_read(leaf/name) == value


def test_surrogate_and_exponent_overflow_refused_without_process():
    for raw in (b'{"mode":"yaml","template_id":"one","catalog_source_id":"source","yaml":"\\ud800"}',
                b'{"n":1e999}'):
        with pytest.raises(ConfiguratorError):
            decode_request(raw,'source')


def test_response_plan_cap_is_advertised_api_cap_and_unsafe_dto_refused():
    with pytest.raises(ConfiguratorError) as error:
        decode_response(response(),'source',1,False)
    assert error.value.code == 'limit'
    unsafe = json.loads(response())
    unsafe['used'] = 9007199254740993
    with pytest.raises(ConfiguratorError):
        decode_response(json.dumps(unsafe).encode(),'source',1998976,False)


def test_disconnect_during_spawn_retains_handle_and_blocks_reuse():
    async def case():
        spawned, release, saw_disconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()
        group, process = Group(), Process(response())
        async def spawn(*args, **kwargs):
            spawned.set()
            await release.wait()
            return process
        async def disconnected():
            saw_disconnect.set()
            return True
        adapter = Configurator(settings(),group=group,spawn=spawn,killpg=lambda pid:None)
        task = asyncio.create_task(adapter.preview(REQUEST,disconnected))
        await spawned.wait()
        await saw_disconnect.wait()
        # Give the ready watcher a turn; cleanup must own the unresolved spawn.
        await asyncio.sleep(0)
        assert adapter.busy and not task.done()
        release.set()
        with pytest.raises(ConfiguratorError) as error:
            await task
        assert error.value.code == 'disconnected'
        assert process.waited and group.events[-1] == 'remove' and not adapter.busy
    asyncio.run(case())


def test_ordinary_delegation_parent_kill_unwritable_but_leaf_kill_required(tmp_path, monkeypatch):
    from pathlib import Path
    from qcl_negf_api.configurator import Cgroup
    root = tmp_path / 'delegated'
    root.mkdir()
    for name, content in {'cgroup.procs':'','cgroup.controllers':'cpu memory pids',
                          'cgroup.subtree_control':'cpu memory pids',
                          'cgroup.events':'populated 1','cgroup.kill':''}.items():
        (root/name).write_text(content)
    original_read, original_mkdir, original_rmdir = Path.read_text, Path.mkdir, Path.rmdir
    leaf_kill_writable = True
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return f'1 0 0:1 / {tmp_path} rw - cgroup2 cgroup rw\n'
        if str(path) == '/proc/self/cgroup':
            return '0::/delegated/api\n'
        return original_read(path,*args,**kwargs)
    def access(path, mode):
        path = Path(path)
        if path == root/'cgroup.kill':
            return False  # Ordinary manager-owned delegated parent control.
        if path.name == 'cgroup.kill' and path.parent.name.startswith('configurator-'):
            return leaf_kill_writable
        return True
    def mkdir(path, *args, **kwargs):
        original_mkdir(path,*args,**kwargs)
        if path.name.startswith('configurator-'):
            for control in ('cgroup.kill','cgroup.procs','cgroup.events'):
                (path/control).write_text('populated 0' if control=='cgroup.events' else '')
    def rmdir(path):
        # Kernel cgroup pseudo-files disappear with rmdir, unlike tmp files.
        if path.name.startswith('configurator-'):
            for control in path.iterdir():
                control.unlink()
        original_rmdir(path)
    monkeypatch.setattr(Path,'read_text',read)
    monkeypatch.setattr(Path,'mkdir',mkdir)
    monkeypatch.setattr(Path,'rmdir',rmdir)
    monkeypatch.setattr('qcl_negf_api.configurator.os.access',access)
    group = Cgroup(str(root))
    group.ready()  # Must not require parent cgroup.kill write permission.
    leaf = group.create()
    group.remove(leaf)
    leaf_kill_writable = False
    async def case():
        spawned = []
        async def spawn(*args, **kwargs):
            spawned.append(args)
        adapter = Configurator(settings(),group=group,spawn=spawn)
        with pytest.raises(ConfiguratorError) as error:
            await adapter.catalog()
        assert error.value.code == 'unavailable' and spawned == []
    asyncio.run(case())


def test_live_catalog_one_mib_plus_one_stall_refused_but_preview_cap_unchanged():
    async def case():
        killed = asyncio.Event()
        group = Group()
        process = Process(wait=killed)
        process.stdout = asyncio.StreamReader()
        process.stdout.feed_data(b'x' * (1024*1024+1))
        # Neither process wait nor stdout EOF completes until cleanup kill.
        def kill(leaf):
            group.events.append('kill')
            if not killed.is_set():
                process.stdout.feed_eof()
            killed.set()
        group.kill = kill
        async def spawn(*args, **kwargs):
            return process
        adapter = Configurator(settings(),group=group,spawn=spawn,killpg=lambda pid:None)
        with pytest.raises(ConfiguratorError) as error:
            await asyncio.wait_for(adapter.catalog(),0.25)
        assert error.value.code == 'limit'
        assert killed.is_set() and process.waited
        assert group.events[-1] == 'remove' and not adapter.busy

        # A valid preview larger than the catalog cap must retain the 8MiB cap.
        payload = json.loads(response())
        payload['padding'] = 'x' * (1024*1024+1)
        preview_process = Process(json.dumps(payload).encode())
        async def preview_spawn(*args, **kwargs):
            return preview_process
        preview = Configurator(settings(),group=Group(),spawn=preview_spawn,
                               killpg=lambda pid:None)
        assert (await preview.preview(REQUEST))['plan'] == RAW_PLAN
        assert preview_process.waited and not preview.busy
    asyncio.run(case())
