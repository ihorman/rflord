#!/usr/bin/env python3
"""Diagnostic: test which key-reading method works on this terminal."""
import curses, os, select, sys, time

def main(stdscr):
    curses.cbreak()
    curses.noecho()
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.nodelay(True)
    stdscr.timeout(100)
    
    log = open('/tmp/key_test.log', 'w')
    lines = []
    
    def show(msg):
        lines.append(msg)
        if len(lines) > 10:
            lines.pop(0)
        for i, l in enumerate(lines):
            try: stdscr.addstr(i, 0, l.ljust(79))
            except: pass
        stdscr.refresh()
    
    show("Press any key. 'q' to quit.")
    show("Testing getch(), /dev/tty, stdin...")
    
    while True:
        # Method 1: curses getch
        k = stdscr.getch()
        if k != -1:
            msg = f"getch: {k} 0x{k:02x} '{chr(k) if 32<=k<127 else '?'}'"
            show(msg)
            log.write(msg + "\n"); log.flush()
            if k in (ord('q'), ord('Q')):
                break
        
        # Method 2: /dev/tty
        try:
            fd = os.open('/dev/tty', os.O_RDONLY | os.O_NONBLOCK)
            r, _, _ = select.select([fd], [], [], 0)
            if r:
                ch = os.read(fd, 1)
                if ch:
                    c = ch[0]
                    msg = f"tty:  {c} 0x{c:02x} '{chr(c) if 32<=c<127 else '?'}'"
                    show(msg)
                    log.write(msg + "\n"); log.flush()
                    if c in (ord('q'), ord('Q')):
                        os.close(fd)
                        break
            os.close(fd)
        except Exception as e:
            show(f"tty err: {e}")
        
        # Method 3: stdin
        try:
            r, _, _ = select.select([sys.stdin], [], [], 0)
            if r:
                ch = os.read(sys.stdin.fileno(), 1)
                if ch:
                    c = ch[0]
                    msg = f"stdin:{c} 0x{c:02x} '{chr(c) if 32<=c<127 else '?'}'"
                    show(msg)
                    log.write(msg + "\n"); log.flush()
                    if c in (ord('q'), ord('Q')):
                        break
        except Exception as e:
            show(f"stdin err: {e}")
    
    log.close()

curses.wrapper(main)
print("Done. Log at /tmp/key_test.log")
