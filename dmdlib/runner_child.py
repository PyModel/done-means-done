"""Hold a process-group identity until the parent cleans up every check descendant.

The parent keeps this process's stdin open for the whole run. EOF before the parent's own
cleanup means the parent died (SIGKILL, a host tool timeout): the group is then terminated
here, so a check never keeps running unsupervised and a recovered ledger cannot start a
second concurrent copy of it."""
import os
import signal
import subprocess
import sys
import threading
import time

TERM_GRACE_SECONDS = 2.0


def _orphaned():
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    group = os.getpgid(0)
    try:
        os.killpg(group, signal.SIGTERM)
        time.sleep(TERM_GRACE_SECONDS)
        os.killpg(group, signal.SIGKILL)
    except OSError:
        pass


fd = int(sys.argv[1])
try:
    p = subprocess.Popen([sys.argv[2], "-c", sys.argv[3]], stdin=subprocess.DEVNULL, close_fds=True)
except Exception:
    p = None
# Started after the command so it inherits default signal handling. The supervisor ignores
# the group SIGTERM (the parent always follows with SIGKILL) and watches for its parent.
signal.signal(signal.SIGTERM, signal.SIG_IGN)
threading.Thread(target=_orphaned, daemon=True).start()
try:
    code = p.wait() if p else 127
except Exception:
    code = 127
os.write(fd, str(code).encode())
os.close(fd)
# Close our stream copies: background descendants may still hold theirs. The
# parent enforces its deadline while this process keeps the group identity alive.
os.close(1)
os.close(2)
while True:
    time.sleep(3600)
