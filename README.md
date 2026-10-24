# AI Agent in QGIS [![QGIS](https://img.shields.io/badge/QGIS-3.28+-93b023?style=flat-square&logo=qgis&logoColor=white)](https://qgis.org) [![Windows](https://img.shields.io/badge/Windows-0078D6?style=flat-square&logo=windows&logoColor=white)]() [![macOS](https://img.shields.io/badge/macOS-000000?style=flat-square&logo=apple&logoColor=white)]() [![Linux](https://img.shields.io/badge/Linux-FCC624?style=flat-square&logo=linux&logoColor=black)]() [![License: GPL v2](https://img.shields.io/badge/License-GPLv2-blue.svg?style=flat-square)](LICENSE)

## Your AI agent for QGIS: hand it the work and it does it in your open project

Ask for anything in plain English and it finds the data, runs the analysis and
builds the map. No code, and one click undoes anything it changed.

<img src="https://terra-lab.ai/images/ai-agent/ai-agent-hero.webp" alt="AI Agent carrying out a written task in a QGIS project" width="700"/>

## What it does

AI Agent by TerraLab is an AI agent for QGIS. You describe the job in plain English and it works on your own layers, in the project you already have open, showing each step as it goes.

- **Finds the data**: 73 open data connectors (OpenStreetMap, Copernicus, NASA, USGS, IGN, Overture, Planetary Computer and 66 more) and a catalog of 843 ready datasets from 174 countries and from EU-wide and global sources, each with its licence.
- **Runs the analysis**: 253 tools to load and inspect data, run Processing algorithms and edit geometry.
- **Styles and lays out the map**: symbology, labels and print layouts.
- **You stay in control**: you see every step before it runs, changes wait for your approval, and one click undoes anything it changed.
- **Fits your habits**: one conversation per project. Attach files, layers, selections or the map view to a message.
- **Works with** [AI Edit](https://github.com/TerraLabAI/QGIS_AI-Edit) and [AI Segmentation](https://github.com/TerraLabAI/QGIS_AI-Segmentation) when they are installed.
- **Free tier with no card.** Pro starts with a 7-day free trial. Plans and limits: https://terra-lab.ai/pricing
- **Platforms**: QGIS 3.28 to QGIS 4, on Windows, macOS and Linux. 12 languages.

Answers come from an AI model and can be wrong. Review what it changes.

## Benchmarks

On GeoKTools and GeoExpertQueries, the geospatial benchmarks Google Research published in October 2026, AI Agent scored **43.8 out of 100** on 41 held-out public questions at Low effort, the setting on the free plan. In the same runs, Claude Sonnet 5.5 with the nkarasiak QGIS MCP server in Claude Code scored 24.9, and the jjsantos01 QGIS MCP server in Codex scored 22.1. Runs from 16 to 20 October 2026, graded by Google's own scoring code, mean of 3 runs. Google keeps its official test questions private, so this is our run on the public questions, not a Google leaderboard.

It also passed 62.8% of 160 [GeoBenchX](https://github.com/Solirinai/GeoBenchX) tasks and scored 61.1 out of 100 on [GS-QA2](https://huggingface.co/datasets/Zoe/GS-QA2). Method, caveats and every score: [AI Agent QGIS plugin: the geospatial benchmark](https://terra-lab.ai/blog/ai-agent-qgis-plugin-geospatial-benchmark).

## A QGIS MCP alternative

QGIS MCP connects an AI app outside QGIS to your project through a local server: you install the client, `uv`, the MCP server and the plugin, then start the server each session. AI Agent is a QGIS MCP alternative that runs inside QGIS with the model included, so there is nothing else to install. On the same task, timed (schools within 300 m of a tram stop in Lyon), QGIS MCP with Claude Code took 9 min 28 s and AI Agent 5 min 35 s, using the city's official school layers ([setup, limits and four timed tasks](https://terra-lab.ai/blog/qgis-mcp)).

## Install

In QGIS, open *Plugins > Manage and Install Plugins*, search "AI Agent", install. The free tier needs no card, and Pro starts with a 7-day free trial.

## Links

- Page, tutorial and plans: https://terra-lab.ai/ai-agent
- QGIS plugin repository: https://plugins.qgis.org/plugins/AI_Agent/
- [AI Agent for QGIS, the complete guide](https://terra-lab.ai/blog/ai-agent-complete-guide)
- [AI Agents in QGIS: what they are and which to use](https://terra-lab.ai/blog/what-is-an-ai-agent-in-qgis)
- [ChatGPT for QGIS: what it can and can't do, tested](https://terra-lab.ai/blog/chatgpt-for-qgis)
- [QGIS MCP: setup, limits, and one task timed](https://terra-lab.ai/blog/qgis-mcp)
- Bugs and requests: https://github.com/TerraLabAI/QGIS_AI-Agent/issues

License: GPL-2.0-or-later. Made by [TerraLab](https://terra-lab.ai), terra-lab.ai.

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

That service is not licensed for reuse, and text and data mining rights on it
are reserved (EU Directive 2019/790, Art. 4). See our
[Terms of Use](https://terra-lab.ai/terms-of-use). The TerraLab names and logos
are not covered by the GPL, see [TRADEMARKS.md](TRADEMARKS.md).
