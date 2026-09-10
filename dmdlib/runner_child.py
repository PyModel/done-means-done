"""Hold a process-group identity until the parent cleans up every check descendant."""
import os
import subprocess
import sys

fd = int(sys.argv[1])
try:
    p = subprocess.Popen([sys.argv[2], "-c", sys.argv[3]], stdin=subprocess.DEVNULL, close_fds=True)
    code = p.wait()
except Exception:
    code = 127
os.write(fd, str(code).encode())
os.close(fd)
# Close our stream copies: background descendants may still hold theirs. The
# parent enforces its deadline while this process keeps the group identity alive.
os.close(1)
os.close(2)
try:
    sys.stdin.buffer.read()
except Exception:
    pass
