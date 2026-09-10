# Security

AI Agent runs an AI model's decisions inside QGIS, on your machine, with your
permissions. This page says how to report a hole in that boundary and what the
plugin refuses to do. What leaves the machine is in the
[privacy policy](https://terra-lab.ai/privacy-policy).

## Reporting a vulnerability

Email **security@terra-lab.ai** with the version (`metadata.txt` `version=`),
your QGIS version and OS, and what an attacker gets. A proof of concept helps; a
full exploit is not required. We answer within 3 working days and assess within
10. Please do not open a public issue before a fix ships: the tracker is public
and installs update slowly through the QGIS plugin repository.

**In scope**: the code in `src/`, the wire protocol, the permission model, the
file and network guards, and anything that leaks the activation key.

**Out of scope**: the model being wrong (that is a quality bug, open an issue);
attacks that need the user to be running attacker-controlled code already; and
hardening against a hostile sibling plugin, since QGIS plugins share one Python
process by design.

Only the latest release on the QGIS plugin repository receives fixes.

## What holds, and where to check it

The model chooses a tool and its arguments. It never chooses what that tool is
allowed to do: the plugin assigns every tool its own danger level from its own
table, and the guards fail closed.

| What holds | Where to read it |
|---|---|
| The client decides the danger level, not the model | `src/tools/danger.py`, `src/core/executor.py` |
| Ask mode refuses every write at the executor | `src/core/executor.py` |
| Some tools ask at every level, whatever you approved before | `src/tools/guards.py` `ALWAYS_CONFIRM` |
| Writes stay inside an allow-list of paths | `src/core/security.py` `validate_path` |
| Fetches pass one URL guard, redirects re-checked | `src/core/net.py` `check_url` |
| Your activation key never reaches the model or a result | `src/core/log_scrub.py` `scrub_result` |
| Fetched text is labelled as data, never as instructions | `src/core/serialization.py` `_untrusted_text` |
| Every run can be undone | `src/core/snapshot.py`, `src/core/checkpoints.py` |
| A link in an answer cannot launch a program | `src/ui/file_links.py` `SAFE_TO_OPEN` |

If one of them does not hold, that is a bug and we want the email.

## What we do not claim

- **This is not a sandbox.** A QGIS plugin runs with your user's rights. Approve
  `execute_code` and you approved arbitrary Python. In-process filtering is what
  we have; process isolation would be a sandbox.
- **No LLM is immune to prompt injection.** We label untrusted text, keep the
  danger level out of the model's reach, and put a human in front of destructive
  actions. That is defence in depth, not a proof.
- **No SOC 2, no ISO 27001, no external penetration test** today. If that
  changes it will say so here, with a date.
