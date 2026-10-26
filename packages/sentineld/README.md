# sentineld

The workspace daemon: VMs on macOS, one per workspace. The Sentinel app runs it, but it also runs on
its own.

```sh
make                      # build/: bin/sentineld (also each VM, as `sentineld vm`), lib/, share/,
                          # distributions/, tools/ (the first build compiles the VM's libraries)

build/bin/sentineld --state DIR [--cache DIR] [--distributions DIR]...
```

- `--state`: its catalog, logs and socket (`DIR/sentineld.sock`). One sentineld per state folder.
- `--cache`: base disks, layers and downloads (default: `~/Library/Caches/Sentinel/<hash>`).
- `--distributions`: a folder of distribution folders, any number of times (default: the ones it
  ships, `build/distributions`).

Talk to it with the CLI:

```sh
build/bin/sentineld ctl --state DIR catalog
build/bin/sentineld ctl --state DIR configure '{"workspace":"w1","revision":0,"spec":{...}}'
build/bin/sentineld ctl --state DIR start '{"workspace":"w1","revision":1}'
build/bin/sentineld ctl --state DIR list '{"watch":true}'
```

`sentineld view --state DIR WORKSPACE` opens a workspace's desktop in a window (video, sound, input,
clipboard). `sentineld --help` explains the rest: `sentineld ctl --help` lists every operation with its fields
and an example, and `sentineld help distributions` how to write and test a distribution folder. The
texts are `core/help/*.txt`, built into the binary; the design behind them is DESIGN.md §4–5 and §7–8.
