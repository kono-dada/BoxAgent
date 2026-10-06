# BoxAgent Skills

`builtin/` contains read-only Skills shipped with BoxAgent. User-created Skills
are stored under `{BOXAGENT_DATA_DIR}/skills` and are not committed to Git.

Each Skill is a directory containing a `SKILL.md` manifest. BoxAgent owns the
catalog and enabled state; Runtime adapters only project that state into the
selected Agent Runtime.
