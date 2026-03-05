# Environment
- you are running inside a chroot workspace
- your user is `worker` with passwordless sudo
- repos are in ~/OTC.Relay, ~/OTC.SDK.Server, etc.
- root repos are bind-mounted at ~/dev/root/<repo>
- USB devices are available via /dev

# git
- do not co-author commits
- do NOT sign commits (no GPG key in chroot)
- commit as: Karl Ruskowski <karl.ruskowski@optris.de>

# style
- stick to google style guide
- 80 character line limit
- 2 space indents
- dont do double newline as demanded per pep 8, while
  writing python

# Building
- use cmake presets: `cmake --preset default`
- then: `cmake --build build --parallel`
- do NOT install dependencies manually — FetchContent
  handles C++ deps, system packages are pre-installed
- if a build tool is missing, report it — do not try
  to pip install or download alternatives

# Testing
- run tests: `ctest --output-on-failure -j --test-dir build/tests`
- always test your changes before committing
