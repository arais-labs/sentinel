"""Invoke the desktop runtime installed by the workspace's owning worker."""

import os
import sys

os.execv("/usr/bin/python3", ["python3", "/opt/sentinel/desktop/desktop-session.py", sys.argv[1]])
