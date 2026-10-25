"""Internal stdlib launcher: join the prepared leaf before exact Runner exec."""
import os
from pathlib import Path
import sys


def main():
    try:
        leaf = Path(sys.argv[1])
        argv = sys.argv[2:]
        if len(argv) not in (6, 7) or argv[1] != 'configure':
            raise ValueError('fixed command required')
        if argv[2] != '--catalog-root' or argv[4] != '--catalog-source-id':
            raise ValueError('fixed binding required')
        if len(argv) == 7 and argv[6] != '--catalog':
            raise ValueError('fixed mode required')
        if not leaf.is_absolute() or not Path(argv[0]).is_absolute():
            raise ValueError('absolute binding required')
        (leaf / 'cgroup.procs').write_text(str(os.getpid()))
        # Verify membership through the delegated cgroup.procs, not a guessed
        # cgroup hierarchy string (mount roots may be namespaced).
        if str(os.getpid()) not in (leaf / 'cgroup.procs').read_text().split():
            raise OSError('placement failed')
        os.execve(argv[0], argv, dict(os.environ))
    except Exception:
        # Never expose host paths, arguments, environment or traceback.
        os.write(2, b'configurator launcher refused\n')
        return 125


if __name__ == '__main__':
    sys.exit(main())
