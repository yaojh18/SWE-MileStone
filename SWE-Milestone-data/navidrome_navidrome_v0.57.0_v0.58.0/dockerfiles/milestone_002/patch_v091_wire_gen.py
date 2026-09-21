#!/usr/bin/env python3
"""[ENV-PATCH-v0.91] milestone_002: stitch cmd/wire_gen.go to milestone vintage.

wire_gen.go in the v0.9 tag is future-vintage vs this milestone's
core/scanner/nativeapi: it references scanner.GetWatcher, core.NewLibrary,
5-arg nativeapi.New and wire.Bind(core.Scanner/Watcher), none of which exist
here. That breaks the non-test cmd package -> `go build .` gate fails ->
the evaluator silently grades the START tag instead of END.

Touches NO test file; the classification oracle is unchanged.
"""
import re

src = open("cmd/wire_gen.go").read()

# 1) CreateNativeAPIRouter: replace the whole function with milestone-vintage
#    wiring (nativeapi.New is 4-arg; dropping library/watcher un-uses the rest
#    of the future-vintage body, so a full-body replace is the minimal stitch).
new_fn = """func CreateNativeAPIRouter() *nativeapi.Router {
\tsqlDB := db.Db()
\tdataStore := persistence.New(sqlDB)
\tshare := core.NewShare(dataStore)
\tplaylists := core.NewPlaylists(dataStore)
\tinsights := metrics.GetInstance(dataStore)
\trouter := nativeapi.New(dataStore, share, playlists, insights)
\treturn router
}"""
pat = re.compile(r"func CreateNativeAPIRouter\(ctx context\.Context\) \*nativeapi\.Router \{.*?\n\}", re.S)
src, n = pat.subn(new_fn, src)
assert n == 1, f"CreateNativeAPIRouter replace count: {n}"

# 2) scanner.GetWatcher -> scanner.NewWatcher (this vintage's constructor)
src, n = re.subn(r"scanner\.GetWatcher", "scanner.NewWatcher", src)
assert n >= 1, "scanner.GetWatcher not found"

# 3) allProviders: drop wire.Bind entries for core.Scanner/core.Watcher (absent types)
src, n = re.subn(r", wire\.Bind\(new\(core\.Scanner\), new\(scanner\.Scanner\)\)", "", src)
assert n == 1, f"core.Scanner bind count: {n}"
src, n = re.subn(r", wire\.Bind\(new\(core\.Watcher\), new\(scanner\.Watcher\)\)", "", src)
assert n == 1, f"core.Watcher bind count: {n}"

open("cmd/wire_gen.go", "w").write(src)

# 4) root.go already calls CreateNativeAPIRouter() — matches the new signature.
root = open("cmd/root.go").read()
assert "CreateNativeAPIRouter()" in root, "root.go call shape changed unexpectedly"
print("[ENV-PATCH-v0.91] wire_gen stitches applied")
