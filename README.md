
# AI Agent in QGIS [![QGIS](https://img.shields.io/badge/QGIS-3.28+-93b023?style=flat-square&logo=qgis&logoColor=white)](https://qgis.org) [![Windows](https://img.shields.io/badge/Windows-0078D6?style=flat-square&logo=windows&logoColor=white)]() [![macOS](https://img.shields.io/badge/macOS-000000?style=flat-square&logo=apple&logoColor=white)]() [![Linux](https://img.shields.io/badge/Linux-FCC624?style=flat-square&logo=linux&logoColor=black)]()

## Your AI agent for QGIS. Hand it the work and it does it in your open project
### Follow this tutorial/documentation to use the plugin :
 https://terra-lab.ai/ai-agent
---


<img src="https://terra-lab.ai/images/ai-agent/ai-agent-hero.webp" alt="Demo" width="700"/>

---

## Data & privacy

Every message goes to TerraLab's hosted service at `agent.terra-lab.ai`, in the
European Union. There is no offline mode and no local model. Answers come from an
AI model, so read what it proposes: nothing that changes your data or writes a
file runs without your approval, at the permission level you chose.

**What is sent**: your request; a description of the open project (its CRS,
the canvas extent, and per layer the name, type, CRS, feature count, field names
and extent); the attachments you add; and what you wrote in the agent's Settings
fields. Leave those empty and nothing of the kind is sent.

**What is not sent**: the contents of your raster and vector files, your project
file itself, feature geometries and whole attribute tables. Your activation key
never appears in a tool result: every result, error and traceback passes a
scrubber that also removes database passwords from layer URIs and API keys from
URLs.

Usage counting carries no prompt text, layer name, file path or coordinate, and
switching it off in *Account Settings* stops it on your machine **and** on our
server. See our [Privacy Policy](https://terra-lab.ai/privacy-policy) and
[SECURITY.md](SECURITY.md) for the threat model.

---

## What is open and what is not

This repository is the **plugin**: everything that runs on your computer, under
GPL-2.0-or-later, so what it can do to your machine is auditable in full. The
hosted service is not in this tree: the model's system prompt, the tool
descriptions it reads, and the open data catalogue.
