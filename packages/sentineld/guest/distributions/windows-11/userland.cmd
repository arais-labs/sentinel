@echo off
rem The POSIX userland Sentinel's shell commands, terminals and tools run in (DESIGN.md §9): MSYS2 from
rem the share (windows\msys2: its base and the packages guest/msys2/sources.txt pins), installed into
rem C:\msys64 by its own pacman, offline, once per layer (distribution.json "userland"): workspaces start
rem with it.
setlocal
set "from=%SENTINEL_SHARE%\windows\msys2"
set "root=C:\msys64"
rem Windows' own tar: MSYS2's reads "C:" as a remote host.
for %%f in ("%from%\msys2-base-*.tar.xz") do "%SystemRoot%\System32\tar.exe" -xf "%%f" -C C:\ || exit /b 1
rem Its first login sets it up (the pacman keyring), then the packages from the share.
"%root%\usr\bin\bash.exe" -lc "pacman -U --noconfirm $(cygpath -u $SENTINEL_SHARE)/windows/msys2/*.pkg.tar.zst" || exit /b 1
rem The tmux in front of MSYS2's (this folder's tmux says why).
mkdir "%root%\usr\local\bin" 2>nul
copy /y "%SENTINEL_SHARE%\distribution\tmux" "%root%\usr\local\bin\tmux" >nul
